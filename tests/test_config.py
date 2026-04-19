from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from inky_arena.config import AppConfig


class ConfigTests(unittest.TestCase):
    def test_load_requires_channel_slugs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            path.write_text("", encoding="utf-8")

            with self.assertRaises(ValueError):
                AppConfig.load(path)

    def test_load_reads_file_and_env_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            path.write_text(
                'channel_slugs = ["alpha", "beta"]\nrefresh_minutes = 2\nmax_blocks_per_channel = 20\n',
                encoding="utf-8",
            )

            old_env = dict(os.environ)
            try:
                os.environ["ARENA_CHANNEL_SLUGS"] = "gamma, delta"
                os.environ["ARENA_REFRESH_MINUTES"] = "5"
                config = AppConfig.load(path)
            finally:
                os.environ.clear()
                os.environ.update(old_env)

            self.assertEqual(config.channel_slugs, ["gamma", "delta"])
            self.assertEqual(config.refresh_minutes, 5)
            self.assertEqual(config.max_blocks_per_channel, 20)
            self.assertEqual(config.sync_minutes, 15)

    def test_load_normalizes_channel_urlish_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            path.write_text(
                'channel_slugs = ["https://www.are.na/--1801/design-art-direction", "yeah-gesture/graphic-design-inspiration-y_tnlb1_bi8"]\n',
                encoding="utf-8",
            )

            config = AppConfig.load(path)

            self.assertEqual(config.channel_slugs, ["design-art-direction", "graphic-design-inspiration-y_tnlb1_bi8"])
