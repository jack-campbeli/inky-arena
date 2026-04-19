from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from inky_arena.models import AppState


def load_state(path: Path) -> AppState:
    if not path.exists():
        return AppState()

    payload = json.loads(path.read_text(encoding="utf-8"))
    return AppState(
        queue_ids=list(payload.get("queue_ids", [])),
        shown_ids=list(payload.get("shown_ids", [])),
        last_candidate_ids=list(payload.get("last_candidate_ids", [])),
        last_displayed_id=payload.get("last_displayed_id"),
        last_sync_iso=payload.get("last_sync_iso"),
        last_error=payload.get("last_error"),
    )


def save_state(path: Path, state: AppState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2, sort_keys=True), encoding="utf-8")

