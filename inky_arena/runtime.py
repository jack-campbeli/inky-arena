from __future__ import annotations

import logging
import os
import random
import signal
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

from PIL import Image

from inky_arena.arena_client import ArenaClient, CandidateFetchResult
from inky_arena.config import AppConfig
from inky_arena.models import AppState, DisplayCandidate
from inky_arena.render import render_candidate, render_status
from inky_arena.state import load_state, save_state


LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class RefreshTimeout(BaseException):
    """Raised from SIGALRM when one refresh cycle appears wedged."""


class DisplayPublishError(RuntimeError):
    """Raised when installed Inky hardware cannot publish a frame."""


def main() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    config = AppConfig.load()
    run_forever(config)


def run_forever(config: AppConfig) -> None:
    client = ArenaClient(config)
    state = load_state(config.state_path)
    rng = random.Random()

    notify_systemd("READY=1\nSTATUS=Inky Arena started")
    while True:
        try:
            notify_systemd("WATCHDOG=1\nSTATUS=Refreshing Are.na display")
            with refresh_deadline(config.refresh_timeout_seconds):
                state = refresh_once(config, client, state, rng=rng)
        except RefreshTimeout as exc:
            logging.error("%s", exc)
            notify_systemd(f"STATUS={exc}")
            raise SystemExit(1) from exc
        except DisplayPublishError as exc:
            logging.error("%s", exc)
            notify_systemd(f"STATUS={exc}")
            raise SystemExit(1) from exc
        except Exception:  # noqa: BLE001
            logging.exception("Refresh cycle failed")

        sleep_seconds = seconds_until_next_refresh(config.refresh_minutes)
        logging.info("Sleeping for %.0f seconds", sleep_seconds)
        sleep_with_watchdog(sleep_seconds)


@contextmanager
def refresh_deadline(timeout_seconds: float):
    if timeout_seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _raise_timeout(signum: int, frame: object) -> None:
        raise RefreshTimeout(f"Refresh cycle exceeded {timeout_seconds:.0f} seconds; exiting for service restart")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _raise_timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def sleep_with_watchdog(sleep_seconds: float) -> None:
    remaining = max(0.0, sleep_seconds)
    watchdog_interval = systemd_watchdog_interval_seconds()
    chunk_seconds = remaining
    if watchdog_interval is not None:
        chunk_seconds = max(1.0, min(remaining, watchdog_interval / 2))

    deadline = time.monotonic() + remaining
    while remaining > 0:
        notify_systemd("WATCHDOG=1\nSTATUS=Sleeping until next refresh")
        time.sleep(min(chunk_seconds, remaining))
        remaining = deadline - time.monotonic()


def systemd_watchdog_interval_seconds() -> float | None:
    watchdog_usec = os.getenv("WATCHDOG_USEC")
    if not watchdog_usec:
        return None
    try:
        interval = int(watchdog_usec) / 1_000_000
    except ValueError:
        return None
    return interval if interval > 0 else None


def notify_systemd(message: str) -> bool:
    notify_socket = os.getenv("NOTIFY_SOCKET")
    if not notify_socket:
        return False

    address: str | bytes = notify_socket
    if notify_socket.startswith("@"):
        address = b"\0" + notify_socket[1:].encode("utf-8")

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.connect(address)
            client.sendall(message.encode("utf-8"))
        return True
    except OSError as exc:
        logging.debug("Unable to notify systemd: %s", exc)
        return False


def refresh_once(
    config: AppConfig,
    client: ArenaClient,
    state: AppState,
    rng: random.Random | None = None,
) -> AppState:
    rng = rng or random.Random()

    logging.info("Starting refresh cycle")
    sync_before = state.last_sync_iso
    try:
        candidates = _load_candidates(config, client, state)
    except Exception as exc:
        state.last_error = str(exc)
        publish_image(
            render_status(
                config,
                "Are.na sync failed",
                "Rate limited or unable to sync.\nUsing cached content is preferred when available.\nCheck your token, network, and sync interval.",
            ),
            config,
        )
        save_state(config.state_path, state)
        raise

    if not candidates:
        publish_image(
            render_status(
                config,
                "No visual blocks found",
                "The configured Are.na channels did not return any previewable image, embed, link, or attachment blocks.",
            ),
            config,
        )
        state.last_candidate_ids = []
        state.queue_ids = []
        save_state(config.state_path, state)
        return state

    if state.last_sync_iso != sync_before:
        _discover_channels(config, client, state, candidates, rng)

    state = _prepare_queue(state, candidates, rng)
    if not state.queue_ids:
        logging.info("Current candidate pool is exhausted; checking live sync eligibility before restarting rotation")
        candidates = _load_candidates(config, client, state, force_refresh=True)
        if not candidates:
            publish_image(
                render_status(
                    config,
                    "No visual blocks found",
                    "The configured Are.na channels did not return any previewable image, embed, link, or attachment blocks.",
                ),
                config,
            )
            state.last_candidate_ids = []
            state.queue_ids = []
            save_state(config.state_path, state)
            return state

        state = _prepare_queue(state, candidates, rng)
        if not state.queue_ids:
            logging.info("Full rotation complete; resetting shown history for a new cycle")
            state.shown_ids = []
            state = _prepare_queue(state, candidates, rng)
            if not state.queue_ids:
                save_state(config.state_path, state)
                return state

    candidate_map = {candidate.id: candidate for candidate in candidates}
    if _try_display_queue(config, client, state, candidate_map, candidates):
        logging.info("Refresh cycle completed")
        return state

    logging.info("Rebuilding queue after exhausting current batch without a renderable block")
    state = _prepare_retry_queue(state, candidates, rng)
    if _try_display_queue(config, client, state, candidate_map, candidates):
        logging.info("Refresh cycle completed after queue rebuild")
        return state

    publish_image(
        render_status(
            config,
            "No renderable blocks",
            "The current block queue could not be rendered. The next refresh will try to rebuild the rotation.",
        ),
        config,
    )
    save_state(config.state_path, state)
    return state


