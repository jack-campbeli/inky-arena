from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta

from PIL import Image

from inky_arena.arena_client import ArenaClient
from inky_arena.config import AppConfig
from inky_arena.models import AppState, DisplayCandidate
from inky_arena.render import render_candidate, render_status
from inky_arena.state import load_state, save_state


LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    config = AppConfig.load()
    run_forever(config)


def run_forever(config: AppConfig) -> None:
    client = ArenaClient(config)
    state = load_state(config.state_path)
    rng = random.Random()

    while True:
        try:
            state = refresh_once(config, client, state, rng=rng)
        except Exception:  # noqa: BLE001
            logging.exception("Refresh cycle failed")

        sleep_seconds = seconds_until_next_refresh(config.refresh_minutes)
        logging.info("Sleeping for %.0f seconds", sleep_seconds)
        time.sleep(sleep_seconds)


def refresh_once(
    config: AppConfig,
    client: ArenaClient,
    state: AppState,
    rng: random.Random | None = None,
) -> AppState:
    rng = rng or random.Random()

    try:
        candidates = client.fetch_candidates()
        state.last_sync_iso = datetime.now().astimezone().isoformat()
        state.last_error = None
    except Exception as exc:
        state.last_error = str(exc)
        publish_image(
            render_status(
                config,
                "Are.na sync failed",
                f"{exc}\n\nCheck your channel slugs, token, and network connection.",
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

    candidate_map = {candidate.id: candidate for candidate in candidates}
    state = _prepare_queue(state, candidates, rng)

    while state.queue_ids:
        next_id = state.queue_ids.pop(0)
        candidate = candidate_map.get(next_id)
        if candidate is None:
            continue
        try:
            image_bytes = client.fetch_image_bytes(candidate.image_url)
            image = render_candidate(config, candidate, image_bytes)
            publish_image(image, config)
            state.last_displayed_id = candidate.id
            state.shown_ids = _append_unique(state.shown_ids, candidate.id, limit=max(200, len(candidates) * 4))
            save_state(config.state_path, state)
            logging.info("Displayed block %s from %s", candidate.id, candidate.channel_slug)
            return state
        except Exception as exc:  # noqa: BLE001
            logging.warning("Skipping block %s after image/render failure: %s", candidate.id, exc)

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
    candidate_ids = [candidate.id for candidate in candidates]
    pool_changed = candidate_ids != state.last_candidate_ids

    valid_queue = [candidate_id for candidate_id in state.queue_ids if candidate_id in candidate_ids]
    state.queue_ids = valid_queue

    if pool_changed:
        state.last_candidate_ids = candidate_ids
        state.shown_ids = [candidate_id for candidate_id in state.shown_ids if candidate_id in candidate_ids]

    if state.queue_ids:
        return state

    unseen = [candidate_id for candidate_id in candidate_ids if candidate_id not in set(state.shown_ids)]
    if not unseen:
        state.shown_ids = []
        unseen = list(candidate_ids)

    rng.shuffle(unseen)
    state.queue_ids = unseen
    return state


def publish_image(image: Image.Image, config: AppConfig) -> None:
    try:
        from inky.auto import auto
    except ImportError:
        _save_preview(image, config, "Inky library unavailable")
        return

    try:
        display = auto()
        resized = image.resize((display.WIDTH, display.HEIGHT))
        display.set_image(resized)
        display.show()
    except Exception as exc:  # noqa: BLE001
        _save_preview(image, config, f"Inky hardware unavailable ({exc})")


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
