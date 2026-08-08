from __future__ import annotations

import hashlib
import logging
import os
import queue
import random
import signal
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from PIL import Image

from inky_arena.arena_client import ArenaClient, CandidateFetchResult
from inky_arena.buttons import ButtonAction, ButtonWatcher
from inky_arena.config import AppConfig
from inky_arena.models import DISPLAY_MODE_COLLAGE, DISPLAY_MODE_SINGLE, AppState, DisplayCandidate
from inky_arena.render import decode_source_image, render_candidate, render_collage, render_status
from inky_arena.state import load_state, save_state
from inky_arena.vocabulary import VocabularyEntry, entry_for_datetime, period_for_datetime


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
    button_wake_event = threading.Event()
    button_actions: queue.Queue[ButtonAction] = queue.Queue()

    def handle_button_action(action: ButtonAction) -> None:
        button_actions.put(action)
        button_wake_event.set()

    button_watcher = ButtonWatcher(handle_button_action)
    button_watcher.start()

    notify_systemd("READY=1\nSTATUS=Inky Arena started")
    try:
        while True:
            try:
                with refresh_deadline(config.refresh_timeout_seconds):
                    button_action = _take_button_action(button_actions, button_wake_event)
                    if button_action is not None:
                        notify_systemd(f"WATCHDOG=1\nSTATUS={_button_action_status(button_action)}")
                        state = process_button_action(config, client, state, button_action, rng=rng)
                    else:
                        notify_systemd("WATCHDOG=1\nSTATUS=Refreshing Are.na display")
                        state = refresh_once(config, client, state, rng=rng)
                        if vocabulary_refresh_due(state):
                            state = refresh_current_candidate(config, client, state)
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
            sleep_with_watchdog(sleep_seconds, wake_event=button_wake_event)
    finally:
        button_watcher.close()


def _take_button_action(
    actions: queue.Queue[ButtonAction],
    wake_event: threading.Event,
) -> ButtonAction | None:
    try:
        action = actions.get_nowait()
    except queue.Empty:
        wake_event.clear()
        try:
            action = actions.get_nowait()
        except queue.Empty:
            return None

    # Clear after consuming so an action enqueued during this function cannot
    # leave a stale wake flag behind. Re-checking the queue prevents a clear
    # from hiding a newly enqueued action.
    wake_event.clear()
    if not actions.empty():
        wake_event.set()
    return action


def _button_action_status(action: ButtonAction) -> str:
    if action is ButtonAction.A_SHORT:
        return "Advancing display content"
    if action is ButtonAction.A_LONG:
        return "Switching display mode"
    return "Updating vocabulary overlay"


def process_button_action(
    config: AppConfig,
    client: ArenaClient,
    state: AppState,
    action: ButtonAction,
    rng: random.Random | None = None,
) -> AppState:
    if action is ButtonAction.A_SHORT:
        return refresh_once(config, client, state, rng=rng, force_sync=True)
    if action is ButtonAction.A_LONG:
        state = switch_display_mode(config, state)
        return refresh_once(config, client, state, rng=rng, force_sync=True)
    if action is ButtonAction.B_TOGGLE_VOCABULARY:
        return toggle_vocabulary(config, client, state)
    raise ValueError(f"Unsupported button action: {action}")


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


def sleep_with_watchdog(sleep_seconds: float, wake_event: threading.Event | None = None) -> bool:
    remaining = max(0.0, sleep_seconds)
    watchdog_interval = systemd_watchdog_interval_seconds()
    chunk_seconds = remaining
    if watchdog_interval is not None:
        chunk_seconds = max(1.0, min(remaining, watchdog_interval / 2))

    deadline = time.monotonic() + remaining
    while remaining > 0:
        notify_systemd("WATCHDOG=1\nSTATUS=Sleeping until next refresh")
        wait_seconds = min(chunk_seconds, remaining)
        if wake_event is not None:
            if wake_event.wait(wait_seconds):
                return True
        else:
            time.sleep(wait_seconds)
        remaining = deadline - time.monotonic()
    return False


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


