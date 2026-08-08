from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from inky_arena.models import AppState, DisplayCandidate
from inky_arena.state import load_state, save_state


class StateTests(unittest.TestCase):
    def test_state_round_trips_through_atomic_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            candidate = DisplayCandidate(
                id="one",
                channel_slug="demo",
                channel_title="Demo",
                block_type="Image",
                title="One",
                image_url="https://example.com/one.png",
            )
            state = AppState(
                queue_ids=["one"],
                shown_ids=["two"],
                displayed_image_digests=["abc123"],
                cached_candidates=[candidate],
                last_displayed_id="two",
                channel_failure_counts={"walked": 2},
                channel_page_cursors={"demo": 3},
            )

            save_state(path, state)
            loaded = load_state(path)

            self.assertEqual(loaded.queue_ids, ["one"])
            self.assertEqual(loaded.shown_ids, ["two"])
            self.assertEqual(loaded.displayed_image_digests, ["abc123"])
            self.assertEqual(loaded.cached_candidates, [candidate])
            self.assertEqual(loaded.last_displayed_id, "two")
            self.assertEqual(loaded.channel_failure_counts, {"walked": 2})
            self.assertEqual(loaded.channel_page_cursors, {"demo": 3})

    def test_malformed_json_is_preserved_and_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text('{"queue_ids": [', encoding="utf-8")

            with self.assertLogs(level="WARNING"):
                loaded = load_state(path)

            self.assertEqual(loaded, AppState())
            self.assertFalse(path.exists())
            corrupt_files = list(Path(tmpdir).glob("state.json.*.corrupt"))
            self.assertEqual(len(corrupt_files), 1)
            self.assertEqual(corrupt_files[0].read_text(encoding="utf-8"), '{"queue_ids": [')

    def test_incompatible_state_shape_is_preserved_and_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text(json.dumps({"queue_ids": "not-a-list"}), encoding="utf-8")

            with self.assertLogs(level="WARNING"):
                loaded = load_state(path)

            self.assertEqual(loaded, AppState())
            self.assertEqual(len(list(Path(tmpdir).glob("state.json.*.corrupt"))), 1)

    def test_replace_failure_preserves_previous_valid_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            save_state(path, AppState(last_displayed_id="old"))

            with patch("os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    save_state(path, AppState(last_displayed_id="new"))

            self.assertEqual(load_state(path).last_displayed_id, "old")
            self.assertEqual(list(Path(tmpdir).glob(".state.json.*.tmp")), [])

    def test_state_read_permission_error_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text("{}", encoding="utf-8")

            with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    load_state(path)

    def test_corrupt_state_rename_failure_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text("not-json", encoding="utf-8")

            with patch.object(Path, "replace", side_effect=PermissionError("denied")):
                with self.assertRaises(BaseException) as context:
                    load_state(path)

            self.assertIsInstance(context.exception, PermissionError)
