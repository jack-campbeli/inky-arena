from __future__ import annotations

import random
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PIL import ImageChops
from PIL import ImageStat

from inky_arena.arena_client import CandidateFetchResult, ImageFetchResult
from inky_arena.config import AppConfig
from inky_arena.models import AppState, DisplayCandidate
from inky_arena.runtime import DisplayPublishError, RefreshTimeout, _prepare_queue, _should_use_cached_candidates, _try_display_queue, notify_systemd, publish_image, refresh_deadline, refresh_once, run_forever, seconds_until_next_refresh, sleep_with_watchdog
from inky_arena.render import render_candidate, render_status


class FakeClient:
    def __init__(self, candidates: list[DisplayCandidate], image_bytes: bytes | None = None, from_cache: bool = False) -> None:
        self._candidates = candidates
        self._image_bytes = image_bytes or _make_png_bytes()
        self._from_cache = from_cache

    def fetch_candidates(self) -> list[DisplayCandidate]:
        return list(self._candidates)

    def fetch_candidates_with_metadata(self, channel_slugs: list[str] | None = None) -> CandidateFetchResult:
        return CandidateFetchResult(candidates=list(self._candidates))

    def fetch_image_bytes(self, image_url: str) -> ImageFetchResult:
        return ImageFetchResult(payload=self._image_bytes, from_cache=self._from_cache)

    def fetch_block_connections(self, block_id: str) -> list[str]:
        return []


class SequenceClient(FakeClient):
    def __init__(self, candidate_batches: list[list[DisplayCandidate]], image_bytes: bytes | None = None, from_cache: bool = False) -> None:
        super().__init__(candidate_batches[-1] if candidate_batches else [], image_bytes=image_bytes, from_cache=from_cache)
        self._candidate_batches = [list(batch) for batch in candidate_batches]
        self.fetch_candidates_with_metadata_calls = 0

    def fetch_candidates_with_metadata(self, channel_slugs: list[str] | None = None) -> CandidateFetchResult:
        self.fetch_candidates_with_metadata_calls += 1
        if self._candidate_batches:
            self._candidates = list(self._candidate_batches.pop(0))
        return CandidateFetchResult(candidates=list(self._candidates))


def _make_png_bytes(color: str = "red") -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sample.png"
        Image.new("RGB", (120, 120), color).save(path)
        return path.read_bytes()


