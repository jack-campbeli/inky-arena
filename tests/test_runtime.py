from __future__ import annotations

import random
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PIL import ImageChops
from PIL import ImageStat

from inky_arena.config import AppConfig
from inky_arena.models import AppState, DisplayCandidate
from inky_arena.runtime import _prepare_queue, _should_use_cached_candidates, publish_image, refresh_once, seconds_until_next_refresh
from inky_arena.render import render_candidate


class FakeClient:
    def __init__(self, candidates: list[DisplayCandidate], image_bytes: bytes | None = None) -> None:
        self._candidates = candidates
        self._image_bytes = image_bytes or _make_png_bytes()

    def fetch_candidates(self) -> list[DisplayCandidate]:
        return list(self._candidates)

    def fetch_image_bytes(self, image_url: str) -> bytes:
        return self._image_bytes


def _make_png_bytes(color: str = "red") -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sample.png"
        Image.new("RGB", (120, 120), color).save(path)
        return path.read_bytes()


class RuntimeTests(unittest.TestCase):
    def test_prepare_queue_rotates_without_repeats(self) -> None:
        candidates = [
            DisplayCandidate(id="1", channel_slug="a", channel_title="A", block_type="Image", title="One", image_url="https://example.com/1.jpg"),
            DisplayCandidate(id="2", channel_slug="a", channel_title="A", block_type="Image", title="Two", image_url="https://example.com/2.jpg"),
        ]
        state = AppState(shown_ids=["1"])

        updated = _prepare_queue(state, candidates, random.Random(7))

        self.assertEqual(updated.last_candidate_ids, ["1", "2"])
        self.assertEqual(updated.queue_ids, ["2"])

    def test_refresh_once_writes_preview_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig(
                channel_slugs=["demo"],
                state_path=Path(tmpdir) / "state.json",
                preview_output=Path(tmpdir) / "preview.png",
            )
            candidates = [
                DisplayCandidate(id="abc", channel_slug="demo", channel_title="Demo", block_type="Image", title="Hello", image_url="https://example.com/abc.jpg")
            ]
            state = AppState()

            with patch("inky_arena.runtime.publish_image"):
                updated = refresh_once(config, FakeClient(candidates), state, rng=random.Random(1))

            self.assertEqual(updated.last_displayed_id, "abc")
            self.assertTrue(config.state_path.exists())

    def test_seconds_until_next_refresh_has_floor(self) -> None:
        seconds = seconds_until_next_refresh(2)
        self.assertGreaterEqual(seconds, 5.0)

    def test_cached_candidates_are_used_between_syncs(self) -> None:
        config = AppConfig(channel_slugs=["demo"], sync_minutes=15)
        state = AppState(
            cached_candidates=[
                DisplayCandidate(id="1", channel_slug="demo", channel_title="Demo", block_type="Image", title="A", image_url="https://example.com/a.jpg")
            ],
            last_sync_iso="2026-04-19T09:00:00-07:00",
        )

        with patch("inky_arena.runtime.datetime") as mock_datetime:
            real_datetime = __import__("datetime").datetime
            mock_datetime.now.return_value = real_datetime.fromisoformat("2026-04-19T09:05:00-07:00")
            mock_datetime.fromisoformat.side_effect = real_datetime.fromisoformat
            self.assertTrue(_should_use_cached_candidates(config, state))

    def test_cached_candidates_are_used_during_backoff_window(self) -> None:
        config = AppConfig(channel_slugs=["demo"], sync_minutes=15)
        state = AppState(
            cached_candidates=[
                DisplayCandidate(id="1", channel_slug="demo", channel_title="Demo", block_type="Image", title="A", image_url="https://example.com/a.jpg")
            ],
            last_sync_iso="2026-04-19T09:00:00-07:00",
            next_sync_not_before_iso="2026-04-19T09:20:00-07:00",
        )

        with patch("inky_arena.runtime.datetime") as mock_datetime:
            real_datetime = datetime
            mock_datetime.now.return_value = real_datetime.fromisoformat("2026-04-19T09:16:00-07:00")
            mock_datetime.fromisoformat.side_effect = real_datetime.fromisoformat
            self.assertTrue(_should_use_cached_candidates(config, state))

    def test_refresh_once_uses_cached_candidates_when_sync_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig(
                channel_slugs=["demo"],
                state_path=Path(tmpdir) / "state.json",
                preview_output=Path(tmpdir) / "preview.png",
            )
            cached = DisplayCandidate(
                id="cached",
                channel_slug="demo",
                channel_title="Demo",
                block_type="Image",
                title="Cached",
                image_url="https://example.com/cached.jpg",
            )
            state = AppState(
                cached_candidates=[cached],
                last_candidate_ids=["cached"],
                queue_ids=["cached"],
                last_sync_iso="2026-04-19T09:00:00-07:00",
            )

            class FailingClient(FakeClient):
                def fetch_candidates_with_metadata(self):  # type: ignore[override]
                    raise RuntimeError("429 Too Many Requests")

            with patch("inky_arena.runtime.publish_image"):
                updated = refresh_once(config, FailingClient([cached]), state, rng=random.Random(1))

            self.assertEqual(updated.last_displayed_id, "cached")

    def test_refresh_once_skips_blank_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig(
                channel_slugs=["demo"],
                state_path=Path(tmpdir) / "state.json",
                preview_output=Path(tmpdir) / "preview.png",
            )
            candidates = [
                DisplayCandidate(id="blank", channel_slug="demo", channel_title="Demo", block_type="Image", title="Blank", image_url="https://example.com/blank.png"),
                DisplayCandidate(id="good", channel_slug="demo", channel_title="Demo", block_type="Image", title="Good", image_url="https://example.com/good.png"),
            ]

            class MixedClient(FakeClient):
                def fetch_image_bytes(self, image_url: str) -> bytes:
                    return _make_png_bytes("white") if "blank" in image_url else _make_png_bytes("red")

            state = AppState()
            with patch("inky_arena.runtime.publish_image"):
                updated = refresh_once(config, MixedClient(candidates), state, rng=random.Random(1))

            self.assertEqual(updated.last_displayed_id, "good")

    def test_star_field_is_stable_for_same_image(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        candidate = DisplayCandidate(
            id="same",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="Same",
            image_url="https://example.com/same.png",
        )

        image_one = render_candidate(config, candidate, _make_png_bytes("red"))
        image_two = render_candidate(config, candidate, _make_png_bytes("red"))

        diff = ImageChops.difference(image_one, image_two)
        self.assertIsNone(diff.getbbox())

    def test_star_field_varies_for_different_images(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        candidate_one = DisplayCandidate(
            id="one",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="One",
            image_url="https://example.com/one.png",
        )
        candidate_two = DisplayCandidate(
            id="two",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="Two",
            image_url="https://example.com/two.png",
        )

        image_one = render_candidate(config, candidate_one, _make_png_bytes("red"))
        image_two = render_candidate(config, candidate_two, _make_png_bytes("red"))

        diff = ImageChops.difference(image_one, image_two)
        self.assertIsNotNone(diff.getbbox())

    def test_star_field_uses_black_only_in_margins(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        candidate = DisplayCandidate(
            id="black-stars",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="Stars",
            image_url="https://example.com/stars.png",
        )

        image = render_candidate(config, candidate, _make_png_bytes("red"))
        footer_y = config.display_height - config.caption_height
        top_margin = image.crop((0, 0, config.display_width, footer_y))
        colors = top_margin.getcolors(maxcolors=100000) or []
        present_colors = {color for _, color in colors}

        self.assertNotIn((90, 90, 90), present_colors)
        self.assertNotIn((200, 108, 32), present_colors)
