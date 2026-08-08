from __future__ import annotations

import subprocess
import unittest
from unittest.mock import Mock, patch

from inky_arena.build_info import get_build_label


class BuildInfoTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_build_label.cache_clear()

    def test_build_label_uses_short_git_revision(self) -> None:
        completed = Mock(returncode=0, stdout="58267c84\n")
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("inky_arena.build_info.subprocess.run", return_value=completed),
        ):
            get_build_label.cache_clear()
            label = get_build_label()

        self.assertEqual(label, "r58267c84")

    def test_build_label_can_be_overridden_for_packaged_deployments(self) -> None:
        with patch.dict("os.environ", {"INKY_ARENA_BUILD_LABEL": "release-12"}, clear=True):
            get_build_label.cache_clear()
            label = get_build_label()

        self.assertEqual(label, "release-12")

    def test_build_label_falls_back_when_git_is_unavailable(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("inky_arena.build_info.subprocess.run", side_effect=subprocess.SubprocessError),
        ):
            get_build_label.cache_clear()
            label = get_build_label()

        self.assertEqual(label, "dev")
