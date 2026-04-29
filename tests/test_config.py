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
                'channel_slugs = ["alpha", "beta"]\nrefresh_minutes = 2\nrefresh_timeout_seconds = 300\nmax_blocks_per_channel = 20\n',
                encoding="utf-8",
            )

            old_env = dict(os.environ)
            try:
                os.environ["ARENA_CHANNEL_SLUGS"] = "gamma, delta"
                os.environ["ARENA_REFRESH_MINUTES"] = "5"
                os.environ["ARENA_REFRESH_TIMEOUT_SECONDS"] = "900"
                config = AppConfig.load(path)
            finally:
                os.environ.clear()
                os.environ.update(old_env)

            self.assertEqual(config.channel_slugs, ["gamma", "delta"])
            self.assertEqual(config.refresh_minutes, 5)
            self.assertEqual(config.refresh_timeout_seconds, 900.0)
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

    def test_refresh_timeout_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            path.write_text('channel_slugs = ["alpha"]\nrefresh_timeout_seconds = 0\n', encoding="utf-8")

            config = AppConfig.load(path)

            self.assertEqual(config.refresh_timeout_seconds, 0.0)

    def test_load_reads_landscape_art_mode_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            path.write_text(
                'channel_slugs = ["alpha"]\ndisplay_orientation = "landscape"\nmetadata_mode = "time_only"\n',
                encoding="utf-8",
            )

            config = AppConfig.load(path)

            self.assertEqual(config.display_orientation, "landscape")
            self.assertEqual(config.metadata_mode, "time_only")
            self.assertEqual(config.display_width, 800)
            self.assertEqual(config.display_height, 480)
            self.assertFalse(config.uses_footer)

    def test_load_reads_portrait_footer_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            path.write_text(
                'channel_slugs = ["alpha"]\ndisplay_orientation = "portrait"\nmetadata_mode = "footer"\n',
                encoding="utf-8",
            )

            config = AppConfig.load(path)

            self.assertEqual(config.display_orientation, "portrait")
            self.assertEqual(config.metadata_mode, "footer")
            self.assertEqual(config.display_width, 480)
            self.assertEqual(config.display_height, 800)
            self.assertTrue(config.uses_footer)

    def test_display_settings_can_be_overridden_by_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            path.write_text(
                'channel_slugs = ["alpha"]\ndisplay_orientation = "portrait"\nmetadata_mode = "footer"\n',
                encoding="utf-8",
            )

            old_env = dict(os.environ)
            try:
                os.environ["ARENA_DISPLAY_ORIENTATION"] = "landscape"
                os.environ["ARENA_METADATA_MODE"] = "time_only"
                config = AppConfig.load(path)
            finally:
                os.environ.clear()
                os.environ.update(old_env)

            self.assertEqual(config.display_orientation, "landscape")
            self.assertEqual(config.metadata_mode, "time_only")
            self.assertEqual(config.display_width, 800)
            self.assertEqual(config.display_height, 480)
            self.assertFalse(config.uses_footer)

    def test_invalid_display_settings_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            path.write_text(
                'channel_slugs = ["alpha"]\ndisplay_orientation = "diagonal"\nmetadata_mode = "clockish"\n',
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as ctx:
                AppConfig.load(path)

            self.assertIn("display_orientation", str(ctx.exception))

    def test_invalid_metadata_mode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            path.write_text(
                'channel_slugs = ["alpha"]\ndisplay_orientation = "landscape"\nmetadata_mode = "clockish"\n',
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as ctx:
                AppConfig.load(path)

            self.assertIn("metadata_mode", str(ctx.exception))

    def test_display_settings_are_case_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            path.write_text(
                'channel_slugs = ["alpha"]\ndisplay_orientation = "LANDSCAPE"\nmetadata_mode = "FOOTER"\n',
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as ctx:
                AppConfig.load(path)

            self.assertIn("display_orientation", str(ctx.exception))
