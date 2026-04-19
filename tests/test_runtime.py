from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from inky_arena.config import AppConfig
from inky_arena.models import AppState, DisplayCandidate
from inky_arena.runtime import _prepare_queue, publish_image, refresh_once, seconds_until_next_refresh


class FakeClient:
    def __init__(self, candidates: list[DisplayCandidate], image_bytes: bytes | None = None) -> None:
        self._candidates = candidates
        self._image_bytes = image_bytes or _make_png_bytes()

    def fetch_candidates(self) -> list[DisplayCandidate]:
        return list(self._candidates)

    def fetch_image_bytes(self, image_url: str) -> bytes:
        return self._image_bytes


def _make_png_bytes() -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sample.png"
        Image.new("RGB", (120, 120), "red").save(path)
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

            updated = refresh_once(config, FakeClient(candidates), state, rng=random.Random(1))

            self.assertEqual(updated.last_displayed_id, "abc")
            self.assertTrue(config.preview_output.exists())
            self.assertTrue(config.state_path.exists())

    def test_seconds_until_next_refresh_has_floor(self) -> None:
        seconds = seconds_until_next_refresh(2)
        self.assertGreaterEqual(seconds, 5.0)

