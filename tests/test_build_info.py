from __future__ import annotations

import unittest
from unittest.mock import patch

from inky_arena.build_info import get_version_label


class BuildInfoTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_version_label.cache_clear()

    def test_version_label_uses_application_version(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            get_version_label.cache_clear()
            label = get_version_label()

        self.assertEqual(label, "1.1.0")

    def test_version_label_can_be_overridden_for_packaged_deployments(self) -> None:
        with patch.dict("os.environ", {"INKY_ARENA_VERSION": "1.2.3"}, clear=True):
            get_version_label.cache_clear()
            label = get_version_label()

        self.assertEqual(label, "1.2.3")

    def test_invalid_version_override_uses_application_version(self) -> None:
        with patch.dict("os.environ", {"INKY_ARENA_VERSION": "not a version"}, clear=True):
            get_version_label.cache_clear()
            label = get_version_label()

        self.assertEqual(label, "1.1.0")
