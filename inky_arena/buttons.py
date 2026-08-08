from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Protocol


BUTTON_A_GPIO = 5
BUTTON_DEBOUNCE = timedelta(milliseconds=200)


class ButtonRequest(Protocol):
    def wait_edge_events(self, timeout: float | None = None) -> bool: ...

    def read_edge_events(self, max_events: int | None = None) -> list[object]: ...

    def release(self) -> None: ...


ButtonRequestFactory = Callable[[int], ButtonRequest]


def _request_button_line(gpio_number: int) -> ButtonRequest:
    import gpiod
    import gpiodevice
    from gpiod.line import Bias, Direction, Edge

    chip = gpiodevice.find_chip_by_platform()
    offset = chip.line_offset_from_id(gpio_number)
    settings = gpiod.LineSettings(
        direction=Direction.INPUT,
        bias=Bias.PULL_UP,
        edge_detection=Edge.FALLING,
        debounce_period=BUTTON_DEBOUNCE,
    )
    return chip.request_lines(consumer="inky-arena-button-a", config={offset: settings})


class ButtonAWatcher:
    def __init__(
        self,
        pressed_event: threading.Event,
        request_factory: ButtonRequestFactory = _request_button_line,
    ) -> None:
        self._pressed_event = pressed_event
        self._request_factory = request_factory
        self._request: ButtonRequest | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        try:
            self._request = self._request_factory(BUTTON_A_GPIO)
        except (ImportError, OSError, RuntimeError) as exc:
            logging.warning("Button A unavailable; vocabulary can still be rendered in previews: %s", exc)
            return False

        self._thread = threading.Thread(target=self._listen, name="button-a", daemon=True)
        self._thread.start()
        logging.info("Button A ready on GPIO %d", BUTTON_A_GPIO)
        return True

    def close(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._request is not None:
            self._request.release()
            self._request = None

    def _listen(self) -> None:
        if self._request is None:
            return

        try:
            while not self._stop_event.is_set():
                if not self._request.wait_edge_events(timeout=0.25):
                    continue
                if self._request.read_edge_events(max_events=1):
                    logging.info("Button A pressed")
                    self._pressed_event.set()
        except (OSError, RuntimeError):
            if not self._stop_event.is_set():
                logging.exception("Button A watcher stopped unexpectedly")