def _prepare_queue(state: AppState, candidates: list[DisplayCandidate], rng: random.Random) -> AppState:
    candidate_ids = _sync_candidate_pool_state(state, candidates)
    shown_ids = set(state.shown_ids)
    state.queue_ids = [qid for qid in state.queue_ids if qid not in shown_ids]
    if state.queue_ids:
        return state
    unseen = [candidate_id for candidate_id in candidate_ids if candidate_id not in shown_ids]
    if not unseen:
        return state

    rng.shuffle(unseen)
    state.queue_ids = unseen
    return state


def _prepare_retry_queue(state: AppState, candidates: list[DisplayCandidate], rng: random.Random) -> AppState:
    candidate_ids = _sync_candidate_pool_state(state, candidates)
    if state.queue_ids:
        return state

    retry_ids = list(candidate_ids)
    if not retry_ids:
        return state

    rng.shuffle(retry_ids)
    state.queue_ids = retry_ids
    return state


def _sync_candidate_pool_state(state: AppState, candidates: list[DisplayCandidate]) -> list[str]:
    candidate_ids = [candidate.id for candidate in candidates]
    pool_changed = candidate_ids != state.last_candidate_ids

    valid_queue = [candidate_id for candidate_id in state.queue_ids if candidate_id in candidate_ids]
    state.queue_ids = valid_queue

    if pool_changed:
        state.last_candidate_ids = candidate_ids
        state.shown_ids = [candidate_id for candidate_id in state.shown_ids if candidate_id in candidate_ids]

    return candidate_ids


def _discover_channels(
    config: AppConfig,
    client: ArenaClient,
    state: AppState,
    candidates: list[DisplayCandidate],
    rng: random.Random,
) -> None:
    if len(state.discovered_channels) >= config.walker_max_discovered_channels:
        return
    if not candidates:
        return

    seed_set = set(config.channel_slugs)
    known = seed_set | set(state.discovered_channels)
    sample = rng.sample(candidates, min(config.walker_discovery_samples, len(candidates)))

    for candidate in sample:
        if len(state.discovered_channels) >= config.walker_max_discovered_channels:
            break
        connected_slugs = client.fetch_block_connections(candidate.id)
        for slug in connected_slugs:
            if slug in known:
                continue
            if len(state.discovered_channels) >= config.walker_max_discovered_channels:
                break
            logging.info("Walker discovered new channel: %s (via block %s)", slug, candidate.id)
            state.discovered_channels.append(slug)
            known.add(slug)


def _load_candidates(
    config: AppConfig,
    client: ArenaClient,
    state: AppState,
    force_refresh: bool = False,
) -> list[DisplayCandidate]:
    effective_slugs = config.channel_slugs + state.discovered_channels

    if force_refresh and state.cached_candidates and _rate_limit_backoff_active(state):
        logging.info("Using cached candidate pool during API backoff")
        return state.cached_candidates

    if not force_refresh and _should_use_cached_candidates(config, state):
        logging.info("Using cached candidate pool")
        return state.cached_candidates

    try:
        logging.info("Syncing %d Are.na channels", len(effective_slugs))
        result = client.fetch_candidates_with_metadata(channel_slugs=effective_slugs)
        candidates = result.candidates
        state.next_sync_not_before_iso = result.next_sync_not_before_iso

        seed_set = set(config.channel_slugs)
        for slug in result.channel_failures:
            if slug not in seed_set:
                state.channel_failure_counts[slug] = state.channel_failure_counts.get(slug, 0) + 1
        state.discovered_channels = [
            slug for slug in state.discovered_channels
            if state.channel_failure_counts.get(slug, 0) < config.walker_channel_failure_limit
        ]

        if result.errors:
            state.last_error = "; ".join(result.errors)

        if not candidates:
            if state.cached_candidates:
                logging.warning("Are.na sync returned no candidates, reusing cached pool")
                save_state(config.state_path, state)
                return state.cached_candidates
            raise RuntimeError(state.last_error or "No channels returned usable candidates")

        state.cached_candidates = candidates
        state.last_sync_iso = datetime.now().astimezone().isoformat()
        if not result.errors:
            state.last_error = None
        save_state(config.state_path, state)
        logging.info("Synced %d display candidates", len(candidates))
        return candidates
    except Exception as exc:
        if state.cached_candidates:
            state.last_error = str(exc)
            logging.warning("Are.na sync failed, reusing cached candidates: %s", exc)
            save_state(config.state_path, state)
            return state.cached_candidates
        raise