def _current_vocabulary(
    state: AppState,
    now: datetime | None = None,
) -> tuple[VocabularyEntry | None, str | None]:
    if not state.vocabulary_enabled:
        return None, None
    moment = now or datetime.now().astimezone()
    return entry_for_datetime(moment), period_for_datetime(moment)


def vocabulary_refresh_due(state: AppState, now: datetime | None = None) -> bool:
    if not state.vocabulary_enabled or state.last_displayed_id is None:
        return False
    _, current_period = _current_vocabulary(state, now=now)
    return state.vocabulary_period != current_period


def switch_display_mode(config: AppConfig, state: AppState) -> AppState:
    state.display_mode = DISPLAY_MODE_SINGLE if state.display_mode == DISPLAY_MODE_COLLAGE else DISPLAY_MODE_COLLAGE
    save_state(config.state_path, state)
    logging.info("Display mode switched to %s", state.display_mode)
    return state


def toggle_vocabulary(config: AppConfig, client: ArenaClient, state: AppState) -> AppState:
    state.vocabulary_enabled = not state.vocabulary_enabled
    state.vocabulary_period = None
    save_state(config.state_path, state)
    logging.info("Vocabulary overlay %s", "enabled" if state.vocabulary_enabled else "disabled")
    return refresh_current_display(config, client, state)


def refresh_current_display(config: AppConfig, client: ArenaClient, state: AppState) -> AppState:
    displayed_ids = state.last_displayed_ids or ([state.last_displayed_id] if state.last_displayed_id else [])
    candidate_map = {candidate.id: candidate for candidate in state.cached_candidates}
    candidates = [candidate_map[candidate_id] for candidate_id in displayed_ids if candidate_id in candidate_map]
    if not candidates or len(candidates) != len(displayed_ids):
        logging.warning("Unable to redraw vocabulary overlay because the current display content is not cached")
        return state

    logging.info("Redrawing %d current image(s) for vocabulary overlay", len(candidates))
    image_payloads = [client.fetch_image_bytes(candidate.image_url).payload for candidate in candidates]
    vocabulary, period = _current_vocabulary(state)
    if state.display_mode == DISPLAY_MODE_COLLAGE:
        source_images = [decode_source_image(payload) for payload in image_payloads]
        image = render_collage(
            config,
            source_images,
            degraded=state.last_error is not None,
            vocabulary=vocabulary,
        )
    else:
        image = render_candidate(
            config,
            candidates[0],
            image_payloads[0],
            degraded=state.last_error is not None,
            vocabulary=vocabulary,
        )
    publish_image(image, config)
    state.vocabulary_period = period
    save_state(config.state_path, state)
    return state


def refresh_current_candidate(config: AppConfig, client: ArenaClient, state: AppState) -> AppState:
    """Backward-compatible name for callers that redraw the current display."""
    return refresh_current_display(config, client, state)


