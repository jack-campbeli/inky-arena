from __future__ import annotations

import threading
import time
import unittest

from inky_arena.buttons import BUTTON_A_GPIO, ButtonAWatcher


class FakeRequest:
    def __init__(self) -> None:
        self.delivered = False
        self.released = False

    def wait_edge_events(self, timeout: float | None = None) -> bool:
        if not self.delivered:
            return True
        time.sleep(timeout or 0)
        return False

    def read_edge_events(self, max_events: int | None = None) -> list[object]:
        self.delivered = True
        return [object()]

    def release(self) -> None:
        self.released = True


class ButtonTests(unittest.TestCase):
    def test_button_a_press_sets_wake_event(self) -> None:
        pressed = threading.Event()
        request = FakeRequest()
        requested_gpios: list[int] = []

        def factory(gpio_number: int) -> FakeRequest:
            requested_gpios.append(gpio_number)
            return request

        watcher = ButtonAWatcher(pressed, request_factory=factory)
        try:
            self.assertTrue(watcher.start())
            self.assertTrue(pressed.wait(timeout=1.0))
        finally:
            watcher.close()

        self.assertEqual(requested_gpios, [BUTTON_A_GPIO])
        self.assertTrue(request.released)

    def test_missing_gpio_support_does_not_stop_application(self) -> None:
        pressed = threading.Event()

        def unavailable(gpio_number: int) -> FakeRequest:
            raise ImportError(f"GPIO {gpio_number} unavailable")

        watcher = ButtonAWatcher(pressed, request_factory=unavailable)
        with self.assertLogs(level="WARNING"):
            self.assertFalse(watcher.start())

        watcher.close()
        self.assertFalse(pressed.is_set())