def _rate_limit_backoff_active(state: AppState, now: datetime | None = None) -> bool:
    if not state.next_sync_not_before_iso:
        return False

    try:
        next_allowed_sync = datetime.fromisoformat(state.next_sync_not_before_iso)
    except ValueError:
        logging.warning("Ignoring invalid next_sync_not_before_iso: %r", state.next_sync_not_before_iso)
        return False

    if next_allowed_sync.tzinfo is None:
        logging.warning("Ignoring invalid next_sync_not_before_iso without timezone: %r", state.next_sync_not_before_iso)
        return False

    current = now or datetime.now().astimezone()
    return current < next_allowed_sync


def _should_use_cached_candidates(config: AppConfig, state: AppState) -> bool:
    if not state.cached_candidates:
        return False

    now = datetime.now().astimezone()
    if _rate_limit_backoff_active(state, now=now):
        return True

    if not state.last_sync_iso:
        return False

    try:
        last_sync = datetime.fromisoformat(state.last_sync_iso)
    except ValueError:
        return False

    age_seconds = (now - last_sync).total_seconds()
    return age_seconds < config.sync_minutes * 60


def _load_inky_display() -> object | None:
    try:
        from inky.auto import auto
    except ImportError:
        return None
    return auto()


def publish_image(image: Image.Image, config: AppConfig) -> None:
    try:
        display = _load_inky_display()
    except Exception as exc:  # noqa: BLE001
        _save_failed_publish_preview(image, config, exc)
        raise DisplayPublishError(f"Inky display discovery failed: {exc}") from exc

    if display is None:
        _save_preview(image, config, "Inky library unavailable")
        return

    try:
        logging.info("Publishing image to Inky display")
        output = image
        if config.display_orientation == "portrait":
            output = image.rotate(90, expand=True)
        resized = output.resize((display.WIDTH, display.HEIGHT))
        display.set_image(resized)
        logging.info("Starting Inky display refresh")
        display.show()
        logging.info("Finished Inky display refresh")
    except Exception as exc:  # noqa: BLE001
        _save_failed_publish_preview(image, config, exc)
        raise DisplayPublishError(f"Inky display publish failed: {exc}") from exc


def _save_failed_publish_preview(image: Image.Image, config: AppConfig, hardware_error: Exception) -> None:
    try:
        _save_preview(image, config, f"Inky hardware unavailable ({hardware_error})")
    except Exception:  # noqa: BLE001
        logging.exception("Unable to save diagnostic preview after Inky hardware failure")


def _save_preview(image: Image.Image, config: AppConfig, reason: str) -> None:
    config.preview_output.parent.mkdir(parents=True, exist_ok=True)
    image.save(config.preview_output)
    logging.warning("%s, saved preview to %s", reason, config.preview_output)


def seconds_until_next_refresh(refresh_minutes: int, now: datetime | None = None) -> float:
    now = now or datetime.now().astimezone()
    minutes = max(1, refresh_minutes)
    bucket = (now.minute // minutes + 1) * minutes
    next_tick = now.replace(second=0, microsecond=0)
    if bucket >= 60:
        next_tick = (next_tick + timedelta(hours=1)).replace(minute=0)
    else:
        next_tick = next_tick.replace(minute=bucket)
    return max(5.0, (next_tick - now).total_seconds())


def _append_unique(values: list[str], new_value: str, limit: int) -> list[str]:
    merged = [value for value in values if value != new_value]
    merged.append(new_value)
    return merged[-limit:]


def _try_display_queue(
    config: AppConfig,
    client: ArenaClient,
    state: AppState,
    candidate_map: dict[str, DisplayCandidate],
    candidates: list[DisplayCandidate],
) -> bool:
    shown_limit = max(200, len(candidates) * 4)

    while state.queue_ids:
        next_id = state.queue_ids.pop(0)
        candidate = candidate_map.get(next_id)
        if candidate is None:
            continue
        try:
            logging.info("Fetching image for block %s", candidate.id)
            image_result = client.fetch_image_bytes(candidate.image_url)
            degraded = state.last_error is not None
            logging.info("Rendering block %s", candidate.id)
            image = render_candidate(config, candidate, image_result.payload, degraded=degraded)
            publish_image(image, config)
            state.last_displayed_id = candidate.id
            state.shown_ids = _append_unique(state.shown_ids, candidate.id, limit=shown_limit)
            save_state(config.state_path, state)
            logging.info("Displayed block %s from %s", candidate.id, candidate.channel_slug)
            return True
        except DisplayPublishError:
            state.queue_ids.insert(0, next_id)
            raise
        except Exception as exc:  # noqa: BLE001
            state.shown_ids = _append_unique(state.shown_ids, candidate.id, limit=shown_limit)
            logging.warning("Skipping block %s after image/render failure: %s", candidate.id, exc)

    return False
