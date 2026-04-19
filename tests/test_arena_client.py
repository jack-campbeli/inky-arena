from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from inky_arena.arena_client import ArenaClient
from inky_arena.config import AppConfig


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, headers: dict | None = None, params: dict | None = None, timeout: float | None = None):  # type: ignore[override]
        self.calls.append((url, params or {}))
        return self.responses.pop(0)


class ArenaClientTests(unittest.TestCase):
    def test_fetch_channel_candidates_normalizes_visual_blocks(self) -> None:
        payload = {
            "data": [
                {
                    "id": 1,
                    "title": "Photo",
                    "image": {"src": "https://example.com/a.jpg"},
                    "class": "Image",
                },
                {
                    "id": 2,
                    "title": "Link with preview",
                    "image": {"src": "https://example.com/link-preview.jpg"},
                    "class": "Link",
                    "source": {"url": "https://example.com/article"},
                },
                {
                    "id": 3,
                    "title": "Attachment preview",
                    "attachment": {"preview_url": "https://example.com/file-preview.jpg"},
                    "class": "Attachment",
                },
                {
                    "id": 4,
                    "title": "Text only",
                    "class": "Text",
                },
            ],
            "meta": {"has_more_pages": False},
        }
        session = FakeSession([FakeResponse(payload)])
        config = AppConfig(channel_slugs=["demo"], max_blocks_per_channel=10)
        client = ArenaClient(config, session=session)  # type: ignore[arg-type]

        candidates = client.fetch_channel_candidates("demo")

        self.assertEqual([candidate.id for candidate in candidates], ["1", "2", "3"])
        self.assertEqual(candidates[0].image_url, "https://example.com/a.jpg")
        self.assertEqual(candidates[1].source_url, "https://example.com/article")

    def test_fetch_candidates_deduplicates_across_channels(self) -> None:
        payload_one = {
            "data": [{"id": 10, "image": {"src": "https://example.com/shared.jpg"}, "class": "Image"}],
            "meta": {"has_more_pages": False},
        }
        payload_two = {
            "data": [{"id": 10, "image": {"src": "https://example.com/shared.jpg"}, "class": "Image"}],
            "meta": {"has_more_pages": False},
        }
        session = FakeSession([FakeResponse(payload_one), FakeResponse(payload_two)])
        config = AppConfig(channel_slugs=["one", "two"], max_blocks_per_channel=10)
        client = ArenaClient(config, session=session)  # type: ignore[arg-type]

        candidates = client.fetch_candidates()

        self.assertEqual(len(candidates), 1)

    def test_fetch_candidates_with_metadata_tolerates_partial_channel_failure(self) -> None:
        payload_one = {
            "data": [{"id": 10, "image": {"src": "https://example.com/shared.jpg"}, "class": "Image"}],
            "meta": {"has_more_pages": False},
        }
        session = FakeSession(
            [
                FakeResponse(payload_one),
                FakeResponse({"error": "Too Many Requests"}, status_code=429, headers={"X-RateLimit-Reset": "1776622800"}),
            ]
        )
        config = AppConfig(channel_slugs=["one", "two"], max_blocks_per_channel=10)
        client = ArenaClient(config, session=session)  # type: ignore[arg-type]

        with patch("inky_arena.arena_client.time.sleep"):
            result = client.fetch_candidates_with_metadata()

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].id, "10")
        self.assertEqual(result.errors, ["two: HTTP 429"])
        self.assertEqual(result.next_sync_not_before_iso, "2026-04-19T11:20:00-07:00")