def _make_sized_png_bytes(size: tuple[int, int], color: str = "red") -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sample.png"
        Image.new("RGB", size, color).save(path)
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

    def test_prepare_queue_returns_empty_queue_when_all_candidates_were_shown(self) -> None:
        candidates = [
            DisplayCandidate(id="1", channel_slug="a", channel_title="A", block_type="Image", title="One", image_url="https://example.com/1.jpg"),
            DisplayCandidate(id="2", channel_slug="a", channel_title="A", block_type="Image", title="Two", image_url="https://example.com/2.jpg"),
        ]
        state = AppState(shown_ids=["1", "2"], last_candidate_ids=["1", "2"])

        updated = _prepare_queue(state, candidates, random.Random(7))

        self.assertEqual(updated.queue_ids, [])
        self.assertEqual(updated.shown_ids, ["1", "2"])

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

    def test_notify_systemd_is_noop_without_socket(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(notify_systemd("WATCHDOG=1"))

    def test_sleep_with_watchdog_sends_heartbeats_during_sleep(self) -> None:
        monotonic_values = [0.0]
        notifications = []

        def fake_monotonic() -> float:
            return monotonic_values[0]

        def fake_sleep(seconds: float) -> None:
            monotonic_values[0] += seconds

        def fake_notify(message: str) -> bool:
            notifications.append(message)
            return True

        with (
            patch("inky_arena.runtime.systemd_watchdog_interval_seconds", return_value=4.0),
            patch("inky_arena.runtime.notify_systemd", side_effect=fake_notify),
            patch("inky_arena.runtime.time.monotonic", side_effect=fake_monotonic),
            patch("inky_arena.runtime.time.sleep", side_effect=fake_sleep),
        ):
            sleep_with_watchdog(5.0)

        self.assertEqual(notifications, ["WATCHDOG=1\nSTATUS=Sleeping until next refresh"] * 3)

    def test_refresh_deadline_raises_when_cycle_exceeds_timeout(self) -> None:
        with self.assertRaises(RefreshTimeout):
            with refresh_deadline(0.01):
                time.sleep(0.05)

    def test_run_forever_exits_when_refresh_exceeds_deadline(self) -> None:
        config = AppConfig(channel_slugs=["demo"], refresh_timeout_seconds=0.01)

        def slow_refresh(*args: object, **kwargs: object) -> AppState:
            time.sleep(0.05)
            return AppState()

        with (
            patch("inky_arena.runtime.ArenaClient"),
            patch("inky_arena.runtime.load_state", return_value=AppState()),
            patch("inky_arena.runtime.refresh_once", side_effect=slow_refresh),
            patch("inky_arena.runtime.notify_systemd"),
        ):
            with self.assertRaises(SystemExit) as context:
                run_forever(config)

        self.assertEqual(context.exception.code, 1)

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
                def fetch_candidates_with_metadata(self, channel_slugs: list[str] | None = None) -> CandidateFetchResult:  # type: ignore[override]
                    raise RuntimeError("429 Too Many Requests")

            with patch("inky_arena.runtime.publish_image"):
                updated = refresh_once(config, FailingClient([cached]), state, rng=random.Random(1))

            self.assertEqual(updated.last_displayed_id, "cached")

    def test_refresh_once_forces_live_sync_when_cached_queue_is_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recent_sync_iso = datetime.now().astimezone().isoformat()
            config = AppConfig(
                channel_slugs=["demo"],
                sync_minutes=15,
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
            fresh = DisplayCandidate(
                id="fresh",
                channel_slug="demo",
                channel_title="Demo",
                block_type="Image",
                title="Fresh",
                image_url="https://example.com/fresh.jpg",
            )
            state = AppState(
                cached_candidates=[cached],
                shown_ids=["cached"],
                last_candidate_ids=["cached"],
                last_displayed_id="cached",
                last_sync_iso=recent_sync_iso,
            )
            client = SequenceClient([[cached, fresh]])

            with patch("inky_arena.runtime.publish_image"):
                updated = refresh_once(config, client, state, rng=random.Random(1))

            self.assertEqual(client.fetch_candidates_with_metadata_calls, 1)
            self.assertEqual(updated.last_displayed_id, "fresh")
            self.assertEqual(updated.last_candidate_ids, ["cached", "fresh"])
            self.assertEqual(updated.shown_ids, ["cached", "fresh"])

    def test_refresh_once_restarts_cycle_when_forced_live_sync_finds_no_new_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recent_sync_iso = datetime.now().astimezone().isoformat()
            config = AppConfig(
                channel_slugs=["demo"],
                sync_minutes=15,
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
                shown_ids=["cached"],
                last_candidate_ids=["cached"],
                last_displayed_id="cached",
                last_sync_iso=recent_sync_iso,
            )
            client = SequenceClient([[cached]])

            with patch("inky_arena.runtime.publish_image") as mock_publish:
                updated = refresh_once(config, client, state, rng=random.Random(1))

            self.assertEqual(client.fetch_candidates_with_metadata_calls, 1)
            self.assertTrue(mock_publish.called)
            self.assertEqual(updated.last_displayed_id, "cached")
            self.assertEqual(updated.shown_ids, ["cached"])

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
                def fetch_image_bytes(self, image_url: str) -> ImageFetchResult:
                    color = "white" if "blank" in image_url else "red"
                    return ImageFetchResult(payload=_make_png_bytes(color))

            state = AppState()
            with patch("inky_arena.runtime.publish_image"):
                updated = refresh_once(config, MixedClient(candidates), state, rng=random.Random(1))

            self.assertEqual(updated.last_displayed_id, "good")

    def test_refresh_once_rebuilds_queue_after_exhausting_bad_unseen_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig(
                channel_slugs=["demo"],
                state_path=Path(tmpdir) / "state.json",
                preview_output=Path(tmpdir) / "preview.png",
            )
            bad = DisplayCandidate(
                id="bad",
                channel_slug="demo",
                channel_title="Demo",
                block_type="Image",
                title="Bad",
                image_url="https://example.com/bad.png",
            )
            good = DisplayCandidate(
                id="good",
                channel_slug="demo",
                channel_title="Demo",
                block_type="Image",
                title="Good",
                image_url="https://example.com/good.png",
            )

            class MixedClient(FakeClient):
                def fetch_image_bytes(self, image_url: str) -> ImageFetchResult:
                    color = "white" if "bad" in image_url else "red"
                    return ImageFetchResult(payload=_make_png_bytes(color))

            state = AppState(
                shown_ids=["good"],
                last_candidate_ids=["bad", "good"],
            )
            with patch("inky_arena.runtime.publish_image"):
                updated = refresh_once(config, MixedClient([bad, good]), state, rng=random.Random(1))

            self.assertEqual(updated.last_displayed_id, "good")

    def test_prepare_queue_filters_shown_ids_from_existing_queue(self) -> None:
        candidates = [
            DisplayCandidate(id="1", channel_slug="a", channel_title="A", block_type="Image", title="One", image_url="https://example.com/1.jpg"),
            DisplayCandidate(id="2", channel_slug="a", channel_title="A", block_type="Image", title="Two", image_url="https://example.com/2.jpg"),
            DisplayCandidate(id="3", channel_slug="a", channel_title="A", block_type="Image", title="Three", image_url="https://example.com/3.jpg"),
        ]
        # Simulate a retry queue that includes already-shown blocks
        state = AppState(queue_ids=["1", "2", "3"], shown_ids=["1", "2"])

        updated = _prepare_queue(state, candidates, random.Random(7))

        self.assertEqual(updated.queue_ids, ["3"])

    def test_refresh_once_passes_degraded_true_when_state_has_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig(
                channel_slugs=["demo"],
                state_path=Path(tmpdir) / "state.json",
                preview_output=Path(tmpdir) / "preview.png",
            )
            candidate = DisplayCandidate(
                id="err-image",
                channel_slug="demo",
                channel_title="Demo",
                block_type="Image",
                title="Error",
                image_url="https://example.com/err.png",
            )

            class ErrorClient(FakeClient):
                def fetch_candidates_with_metadata(self, channel_slugs=None):
                    return CandidateFetchResult(candidates=list(self._candidates), errors=["sync failed"])

            state = AppState()

            with patch("inky_arena.runtime.publish_image"), patch("inky_arena.runtime.render_candidate") as mock_render:
                mock_render.return_value = Image.new("RGB", (config.display_width, config.display_height), "red")
                refresh_once(config, ErrorClient([candidate]), state, rng=random.Random(1))

            self.assertTrue(mock_render.call_args.kwargs["degraded"])

    def test_refresh_once_passes_degraded_false_when_state_has_no_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig(
                channel_slugs=["demo"],
                state_path=Path(tmpdir) / "state.json",
                preview_output=Path(tmpdir) / "preview.png",
            )
            candidate = DisplayCandidate(
                id="ok-image",
                channel_slug="demo",
                channel_title="Demo",
                block_type="Image",
                title="OK",
                image_url="https://example.com/ok.png",
            )
            state = AppState()

            with patch("inky_arena.runtime.publish_image"), patch("inky_arena.runtime.render_candidate") as mock_render:
                mock_render.return_value = Image.new("RGB", (config.display_width, config.display_height), "red")
                refresh_once(config, FakeClient([candidate]), state, rng=random.Random(1))

            self.assertFalse(mock_render.call_args.kwargs["degraded"])

    def test_render_candidate_uses_landscape_art_dimensions(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        candidate = DisplayCandidate(
            id="landscape-frame",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="Landscape",
            image_url="https://example.com/landscape.png",
        )

        image = render_candidate(config, candidate, _make_sized_png_bytes((1600, 960), "red"))

        self.assertEqual(image.size, (800, 480))

    def test_landscape_art_mode_has_no_footer_band(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        candidate = DisplayCandidate(
            id="no-footer",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="No Footer",
            image_url="https://example.com/no-footer.png",
        )

        image = render_candidate(config, candidate, _make_sized_png_bytes((1600, 960), "red"))
        bottom_strip = image.crop((0, 430, 760, 480))
        colors = bottom_strip.getcolors(maxcolors=100000) or []
        red_pixels = sum(count for count, color in colors if color == (255, 0, 0))
        total_pixels = bottom_strip.width * bottom_strip.height

        self.assertGreater(red_pixels / total_pixels, 0.85)

    def test_portrait_image_gets_art_matte_instead_of_tiny_contain(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        candidate = DisplayCandidate(
            id="portrait-matte",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="Portrait",
            image_url="https://example.com/portrait.png",
        )

        image = render_candidate(config, candidate, _make_sized_png_bytes((400, 900), "red"))
        left_edge = image.crop((0, 0, 80, 480))
        center = image.crop((340, 0, 460, 480))
        left_colors = {color for _, color in (left_edge.getcolors(maxcolors=100000) or [])}
        center_colors = {color for _, color in (center.getcolors(maxcolors=100000) or [])}

        self.assertNotEqual(left_colors, {(243, 239, 228)})
        self.assertIn((255, 0, 0), center_colors)

    def test_render_status_uses_landscape_dimensions(self) -> None:
        config = AppConfig(channel_slugs=["demo"])

        image = render_status(config, "Status", "Details")

        self.assertEqual(image.size, (800, 480))

    def test_render_status_shows_multiple_detail_lines_in_landscape(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        detail = (
            "Rate limited or unable to sync.\n"
            "Using cached content is preferred when available.\n"
            "Check your token, network, and sync interval."
        )

        image = render_status(config, "Are.na sync failed", detail)
        lower_detail_area = image.crop((80, 332, 720, 354))
        colors = lower_detail_area.getcolors(maxcolors=100000) or []
        non_background_pixels = sum(count for count, color in colors if color != (243, 239, 228))

        self.assertEqual(image.size, (800, 480))
        self.assertGreater(non_background_pixels, 40)

    def test_render_candidate_shows_time_overlay_in_art_mode(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        candidate = DisplayCandidate(
            id="time-overlay",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="Time",
            image_url="https://example.com/time.png",
        )

        image = render_candidate(config, candidate, _make_sized_png_bytes((1600, 960), "red"))
        corner = image.crop((680, 420, 800, 480))
        colors = {color for _, color in (corner.getcolors(maxcolors=100000) or [])}

        self.assertIn((21, 21, 21), colors)

    def test_star_field_is_stable_for_same_image(self) -> None:
        config = AppConfig(channel_slugs=["demo"], display_orientation="portrait", metadata_mode="footer")
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
        config = AppConfig(channel_slugs=["demo"], display_orientation="portrait", metadata_mode="footer")
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
        config = AppConfig(channel_slugs=["demo"], display_orientation="portrait", metadata_mode="footer")
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

    def test_refresh_once_discovers_channels_via_block_connections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig(
                channel_slugs=["demo"],
                state_path=Path(tmpdir) / "state.json",
                preview_output=Path(tmpdir) / "preview.png",
            )
            candidate = DisplayCandidate(
                id="abc",
                channel_slug="demo",
                channel_title="Demo",
                block_type="Image",
                title="Hello",
                image_url="https://example.com/abc.jpg",
            )

            class DiscoveryClient(FakeClient):
                def fetch_block_connections(self, block_id: str) -> list[str]:
                    return ["discovered-channel-one", "discovered-channel-two"]

            state = AppState()
            with patch("inky_arena.runtime.publish_image"):
                updated = refresh_once(config, DiscoveryClient([candidate]), state, rng=random.Random(1))

            self.assertIn("discovered-channel-one", updated.discovered_channels)
            self.assertIn("discovered-channel-two", updated.discovered_channels)

    def test_render_candidate_shows_degraded_dot_when_degraded(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        candidate = DisplayCandidate(
            id="degraded-dot",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="Degraded",
            image_url="https://example.com/degraded.png",
        )

        image_healthy = render_candidate(config, candidate, _make_png_bytes("red"))
        image_degraded = render_candidate(config, candidate, _make_png_bytes("red"), degraded=True)

        diff = ImageChops.difference(image_healthy, image_degraded)
        self.assertIsNotNone(diff.getbbox())

    def test_publish_image_writes_preview_when_inky_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig(
                channel_slugs=["demo"],
                preview_output=Path(tmpdir) / "preview.png",
            )
            image = Image.new("RGB", (800, 480), "red")

            with patch("inky_arena.runtime._load_inky_display", return_value=None):
                publish_image(image, config)

            self.assertTrue(config.preview_output.exists())

    def test_publish_image_raises_dedicated_error_when_display_discovery_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig(
                channel_slugs=["demo"],
                preview_output=Path(tmpdir) / "diagnostic.png",
            )
            image = Image.new("RGB", (800, 480), "red")

            with patch("inky_arena.runtime._load_inky_display", side_effect=OSError("display missing")):
                with self.assertRaises(DisplayPublishError) as context:
                    publish_image(image, config)

            self.assertEqual(type(context.exception).__name__, "DisplayPublishError")
            self.assertTrue(config.preview_output.exists())

    def test_publish_image_raises_dedicated_error_when_set_image_fails(self) -> None:
        class FailingDisplay:
            WIDTH = 800
            HEIGHT = 480

            def set_image(self, image: Image.Image) -> None:
                raise OSError("frame transfer failed")

            def show(self) -> None:
                raise AssertionError("show must not run after set_image failure")

        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig(
                channel_slugs=["demo"],
                preview_output=Path(tmpdir) / "diagnostic.png",
            )

            with patch("inky_arena.runtime._load_inky_display", return_value=FailingDisplay()):
                with self.assertRaises(DisplayPublishError) as context:
                    publish_image(Image.new("RGB", (800, 480), "red"), config)

            self.assertEqual(type(context.exception).__name__, "DisplayPublishError")
            self.assertTrue(config.preview_output.exists())

    def test_publish_image_raises_dedicated_error_when_display_show_fails(self) -> None:
        class FailingDisplay:
            WIDTH = 800
            HEIGHT = 480

            def set_image(self, image: Image.Image) -> None:
                self.image = image

            def show(self) -> None:
                raise OSError("SPI failure")

        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig(
                channel_slugs=["demo"],
                preview_output=Path(tmpdir) / "diagnostic.png",
            )

            with patch("inky_arena.runtime._load_inky_display", return_value=FailingDisplay()):
                with self.assertRaises(DisplayPublishError) as context:
                    publish_image(Image.new("RGB", (800, 480), "red"), config)

            self.assertEqual(type(context.exception).__name__, "DisplayPublishError")
            self.assertTrue(config.preview_output.exists())

    def test_diagnostic_preview_failure_does_not_hide_hardware_error(self) -> None:
        config = AppConfig(channel_slugs=["demo"])
        discovery_error = OSError("display missing")

        with (
            patch("inky_arena.runtime._load_inky_display", side_effect=discovery_error),
            patch("inky_arena.runtime._save_preview", side_effect=OSError("preview unwritable")),
            self.assertLogs(level="ERROR"),
        ):
            with self.assertRaises(DisplayPublishError) as context:
                publish_image(Image.new("RGB", (800, 480), "red"), config)

        self.assertEqual(type(context.exception).__name__, "DisplayPublishError")
        self.assertIs(context.exception.__cause__, discovery_error)

    def test_publish_image_does_not_rotate_landscape_output_for_hardware(self) -> None:
        config = AppConfig(channel_slugs=["demo"], display_orientation="landscape", metadata_mode="time_only")
        image = Image.new("RGB", (800, 480), "red")
        image.putpixel((0, 0), (0, 0, 255))

        class FakeDisplay:
            WIDTH = 800
            HEIGHT = 480

            def __init__(self) -> None:
                self.image: Image.Image | None = None
                self.show_called = False

            def set_image(self, image: Image.Image) -> None:
                self.image = image

            def show(self) -> None:
                self.show_called = True

        fake_display = FakeDisplay()

        with patch("inky_arena.runtime._load_inky_display", return_value=fake_display):
            publish_image(image, config)

        self.assertEqual(fake_display.image.size, (800, 480))
        self.assertEqual(fake_display.image.getpixel((0, 0)), (0, 0, 255))
        self.assertTrue(fake_display.show_called)

    def test_publish_image_rotates_portrait_output_for_hardware(self) -> None:
        config = AppConfig(channel_slugs=["demo"], display_orientation="portrait", metadata_mode="footer")
        image = Image.new("RGB", (480, 800), "red")
        image.putpixel((479, 0), (0, 0, 255))

        class FakeDisplay:
            WIDTH = 800
            HEIGHT = 480

            def __init__(self) -> None:
                self.image: Image.Image | None = None

            def set_image(self, image: Image.Image) -> None:
                self.image = image

            def show(self) -> None:
                pass

        fake_display = FakeDisplay()

        with patch("inky_arena.runtime._load_inky_display", return_value=fake_display):
            publish_image(image, config)

        self.assertEqual(fake_display.image.size, (800, 480))
        self.assertEqual(fake_display.image.getpixel((0, 0)), (0, 0, 255))

    def test_display_failure_preserves_candidate_rotation_state(self) -> None:
        candidate = DisplayCandidate(
            id="hardware-failure",
            channel_slug="demo",
            channel_title="Demo",
            block_type="Image",
            title="Hardware failure",
            image_url="https://example.com/failure.png",
        )
        state = AppState(queue_ids=[candidate.id])
        config = AppConfig(channel_slugs=["demo"])

        with (
            patch("inky_arena.runtime.render_candidate", return_value=Image.new("RGB", (800, 480), "red")),
            patch("inky_arena.runtime.publish_image", side_effect=DisplayPublishError("display failed")),
        ):
            with self.assertRaises(DisplayPublishError):
                _try_display_queue(config, FakeClient([candidate]), state, {candidate.id: candidate}, [candidate])

        self.assertEqual(state.queue_ids, [candidate.id])
        self.assertEqual(state.shown_ids, [])
        self.assertIsNone(state.last_displayed_id)

    def test_run_forever_exits_when_display_publish_fails(self) -> None:
        config = AppConfig(channel_slugs=["demo"])

        with (
            patch("inky_arena.runtime.ArenaClient"),
            patch("inky_arena.runtime.load_state", return_value=AppState()),
            patch("inky_arena.runtime.refresh_once", side_effect=DisplayPublishError("display failed")),
            patch("inky_arena.runtime.notify_systemd") as mock_notify,
            patch("inky_arena.runtime.sleep_with_watchdog", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaises(BaseException) as context:
                run_forever(config)

        self.assertIsInstance(context.exception, SystemExit)
        self.assertEqual(context.exception.code, 1)
        self.assertTrue(any("display failed" in call.args[0] for call in mock_notify.call_args_list))
