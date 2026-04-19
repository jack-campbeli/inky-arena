from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_CONFIG_PATH = Path("config.toml")
DEFAULT_PREVIEW_OUTPUT = Path("cache/preview.png")
DEFAULT_STATE_PATH = Path("cache/state.json")
DEFAULT_DOWNLOAD_CACHE_DIR = Path("cache/downloads")


def _parse_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(value).strip()]


def _normalize_channel_slug(value: str) -> str:
    cleaned = value.strip().strip("/")
    if not cleaned:
        return ""
    if "://" in cleaned:
        cleaned = cleaned.split("://", 1)[1]
        cleaned = cleaned.split("/", 1)[1] if "/" in cleaned else cleaned
    if "/" in cleaned:
        cleaned = cleaned.rsplit("/", 1)[-1]
    return cleaned


def _pick_font(*paths: str) -> str:
    for path in paths:
        if Path(path).exists():
            return path
    return ""


@dataclass(slots=True)
class AppConfig:
    channel_slugs: list[str]
    arena_token: str | None = None
    refresh_minutes: int = 2
    sync_minutes: int = 15
    request_timeout_seconds: float = 20.0
    max_blocks_per_channel: int = 48
    state_path: Path = field(default_factory=lambda: DEFAULT_STATE_PATH)
    preview_output: Path = field(default_factory=lambda: DEFAULT_PREVIEW_OUTPUT)
    download_cache_dir: Path = field(default_factory=lambda: DEFAULT_DOWNLOAD_CACHE_DIR)
    arena_base_url: str = "https://api.are.na"
    display_width: int = 480
    display_height: int = 800
    caption_height: int = 64
    primary_font_path: str = field(
        default_factory=lambda: _pick_font(
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/opentype/urw-base35/NimbusSans-Regular.otf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        )
    )
    bold_font_path: str = field(
        default_factory=lambda: _pick_font(
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/opentype/urw-base35/NimbusSans-Bold.otf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        )
    )
    mono_font_path: str = field(
        default_factory=lambda: _pick_font(
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        )
    )

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AppConfig":
        config_path = Path(path or os.getenv("ARENA_CONFIG", DEFAULT_CONFIG_PATH))
        raw: dict[str, object] = {}
        if config_path.exists():
            raw = tomllib.loads(config_path.read_text(encoding="utf-8"))

        env_map: dict[str, object] = {
            "channel_slugs": os.getenv("ARENA_CHANNEL_SLUGS"),
            "arena_token": os.getenv("ARENA_TOKEN"),
            "refresh_minutes": os.getenv("ARENA_REFRESH_MINUTES"),
            "sync_minutes": os.getenv("ARENA_SYNC_MINUTES"),
            "request_timeout_seconds": os.getenv("ARENA_REQUEST_TIMEOUT_SECONDS"),
            "max_blocks_per_channel": os.getenv("ARENA_MAX_BLOCKS_PER_CHANNEL"),
            "state_path": os.getenv("ARENA_STATE_PATH"),
            "preview_output": os.getenv("ARENA_PREVIEW_OUTPUT"),
        }

        merged = {**raw}
        for key, value in env_map.items():
            if value not in (None, ""):
                merged[key] = value

        channel_slugs = [_normalize_channel_slug(value) for value in _parse_list(merged.get("channel_slugs"))]
        channel_slugs = [value for value in channel_slugs if value]
        if not channel_slugs:
            raise ValueError(
                "Missing required config value: channel_slugs. Copy config.example.toml to config.toml and set one or more Are.na channels."
            )

        arena_token = str(merged["arena_token"]).strip() if merged.get("arena_token") not in (None, "") else None

        return cls(
            channel_slugs=channel_slugs,
            arena_token=arena_token,
            refresh_minutes=max(1, int(merged.get("refresh_minutes", 2))),
            sync_minutes=max(1, int(merged.get("sync_minutes", 15))),
            request_timeout_seconds=float(merged.get("request_timeout_seconds", 20.0)),
            max_blocks_per_channel=max(1, int(merged.get("max_blocks_per_channel", 48))),
            state_path=Path(str(merged.get("state_path", DEFAULT_STATE_PATH))),
            preview_output=Path(str(merged.get("preview_output", DEFAULT_PREVIEW_OUTPUT))),
        )