def refresh_once(
    config: AppConfig,
    client: ArenaClient,
    state: AppState,
    rng: random.Random | None = None,
    force_sync: bool = False,
) -> AppState:
    rng = rng or random.Random()

    logging.info("Starting refresh cycle")
    sync_before = state.last_sync_iso
    try:
        candidates = _load_candidates(config, client, state, force_sync=force_sync)
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
        logging.info(
            "No unseen images in the current %d-candidate batch; keeping the existing display",
            len(candidates),
        )
        save_state(config.state_path, state)
        return state

    candidate_map = {candidate.id: candidate for candidate in candidates}
    if _try_display_queue(config, client, state, candidate_map, candidates):
        logging.info("Refresh cycle completed")
        return state

    logging.info(
        "No unseen renderable images in the current %d-candidate batch; keeping the existing display",
        len(candidates),
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


def _sync_candidate_pool_state(state: AppState, candidates: list[DisplayCandidate]) -> list[str]:
    candidate_ids = [candidate.id for candidate in candidates]
    pool_changed = candidate_ids != state.last_candidate_ids

    valid_queue = [candidate_id for candidate_id in state.queue_ids if candidate_id in candidate_ids]
    state.queue_ids = valid_queue

    if pool_changed:
        state.last_candidate_ids = candidate_ids

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
    force_sync: bool = False,
) -> list[DisplayCandidate]:
    effective_slugs = config.channel_slugs + state.discovered_channels

    if _rate_limit_backoff_active(state):
        if state.cached_candidates:
            logging.info("Using cached candidate pool during API backoff")
            return state.cached_candidates
        raise RuntimeError(f"Are.na sync deferred until {state.next_sync_not_before_iso}")

    if _cached_pool_has_unseen_candidates(state):
        logging.info("Finishing the current cached candidate batch before syncing another page window")
        return state.cached_candidates

    if not force_sync and _should_use_cached_candidates(config, state):
        logging.info("Using cached candidate pool")
        return state.cached_candidates

    try:
        logging.info("Syncing %d Are.na channels", len(effective_slugs))
        result = client.fetch_candidates_with_metadata(
            channel_slugs=effective_slugs,
            channel_start_pages=state.channel_page_cursors,
        )
        candidates = result.candidates
        state.next_sync_not_before_iso = result.next_sync_not_before_iso
        state.channel_page_cursors = {
            slug: state.channel_page_cursors.get(slug, 1)
            for slug in effective_slugs
        }
        state.channel_page_cursors.update(result.channel_next_pages)

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

        sync_time = datetime.now().astimezone().isoformat()
        if not candidates:
            state.last_sync_iso = sync_time
            if state.cached_candidates:
                logging.info("Are.na page window returned no visual candidates, keeping the existing cached pool")
                save_state(config.state_path, state)
                return state.cached_candidates
            if result.errors:
                save_state(config.state_path, state)
                raise RuntimeError(state.last_error or "No channels returned usable candidates")
            save_state(config.state_path, state)
            return []

        state.cached_candidates = candidates
        state.last_sync_iso = sync_time
        if not result.errors:
            state.last_error = None
        save_state(config.state_path, state)
        logging.info(
            "Synced %d display candidates; next channel pages: %s",
            len(candidates),
            state.channel_page_cursors,
        )
        return candidates
    except Exception as exc:
        if state.cached_candidates:
            state.last_error = str(exc)
            logging.warning("Are.na sync failed, reusing cached candidates: %s", exc)
            save_state(config.state_path, state)
            return state.cached_candidates
        raise


def _cached_pool_has_unseen_candidates(state: AppState) -> bool:
    if not state.cached_candidates:
        return False
    shown_ids = set(state.shown_ids)
    return any(candidate.id not in shown_ids for candidate in state.cached_candidates)


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


def _append_unique(values: list[str], new_value: str) -> list[str]:
    merged = [value for value in values if value != new_value]
    merged.append(new_value)
    return merged


@dataclass(slots=True)
class _PreparedCollageTile:
    candidate: DisplayCandidate
    source_image: Image.Image
    image_digest: str


def _try_display_queue(
    config: AppConfig,
    client: ArenaClient,
    state: AppState,
    candidate_map: dict[str, DisplayCandidate],
    candidates: list[DisplayCandidate],
) -> bool:
    del candidates
    if state.display_mode == DISPLAY_MODE_COLLAGE:
        return _try_display_collage(config, client, state, candidate_map)
    return _try_display_single(config, client, state, candidate_map)


def _try_display_single(
    config: AppConfig,
    client: ArenaClient,
    state: AppState,
    candidate_map: dict[str, DisplayCandidate],
) -> bool:
    while state.queue_ids:
        next_id = state.queue_ids[0]
        candidate = candidate_map.get(next_id)
        if candidate is None:
            state.queue_ids.pop(0)
            continue
        try:
            logging.info("Fetching image for block %s", candidate.id)
            image_result = client.fetch_image_bytes(candidate.image_url)
            image_digest = hashlib.sha256(image_result.payload).hexdigest()
            if image_digest in state.displayed_image_digests:
                state.queue_ids.pop(0)
                state.shown_ids = _append_unique(state.shown_ids, candidate.id)
                logging.info("Skipping block %s because its image content was already displayed", candidate.id)
                continue
            degraded = state.last_error is not None
            vocabulary, vocabulary_period = _current_vocabulary(state)
            logging.info("Rendering block %s", candidate.id)
            image = render_candidate(
                config,
                candidate,
                image_result.payload,
                degraded=degraded,
                vocabulary=vocabulary,
            )
            publish_image(image, config)
            state.queue_ids.pop(0)
            state.last_displayed_id = candidate.id
            state.last_displayed_ids = [candidate.id]
            state.shown_ids = _append_unique(state.shown_ids, candidate.id)
            state.displayed_image_digests = _append_unique(state.displayed_image_digests, image_digest)
            state.vocabulary_period = vocabulary_period
            save_state(config.state_path, state)
            logging.info("Displayed block %s from %s", candidate.id, candidate.channel_slug)
            return True
        except DisplayPublishError:
            raise
        except Exception as exc:  # noqa: BLE001
            state.queue_ids.pop(0)
            state.shown_ids = _append_unique(state.shown_ids, candidate.id)
            logging.warning("Skipping block %s after image/render failure: %s", candidate.id, exc)

    return False


