from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

from inky_arena.models import AppState, DisplayCandidate


def _string_list(payload: dict[str, object], name: str) -> list[str]:
    value = payload.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Invalid persisted {name}: expected a list of strings")
    return list(value)


def _optional_string(payload: dict[str, object], name: str) -> str | None:
    value = payload.get(name)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Invalid persisted {name}: expected a string or null")
    return value


def _candidate_optional_string(payload: dict[str, object], name: str) -> str | None:
    value = payload.get(name)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Invalid persisted cached candidate {name}: expected a string or null")
    return value


def _decode_state(payload: object) -> AppState:
    if not isinstance(payload, dict):
        raise ValueError("Invalid persisted state: expected a JSON object")

    raw_candidates = payload.get("cached_candidates", [])
    if not isinstance(raw_candidates, list) or not all(isinstance(item, dict) for item in raw_candidates):
        raise ValueError("Invalid persisted cached_candidates: expected a list of objects")

    cached_candidates = [
        DisplayCandidate(
            id=str(item["id"]),
            channel_slug=str(item["channel_slug"]),
            channel_title=str(item["channel_title"]),
            block_type=str(item["block_type"]),
            title=str(item["title"]),
            image_url=str(item["image_url"]),
            source_url=_candidate_optional_string(item, "source_url"),
            source_title=_candidate_optional_string(item, "source_title"),
            href=_candidate_optional_string(item, "href"),
            updated_at=_candidate_optional_string(item, "updated_at"),
        )
        for item in raw_candidates
    ]

    raw_failure_counts = payload.get("channel_failure_counts", {})
    if not isinstance(raw_failure_counts, dict):
        raise ValueError("Invalid persisted channel_failure_counts: expected an object")

    raw_page_cursors = payload.get("channel_page_cursors", {})
    if not isinstance(raw_page_cursors, dict):
        raise ValueError("Invalid persisted channel_page_cursors: expected an object")
    channel_page_cursors = {str(key): int(value) for key, value in raw_page_cursors.items()}
    if any(page < 1 for page in channel_page_cursors.values()):
        raise ValueError("Invalid persisted channel_page_cursors: pages must be positive integers")

    return AppState(
        queue_ids=_string_list(payload, "queue_ids"),
        shown_ids=_string_list(payload, "shown_ids"),
        displayed_image_digests=_string_list(payload, "displayed_image_digests"),
        last_candidate_ids=_string_list(payload, "last_candidate_ids"),
        cached_candidates=cached_candidates,
        last_displayed_id=_optional_string(payload, "last_displayed_id"),
        last_sync_iso=_optional_string(payload, "last_sync_iso"),
        next_sync_not_before_iso=_optional_string(payload, "next_sync_not_before_iso"),
        last_error=_optional_string(payload, "last_error"),
        discovered_channels=_string_list(payload, "discovered_channels"),
        channel_failure_counts={str(key): int(value) for key, value in raw_failure_counts.items()},
        channel_page_cursors=channel_page_cursors,
    )


def _corrupt_state_path(path: Path) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    return path.with_name(f"{path.name}.{timestamp}.corrupt")


def load_state(path: Path) -> AppState:
    if not path.exists():
        return AppState()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _decode_state(payload)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        corrupt_path = _corrupt_state_path(path)
        path.replace(corrupt_path)
        logging.warning("Preserved invalid state at %s and started fresh: %s", corrupt_path, exc)
        return AppState()


def save_state(path: Path, state: AppState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "queue_ids": state.queue_ids,
        "shown_ids": state.shown_ids,
        "displayed_image_digests": state.displayed_image_digests,
        "last_candidate_ids": state.last_candidate_ids,
        "cached_candidates": [
            {
                "id": candidate.id,
                "channel_slug": candidate.channel_slug,
                "channel_title": candidate.channel_title,
                "block_type": candidate.block_type,
                "title": candidate.title,
                "image_url": candidate.image_url,
                "source_url": candidate.source_url,
                "source_title": candidate.source_title,
                "href": candidate.href,
                "updated_at": candidate.updated_at,
            }
            for candidate in state.cached_candidates
        ],
        "last_displayed_id": state.last_displayed_id,
        "last_sync_iso": state.last_sync_iso,
        "next_sync_not_before_iso": state.next_sync_not_before_iso,
        "last_error": state.last_error,
        "discovered_channels": state.discovered_channels,
        "channel_failure_counts": state.channel_failure_counts,
        "channel_page_cursors": state.channel_page_cursors,
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                logging.warning("Unable to clean up temporary state file %s: %s", temporary_path, cleanup_error)
        raise
