from __future__ import annotations

from dataclasses import dataclass, field


DISPLAY_MODE_COLLAGE = "collage"
DISPLAY_MODE_SINGLE = "single"
DISPLAY_MODES = {DISPLAY_MODE_COLLAGE, DISPLAY_MODE_SINGLE}


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
    displayed_image_digests: list[str] = field(default_factory=list)
    last_candidate_ids: list[str] = field(default_factory=list)
    cached_candidates: list[DisplayCandidate] = field(default_factory=list)
    last_displayed_id: str | None = None
    last_displayed_ids: list[str] = field(default_factory=list)
    last_sync_iso: str | None = None
    next_sync_not_before_iso: str | None = None
    last_error: str | None = None
    discovered_channels: list[str] = field(default_factory=list)
    channel_failure_counts: dict[str, int] = field(default_factory=dict)
    channel_page_cursors: dict[str, int] = field(default_factory=dict)
    vocabulary_enabled: bool = False
    vocabulary_period: str | None = None
    display_mode: str = DISPLAY_MODE_COLLAGE
