from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class DisplayCandidate:
    id: str
    channel_slug: str
    channel_title: str
    block_type: str
    title: str
    image_url: str
    source_url: str | None = None
    source_title: str | None = None
    href: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class AppState:
    queue_ids: list[str] = field(default_factory=list)
    shown_ids: list[str] = field(default_factory=list)
    last_candidate_ids: list[str] = field(default_factory=list)
    cached_candidates: list[DisplayCandidate] = field(default_factory=list)
    last_displayed_id: str | None = None
    last_sync_iso: str | None = None
    next_sync_not_before_iso: str | None = None
    last_error: str | None = None
