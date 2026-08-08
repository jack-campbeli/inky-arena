from __future__ import annotations

import unittest
from datetime import datetime

from inky_arena.vocabulary import VOCABULARY, entry_for_datetime, entry_for_position, period_for_datetime


class VocabularyTests(unittest.TestCase):
    def test_collection_is_large_curated_and_unique(self) -> None:
        words = [entry.word.casefold() for entry in VOCABULARY]

        self.assertGreaterEqual(len(VOCABULARY), 365)
        self.assertEqual(len(words), len(set(words)))
        self.assertTrue(all(entry.word.isascii() and entry.word.isalpha() for entry in VOCABULARY))
        self.assertTrue(all(entry.part_of_speech in {"adjective", "adverb", "noun", "verb"} for entry in VOCABULARY))
        self.assertTrue(all(12 <= len(entry.definition) <= 100 for entry in VOCABULARY))

    def test_every_entry_appears_once_before_rotation_repeats(self) -> None:
        cycle = [entry_for_position(position).word for position in range(len(VOCABULARY))]

        self.assertEqual(len(cycle), len(set(cycle)))
        self.assertEqual(entry_for_position(len(VOCABULARY)), entry_for_position(0))

    def test_four_waking_hour_periods_have_distinct_words(self) -> None:
        moments = [
            datetime(2026, 8, 8, 6),
            datetime(2026, 8, 8, 10),
            datetime(2026, 8, 8, 14),
            datetime(2026, 8, 8, 18),
        ]

        self.assertEqual(len({entry_for_datetime(moment).word for moment in moments}), 4)
        self.assertEqual([period_for_datetime(moment) for moment in moments], [
            "2026-08-08-0",
            "2026-08-08-1",
            "2026-08-08-2",
            "2026-08-08-3",
        ])

    def test_evening_word_remains_visible_until_six_the_next_morning(self) -> None:
        evening = datetime(2026, 8, 8, 18)
        overnight = datetime(2026, 8, 9, 5, 59)

        self.assertEqual(period_for_datetime(overnight), "2026-08-08-3")
        self.assertEqual(entry_for_datetime(overnight), entry_for_datetime(evening))

    def test_word_is_stable_within_each_period(self) -> None:
        self.assertEqual(
            entry_for_datetime(datetime(2026, 8, 8, 6)),
            entry_for_datetime(datetime(2026, 8, 8, 9, 59)),
        )
