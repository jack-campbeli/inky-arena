from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import requests

from inky_arena.config import AppConfig
from inky_arena.models import DisplayCandidate


@dataclass(slots=True)
class CandidateFetchResult:
    candidates: list[DisplayCandidate]
    errors: list[str] = field(default_factory=list)
    next_sync_not_before_iso: str | None = None


class ArenaClient:
    def __init__(self, config: AppConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    def fetch_candidates(self) -> list[DisplayCandidate]:
        return self.fetch_candidates_with_metadata().candidates

    def fetch_candidates_with_metadata(self) -> CandidateFetchResult:
        candidates: list[DisplayCandidate] = []
        seen_ids: set[str] = set()
        errors: list[str] = []
        next_sync_not_before_iso: str | None = None

        for index, channel_slug in enumerate(self.config.channel_slugs):
            try:
                channel_candidates = self.fetch_channel_candidates(channel_slug)
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else "unknown"
                errors.append(f"{channel_slug}: HTTP {status_code}")
                reset_iso = self._rate_limit_reset_iso(exc.response)
                if reset_iso:
                    next_sync_not_before_iso = max(filter(None, [next_sync_not_before_iso, reset_iso]), default=reset_iso)
                logging.warning("Skipping channel %s after API failure: %s", channel_slug, exc)
                channel_candidates = []
            except requests.RequestException as exc:
                errors.append(f"{channel_slug}: {type(exc).__name__}")
                logging.warning("Skipping channel %s after request failure: %s", channel_slug, exc)
                channel_candidates = []

            for candidate in channel_candidates:
                if candidate.id in seen_ids:
                    continue
                seen_ids.add(candidate.id)
                candidates.append(candidate)
            if index < len(self.config.channel_slugs) - 1:
                time.sleep(0.25)

        return CandidateFetchResult(
            candidates=candidates,
            errors=errors,
            next_sync_not_before_iso=next_sync_not_before_iso,
        )

    def fetch_channel_candidates(self, channel_slug: str) -> list[DisplayCandidate]:
        raw_items = self._fetch_channel_items(channel_slug)
        candidates = [
            candidate
            for item in raw_items
            if (candidate := self._normalize_candidate(item, channel_slug)) is not None
        ]
        return candidates[: self.config.max_blocks_per_channel]

    def fetch_image_bytes(self, image_url: str) -> bytes:
        response = self.session.get(
            image_url,
            headers=self._headers(),
            timeout=self.config.request_timeout_seconds,
        )
        response.raise_for_status()
        return response.content

    def _fetch_channel_items(self, channel_slug: str) -> list[dict[str, Any]]:
        endpoints = [
            f"{self.config.arena_base_url}/v3/channels/{channel_slug}/contents",
            f"{self.config.arena_base_url}/v2/channels/{channel_slug}/contents",
        ]
        last_error: Exception | None = None

        for endpoint in endpoints:
            try:
                return self._fetch_paginated_items(endpoint)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    last_error = exc
                    continue
                raise

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Unable to fetch channel contents for {channel_slug}")

    def _fetch_paginated_items(self, endpoint: str) -> list[dict[str, Any]]:
        page = 1
        per_page = min(24, self.config.max_blocks_per_channel)
        items: list[dict[str, Any]] = []

        while len(items) < self.config.max_blocks_per_channel:
            response = self.session.get(
                endpoint,
                headers=self._headers(),
                params={"page": page, "per": per_page},
                timeout=self.config.request_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            page_items = self._extract_items(payload)
            if not page_items:
                break
            items.extend(page_items)

            if not self._has_more_pages(payload, page_items, per_page):
                break
            page += 1

        return items[: self.config.max_blocks_per_channel]

    def _extract_items(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            if isinstance(payload.get("data"), list):
                return [item for item in payload["data"] if isinstance(item, dict)]
            if isinstance(payload.get("contents"), list):
                return [item for item in payload["contents"] if isinstance(item, dict)]
        return []

    def _has_more_pages(self, payload: Any, page_items: list[dict[str, Any]], per_page: int) -> bool:
        if isinstance(payload, dict):
            meta = payload.get("meta")
            if isinstance(meta, dict):
                if meta.get("has_more_pages") is not None:
                    return bool(meta.get("has_more_pages"))
                next_page = meta.get("next_page")
                if next_page is not None:
                    return True
            total_pages = payload.get("total_pages")
            current_page = payload.get("current_page")
            if isinstance(total_pages, int) and isinstance(current_page, int):
                return current_page < total_pages
        return len(page_items) >= per_page

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.config.arena_token:
            headers["Authorization"] = f"Bearer {self.config.arena_token}"
        return headers

    def _rate_limit_reset_iso(self, response: requests.Response | None) -> str | None:
        if response is None:
            return None
        reset_value = response.headers.get("X-RateLimit-Reset")
        if not reset_value:
            return None
        try:
            return datetime.fromtimestamp(int(reset_value)).astimezone().isoformat()
        except (TypeError, ValueError, OSError):
            return None

    def _normalize_candidate(self, item: dict[str, Any], fallback_channel_slug: str) -> DisplayCandidate | None:
        image_url = self._pick_image_url(item)
        if not image_url:
            return None

        block_id = item.get("id") or item.get("slug") or hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:12]
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        channel = item.get("channel") if isinstance(item.get("channel"), dict) else {}
        channel_slug = str(channel.get("slug") or fallback_channel_slug)
        channel_title = str(channel.get("title") or fallback_channel_slug)
        title = str(item.get("title") or item.get("generated_title") or source.get("title") or "Untitled")

        return DisplayCandidate(
            id=str(block_id),
            channel_slug=channel_slug,
            channel_title=channel_title,
            block_type=str(item.get("class") or item.get("type") or "Block"),
            title=title,
            image_url=image_url,
            source_url=self._first_str(
                source.get("url"),
                source.get("source_url"),
                source.get("original_url"),
            ),
            source_title=self._first_str(
                source.get("title"),
                source.get("source_title"),
            ),
            href=self._first_str(item.get("href"), item.get("url")),
            updated_at=self._first_str(item.get("updated_at")),
        )

    def _pick_image_url(self, item: dict[str, Any]) -> str | None:
        image = item.get("image")
        if isinstance(image, dict):
            nested = self._first_str(
                image.get("display", {}).get("url") if isinstance(image.get("display"), dict) else None,
                image.get("large", {}).get("url") if isinstance(image.get("large"), dict) else None,
                image.get("thumb", {}).get("url") if isinstance(image.get("thumb"), dict) else None,
                image.get("small", {}).get("src") if isinstance(image.get("small"), dict) else None,
                image.get("square", {}).get("src") if isinstance(image.get("square"), dict) else None,
                image.get("display", {}).get("url") if isinstance(image.get("display"), dict) else None,
                image.get("thumbnail", {}).get("url") if isinstance(image.get("thumbnail"), dict) else None,
                image.get("src"),
            )
            if nested:
                return nested

        block_image = item.get("attachment")
        if isinstance(block_image, dict):
            preview = self._first_str(
                block_image.get("preview_url"),
                block_image.get("image", {}).get("src") if isinstance(block_image.get("image"), dict) else None,
            )
            if preview:
                return preview
            content_type = str(block_image.get("content_type") or "")
            direct = self._first_str(block_image.get("url"))
            if direct and content_type.startswith("image/"):
                return direct

        embed = item.get("embed")
        if isinstance(embed, dict):
            preview = self._first_str(embed.get("thumbnail_url"))
            if preview:
                return preview

        source = item.get("source")
        if isinstance(source, dict):
            direct = self._first_str(source.get("url"))
            if direct and self._looks_like_image_url(direct):
                return direct

        return None

    def _looks_like_image_url(self, value: str) -> bool:
        lowered = value.lower()
        return any(lowered.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"))

    def _first_str(self, *values: object) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