def _try_display_collage(
    config: AppConfig,
    client: ArenaClient,
    state: AppState,
    candidate_map: dict[str, DisplayCandidate],
) -> bool:
    prepared_tiles: list[_PreparedCollageTile] = []
    inspected_ids: list[str] = []
    selected_digests: set[str] = set(state.displayed_image_digests)

    for candidate_id in _collage_candidate_order(state.queue_ids, candidate_map):
        candidate = candidate_map.get(candidate_id)
        inspected_ids.append(candidate_id)
        if candidate is None:
            continue
        try:
            logging.info("Fetching collage image for block %s", candidate.id)
            image_result = client.fetch_image_bytes(candidate.image_url)
            image_digest = hashlib.sha256(image_result.payload).hexdigest()
            if image_digest in selected_digests:
                logging.info("Skipping block %s because its image content was already selected or displayed", candidate.id)
                continue
            source_image = decode_source_image(image_result.payload)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Skipping collage block %s after image failure: %s", candidate.id, exc)
            continue

        prepared_tiles.append(
            _PreparedCollageTile(
                candidate=candidate,
                source_image=source_image,
                image_digest=image_digest,
            )
        )
        selected_digests.add(image_digest)
        if len(prepared_tiles) == 4:
            break

    if not prepared_tiles:
        logging.info("No unseen renderable images are available for a collage")
        return False

    vocabulary, vocabulary_period = _current_vocabulary(state)
    try:
        image = render_collage(
            config,
            [tile.source_image for tile in prepared_tiles],
            degraded=state.last_error is not None,
            vocabulary=vocabulary,
        )
        publish_image(image, config)
    except DisplayPublishError:
        raise
    except Exception as exc:  # noqa: BLE001
        logging.warning("Unable to render collage; preserving selection state: %s", exc)
        return False

    consumed_ids = set(inspected_ids)
    state.queue_ids = [candidate_id for candidate_id in state.queue_ids if candidate_id not in consumed_ids]
    for candidate_id in inspected_ids:
        state.shown_ids = _append_unique(state.shown_ids, candidate_id)
    for tile in prepared_tiles:
        state.displayed_image_digests = _append_unique(state.displayed_image_digests, tile.image_digest)
    state.last_displayed_ids = [tile.candidate.id for tile in prepared_tiles]
    state.last_displayed_id = state.last_displayed_ids[-1]
    state.vocabulary_period = vocabulary_period
    save_state(config.state_path, state)
    logging.info(
        "Displayed collage with %d images from blocks %s",
        len(prepared_tiles),
        ", ".join(state.last_displayed_ids),
    )
    return True


def _collage_candidate_order(
    queue_ids: list[str],
    candidate_map: dict[str, DisplayCandidate],
) -> list[str]:
    ordered: list[str] = []
    seen_channels: set[str] = set()
    for candidate_id in queue_ids:
        candidate = candidate_map.get(candidate_id)
        if candidate is None or candidate.channel_slug in seen_channels:
            continue
        ordered.append(candidate_id)
        seen_channels.add(candidate.channel_slug)
    ordered.extend(candidate_id for candidate_id in queue_ids if candidate_id not in ordered)
    return ordered
