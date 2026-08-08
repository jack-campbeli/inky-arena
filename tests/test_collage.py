from __future__ import annotations

import copy
import hashlib
import io
import itertools
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops

from inky_arena.arena_client import ImageFetchResult
from inky_arena.buttons import ButtonAction
from inky_arena.config import AppConfig
from inky_arena.models import AppState, DisplayCandidate
from inky_arena.render import _choose_collage_layout, _crop_mismatch, render_collage
from inky_arena.runtime import (
    DisplayPublishError,
    _try_display_queue,
    process_button_action,
    switch_display_mode,
    toggle_vocabulary,
)
from inky_arena.state import load_state
from inky_arena.vocabulary import VocabularyEntry


def _png_bytes(color: str, size: tuple[int, int] = (240, 180)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _candidate(number: int, channel: str | None = None) -> DisplayCandidate:
    return DisplayCandidate(
        id=str(number),
        channel_slug=channel or f"channel-{number}",
        channel_title=f"Channel {number}",
        block_type="Image",
        title=f"Image {number}",
        image_url=f"https://example.com/{number}.png",
    )


class ImageMapClient:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads

    def fetch_image_bytes(self, image_url: str) -> ImageFetchResult:
        return ImageFetchResult(payload=self.payloads[image_url], from_cache=False)


class CollageRenderingTests(unittest.TestCase):
    def test_dynamic_layouts_cover_the_entire_canvas(self) -> None:
        width, height = 800, 480
        for count in range(1, 5):
            with self.subTest(count=count):
                images = [
                    Image.new("RGB", ((index + 2) * 170, (index % 2 + 2) * 130))
                    for index in range(count)
                ]
                layout, _ = _choose_collage_layout(images, (width, height))

                self.assertEqual(len(layout), count)
                area = sum(
                    (right - left) * (bottom - top)
                    for left, top, right, bottom in layout
                )
                self.assertEqual(area, width * height)
                self.assertTrue(all(0 <= left < right <= width for left, _, right, _ in layout))
                self.assertTrue(all(0 <= top < bottom <= height for _, top, _, bottom in layout))

    def test_two_image_layout_adapts_to_source_orientation(self) -> None:
        portrait_images = [Image.new("RGB", (300, 700)), Image.new("RGB", (300, 700))]
        landscape_images = [Image.new("RGB", (900, 300)), Image.new("RGB", (900, 300))]

        portrait_layout, _ = _choose_collage_layout(portrait_images, (800, 480))
        landscape_layout, _ = _choose_collage_layout(landscape_images, (800, 480))

        self.assertEqual(portrait_layout[0], (0, 0, 400, 480))
        self.assertEqual(landscape_layout[0], (0, 0, 800, 240))

    def test_four_image_layout_changes_with_source_aspect_ratios(self) -> None:
        squares = [Image.new("RGB", (400, 400)) for _ in range(4)]
        mixed = [
            Image.new("RGB", (1200, 260)),
            Image.new("RGB", (260, 1000)),
            Image.new("RGB", (900, 600)),
            Image.new("RGB", (420, 720)),
        ]

        square_layout, _ = _choose_collage_layout(squares, (800, 480))
        mixed_layout, _ = _choose_collage_layout(mixed, (800, 480))
        fixed_quadrants = [
            (0, 0, 400, 240),
            (400, 0, 800, 240),
            (0, 240, 400, 480),
            (400, 240, 800, 480),
        ]

        self.assertNotEqual(square_layout, fixed_quadrants)
        self.assertNotEqual(mixed_layout, square_layout)

    def test_dynamic_layout_reduces_aspect_ratio_crop(self) -> None:
        sources = [
            Image.new("RGB", (1200, 260)),
            Image.new("RGB", (260, 1000)),
            Image.new("RGB", (900, 600)),
            Image.new("RGB", (420, 720)),
        ]
        dynamic_layout, dynamic_order = _choose_collage_layout(sources, (800, 480))
        fixed_quadrants = [
            (0, 0, 400, 240),
            (400, 0, 800, 240),
            (0, 240, 400, 480),
            (400, 240, 800, 480),
        ]

        dynamic_crop = sum(
            _crop_mismatch(image, rectangle)
            for image, rectangle in zip(dynamic_order, dynamic_layout, strict=True)
        )
        best_fixed_crop = min(
            sum(
                _crop_mismatch(image, rectangle)
                for image, rectangle in zip(order, fixed_quadrants, strict=True)
            )
            for order in itertools.permutations(sources)
        )

        self.assertLess(dynamic_crop, best_fixed_crop)

    def test_four_image_collage_renders_each_dynamic_pane(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        colors = [(255, 0, 0), (0, 128, 0), (0, 0, 255), (255, 255, 0)]
        sources = [
            Image.new("RGB", size, color)
            for size, color in zip([(900, 300), (280, 800), (500, 500), (700, 450)], colors, strict=True)
        ]
        layout, ordered_sources = _choose_collage_layout(sources, (800, 480))
        image = render_collage(config, sources)

        self.assertEqual(image.size, (800, 480))
        source_colors = {id(source): color for source, color in zip(sources, colors, strict=True)}
        for rectangle, source in zip(layout, ordered_sources, strict=True):
            left, top, right, bottom = rectangle
            center = ((left + right) // 2, (top + bottom) // 2)
            self.assertEqual(image.getpixel(center), source_colors[id(source)])

    def test_one_image_collage_fallback_is_full_bleed(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        image = render_collage(config, [Image.new("RGB", (200, 700), "magenta")])

        self.assertEqual(image.getpixel((0, 240)), (255, 0, 255))
        self.assertEqual(image.getpixel((799, 240)), (255, 0, 255))

    def test_vocabulary_text_is_centered_without_a_background_card(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        sources = [Image.new("RGB", (400, 300), "navy"), Image.new("RGB", (400, 300), "orange")]
        entry = VocabularyEntry("pragmatic", "adjective", "focused on practical results and what works")

        plain = render_collage(config, sources)
        with_vocabulary = render_collage(config, sources, vocabulary=entry)

        difference = ImageChops.difference(plain, with_vocabulary)
        bounds = difference.getbbox()

        self.assertIsNotNone(bounds)
        assert bounds is not None
        self.assertGreater(bounds[1], 100)
        self.assertLess(bounds[3], 400)
        self.assertIsNone(difference.crop((0, 0, 800, 100)).getbbox())
        self.assertIsNone(difference.crop((0, 420, 800, 480)).getbbox())


class CollageRuntimeTests(unittest.TestCase):
    def test_collage_uses_up_to_four_images_and_counts_every_tile_as_shown(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            candidates = [_candidate(number) for number in range(1, 6)]
            payloads = {
                candidate.image_url: _png_bytes(color)
                for candidate, color in zip(candidates, ["red", "green", "blue", "yellow", "purple"], strict=True)
            }
            state = AppState(queue_ids=[candidate.id for candidate in candidates])
            config = AppConfig(channel_slugs=["demo"], state_path=Path(tmpdir) / "state.json")

            with patch("inky_arena.runtime.publish_image"):
                displayed = _try_display_queue(
                    config,
                    ImageMapClient(payloads),
                    state,
                    {candidate.id: candidate for candidate in candidates},
                    candidates,
                )

            self.assertTrue(displayed)
            self.assertEqual(len(state.last_displayed_ids), 4)
            self.assertEqual(state.shown_ids, state.last_displayed_ids)
            self.assertEqual(len(state.displayed_image_digests), 4)
            self.assertEqual(len(state.queue_ids), 1)

    def test_collage_falls_back_to_every_available_tile_count(self) -> None:
        for count in (1, 2, 3):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmpdir:
                candidates = [_candidate(number) for number in range(1, count + 1)]
                colors = ["red", "green", "blue"][:count]
                payloads = {candidate.image_url: _png_bytes(color) for candidate, color in zip(candidates, colors, strict=True)}
                state = AppState(queue_ids=[candidate.id for candidate in candidates])
                config = AppConfig(channel_slugs=["demo"], state_path=Path(tmpdir) / "state.json")

                with patch("inky_arena.runtime.publish_image"):
                    self.assertTrue(_try_display_queue(
                        config,
                        ImageMapClient(payloads),
                        state,
                        {candidate.id: candidate for candidate in candidates},
                        candidates,
                    ))

                self.assertEqual(len(state.last_displayed_ids), count)
                self.assertEqual(state.queue_ids, [])

    def test_collage_prioritizes_images_from_different_channels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            candidates = [
                _candidate(1, "same"),
                _candidate(2, "same"),
                _candidate(3, "same"),
                _candidate(4, "same"),
                _candidate(5, "different"),
            ]
            payloads = {
                candidate.image_url: _png_bytes(color)
                for candidate, color in zip(candidates, ["red", "green", "blue", "yellow", "purple"], strict=True)
            }
            state = AppState(queue_ids=[candidate.id for candidate in candidates])
            config = AppConfig(channel_slugs=["demo"], state_path=Path(tmpdir) / "state.json")

            with patch("inky_arena.runtime.publish_image"):
                self.assertTrue(_try_display_queue(
                    config,
                    ImageMapClient(payloads),
                    state,
                    {candidate.id: candidate for candidate in candidates},
                    candidates,
                ))

            self.assertEqual(state.last_displayed_ids, ["1", "5", "2", "3"])

    def test_duplicate_content_is_not_used_twice_in_one_collage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            candidates = [_candidate(number) for number in range(1, 4)]
            red = _png_bytes("red")
            payloads = {
                candidates[0].image_url: red,
                candidates[1].image_url: red,
                candidates[2].image_url: _png_bytes("green"),
            }
            state = AppState(queue_ids=[candidate.id for candidate in candidates])
            config = AppConfig(channel_slugs=["demo"], state_path=Path(tmpdir) / "state.json")

            with patch("inky_arena.runtime.publish_image"):
                self.assertTrue(_try_display_queue(
                    config,
                    ImageMapClient(payloads),
                    state,
                    {candidate.id: candidate for candidate in candidates},
                    candidates,
                ))

            self.assertEqual(state.last_displayed_ids, ["1", "3"])
            self.assertEqual(state.shown_ids, ["1", "2", "3"])
            self.assertEqual(len(state.displayed_image_digests), 2)

    def test_previously_displayed_digest_is_excluded_from_collage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            candidates = [_candidate(1), _candidate(2)]
            old_payload = _png_bytes("red")
            new_payload = _png_bytes("green")
            state = AppState(
                queue_ids=["1", "2"],
                displayed_image_digests=[hashlib.sha256(old_payload).hexdigest()],
            )
            config = AppConfig(channel_slugs=["demo"], state_path=Path(tmpdir) / "state.json")
            payloads = {candidates[0].image_url: old_payload, candidates[1].image_url: new_payload}

            with patch("inky_arena.runtime.publish_image"):
                self.assertTrue(_try_display_queue(
                    config,
                    ImageMapClient(payloads),
                    state,
                    {candidate.id: candidate for candidate in candidates},
                    candidates,
                ))

            self.assertEqual(state.last_displayed_ids, ["2"])
            self.assertEqual(len(state.displayed_image_digests), 2)

    def test_hardware_failure_preserves_entire_collage_rotation_state(self) -> None:
        candidates = [_candidate(number) for number in range(1, 5)]
        payloads = {
            candidate.image_url: _png_bytes(color)
            for candidate, color in zip(candidates, ["red", "green", "blue", "yellow"], strict=True)
        }
        state = AppState(
            queue_ids=[candidate.id for candidate in candidates],
            shown_ids=["old"],
            displayed_image_digests=["old-digest"],
            last_displayed_id="old",
            last_displayed_ids=["old"],
        )
        before = copy.deepcopy(state)

        with patch("inky_arena.runtime.publish_image", side_effect=DisplayPublishError("display failed")):
            with self.assertRaises(DisplayPublishError):
                _try_display_queue(
                    AppConfig(channel_slugs=["demo"]),
                    ImageMapClient(payloads),
                    state,
                    {candidate.id: candidate for candidate in candidates},
                    candidates,
                )

        self.assertEqual(state, before)

    def test_switching_mode_persists_without_resetting_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            config = AppConfig(channel_slugs=["demo"], state_path=path)
            state = AppState(
                shown_ids=["1", "2"],
                displayed_image_digests=["a", "b"],
                display_mode="collage",
            )

            switch_display_mode(config, state)
            loaded = load_state(path)

            self.assertEqual(loaded.display_mode, "single")
            self.assertEqual(loaded.shown_ids, ["1", "2"])
            self.assertEqual(loaded.displayed_image_digests, ["a", "b"])

    def test_b_toggle_redraws_same_collage_without_consuming_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            candidates = [_candidate(1), _candidate(2)]
            payloads = {
                candidates[0].image_url: _png_bytes("red"),
                candidates[1].image_url: _png_bytes("green"),
            }
            state = AppState(
                queue_ids=["next"],
                shown_ids=["1", "2"],
                displayed_image_digests=["digest-1", "digest-2"],
                cached_candidates=candidates,
                last_displayed_id="2",
                last_displayed_ids=["1", "2"],
            )
            before_queue = list(state.queue_ids)
            before_shown = list(state.shown_ids)
            before_digests = list(state.displayed_image_digests)
            config = AppConfig(channel_slugs=["demo"], state_path=Path(tmpdir) / "state.json")

            with (
                patch("inky_arena.runtime.publish_image"),
                patch("inky_arena.runtime.render_collage", return_value=Image.new("RGB", (800, 480))) as mock_render,
            ):
                toggle_vocabulary(config, ImageMapClient(payloads), state)

            self.assertTrue(state.vocabulary_enabled)
            self.assertEqual(state.queue_ids, before_queue)
            self.assertEqual(state.shown_ids, before_shown)
            self.assertEqual(state.displayed_image_digests, before_digests)
            self.assertIsInstance(mock_render.call_args.kwargs["vocabulary"], VocabularyEntry)

    def test_short_a_press_refreshes_immediately_without_changing_mode(self) -> None:
        state = AppState(display_mode="collage")
        with patch("inky_arena.runtime.refresh_once", return_value=state) as mock_refresh:
            result = process_button_action(
                AppConfig(channel_slugs=["demo"]),
                ImageMapClient({}),
                state,
                ButtonAction.A_SHORT,
                rng=random.Random(1),
            )

        self.assertIs(result, state)
        self.assertEqual(state.display_mode, "collage")
        self.assertTrue(mock_refresh.call_args.kwargs["force_sync"])

    def test_long_a_press_switches_mode_then_refreshes_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = AppState(display_mode="collage", shown_ids=["kept"])
            config = AppConfig(channel_slugs=["demo"], state_path=Path(tmpdir) / "state.json")
            with patch("inky_arena.runtime.refresh_once", return_value=state) as mock_refresh:
                process_button_action(
                    config,
                    ImageMapClient({}),
                    state,
                    ButtonAction.A_LONG,
                    rng=random.Random(1),
                )

            self.assertEqual(state.display_mode, "single")
            self.assertEqual(state.shown_ids, ["kept"])
            self.assertTrue(mock_refresh.call_args.kwargs["force_sync"])

    def test_b_action_does_not_advance_or_switch_mode(self) -> None:
        state = AppState(display_mode="collage")
        with (
            patch("inky_arena.runtime.toggle_vocabulary", return_value=state) as mock_toggle,
            patch("inky_arena.runtime.refresh_once") as mock_refresh,
            patch("inky_arena.runtime.switch_display_mode") as mock_switch,
        ):
            process_button_action(
                AppConfig(channel_slugs=["demo"]),
                ImageMapClient({}),
                state,
                ButtonAction.B_TOGGLE_VOCABULARY,
            )

        mock_toggle.assert_called_once()
        mock_refresh.assert_not_called()
        mock_switch.assert_not_called()
        self.assertEqual(state.display_mode, "collage")
