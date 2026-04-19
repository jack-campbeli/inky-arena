from __future__ import annotations

import json
from pathlib import Path

from inky_arena.models import AppState, DisplayCandidate


def load_state(path: Path) -> AppState:
    if not path.exists():
        return AppState()

    payload = json.loads(path.read_text(encoding="utf-8"))
    cached_candidates = [
        DisplayCandidate(
            id=str(item["id"]),
            channel_slug=str(item["channel_slug"]),
            channel_title=str(item["channel_title"]),
            block_type=str(item["block_type"]),
            title=str(item["title"]),
            image_url=str(item["image_url"]),
            source_url=item.get("source_url"),
            source_title=item.get("source_title"),
            href=item.get("href"),
            updated_at=item.get("updated_at"),
        )
        for item in payload.get("cached_candidates", [])
        if isinstance(item, dict)
    ]
    return AppState(
        queue_ids=list(payload.get("queue_ids", [])),
        shown_ids=list(payload.get("shown_ids", [])),
        last_candidate_ids=list(payload.get("last_candidate_ids", [])),
        cached_candidates=cached_candidates,
        last_displayed_id=payload.get("last_displayed_id"),
        last_sync_iso=payload.get("last_sync_iso"),
        next_sync_not_before_iso=payload.get("next_sync_not_before_iso"),
        last_error=payload.get("last_error"),
    )


def save_state(path: Path, state: AppState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "queue_ids": state.queue_ids,
        "shown_ids": state.shown_ids,
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
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
