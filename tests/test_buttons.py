from __future__ import annotations

import threading
import time
import unittest
from dataclasses import dataclass

from inky_arena.buttons import (
    BUTTON_A_GPIO,
    BUTTON_B_GPIO,
    ButtonAction,
    ButtonHardware,
    ButtonWatcher,
)


FALLING = "falling"
RISING = "rising"


@dataclass(slots=True)
class FakeEdgeEvent:
    line_offset: int
    event_type: object
    timestamp_ns: int


class FakeRequest:
    def __init__(self, events: list[FakeEdgeEvent]) -> None:
        self.events = list(events)
        self.released = False

    def wait_edge_events(self, timeout: float | None = None) -> bool:
        if self.events:
            return True
        time.sleep(timeout or 0)
        return False

    def read_edge_events(self, max_events: int | None = None) -> list[FakeEdgeEvent]:
        limit = max_events or len(self.events)
        events = self.events[:limit]
        self.events = self.events[limit:]
        return events

    def release(self) -> None:
        self.released = True


class ButtonTests(unittest.TestCase):
    def _run_events(self, events: list[FakeEdgeEvent]) -> tuple[list[ButtonAction], tuple[int, int], FakeRequest]:
        actions: list[ButtonAction] = []
        action_ready = threading.Event()
        request = FakeRequest(events)
        requested_gpios: list[tuple[int, int]] = []

        def factory(gpio_numbers: tuple[int, int]) -> ButtonHardware:
            requested_gpios.append(gpio_numbers)
            return ButtonHardware(request, BUTTON_A_GPIO, BUTTON_B_GPIO, FALLING, RISING)

        def handle(action: ButtonAction) -> None:
            actions.append(action)
            action_ready.set()

        watcher = ButtonWatcher(handle, request_factory=factory)
        try:
            self.assertTrue(watcher.start())
            self.assertTrue(action_ready.wait(timeout=1.0))
        finally:
            watcher.close()

        self.assertEqual(len(requested_gpios), 1)
        return actions, requested_gpios[0], request

    def test_short_a_press_emits_only_advance_action(self) -> None:
        actions, requested_gpios, request = self._run_events([
            FakeEdgeEvent(BUTTON_A_GPIO, FALLING, 1_000_000_000),
            FakeEdgeEvent(BUTTON_A_GPIO, RISING, 1_500_000_000),
        ])

        self.assertEqual(actions, [ButtonAction.A_SHORT])
        self.assertEqual(requested_gpios, (BUTTON_A_GPIO, BUTTON_B_GPIO))
        self.assertTrue(request.released)

    def test_long_a_press_emits_mode_switch_without_short_action(self) -> None:
        actions, _, _ = self._run_events([
            FakeEdgeEvent(BUTTON_A_GPIO, FALLING, 1_000_000_000),
            FakeEdgeEvent(BUTTON_A_GPIO, RISING, 2_000_000_000),
        ])

        self.assertEqual(actions, [ButtonAction.A_LONG])

    def test_b_press_emits_only_vocabulary_action(self) -> None:
        actions, requested_gpios, _ = self._run_events([
            FakeEdgeEvent(BUTTON_B_GPIO, FALLING, 1_000_000_000),
        ])

        self.assertEqual(actions, [ButtonAction.B_TOGGLE_VOCABULARY])
        self.assertEqual(requested_gpios, (5, 6))

    def test_c_and_d_are_not_claimed(self) -> None:
        _, requested_gpios, _ = self._run_events([
            FakeEdgeEvent(BUTTON_B_GPIO, FALLING, 1_000_000_000),
        ])

        self.assertEqual(requested_gpios, (5, 6))
        self.assertNotIn(16, requested_gpios)
        self.assertNotIn(24, requested_gpios)

    def test_missing_gpio_support_does_not_stop_application(self) -> None:
        def unavailable(gpio_numbers: tuple[int, int]) -> ButtonHardware:
            raise ImportError(f"GPIO {gpio_numbers} unavailable")

        watcher = ButtonWatcher(lambda action: None, request_factory=unavailable)
        with self.assertLogs(level="WARNING"):
            self.assertFalse(watcher.start())

        watcher.close()
