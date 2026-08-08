from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Protocol


BUTTON_A_GPIO = 5
BUTTON_B_GPIO = 6
BUTTON_C_GPIO = 16
BUTTON_D_GPIO = 24
CLAIMED_BUTTON_GPIOS = (BUTTON_A_GPIO, BUTTON_B_GPIO)
BUTTON_DEBOUNCE = timedelta(milliseconds=50)
LONG_PRESS_SECONDS = 0.85


class ButtonAction(Enum):
    A_SHORT = "a_short"
    A_LONG = "a_long"
    B_TOGGLE_VOCABULARY = "b_toggle_vocabulary"


class ButtonEdgeEvent(Protocol):
    line_offset: int
    event_type: object
    timestamp_ns: int


class ButtonRequest(Protocol):
    def wait_edge_events(self, timeout: float | None = None) -> bool: ...

    def read_edge_events(self, max_events: int | None = None) -> list[ButtonEdgeEvent]: ...

    def release(self) -> None: ...


@dataclass(slots=True)
class ButtonHardware:
    request: ButtonRequest
    a_offset: int
    b_offset: int
    falling_event_type: object
    rising_event_type: object


ButtonRequestFactory = Callable[[tuple[int, int]], ButtonHardware]
ButtonActionHandler = Callable[[ButtonAction], None]


def _request_button_lines(gpio_numbers: tuple[int, int]) -> ButtonHardware:
    import gpiod
    import gpiodevice
    from gpiod.line import Bias, Direction, Edge

    a_gpio, b_gpio = gpio_numbers
    chip = gpiodevice.find_chip_by_platform()
    a_offset = chip.line_offset_from_id(a_gpio)
    b_offset = chip.line_offset_from_id(b_gpio)
    # Inky's switches are electrically active-low: press is a falling edge and
    # release is a rising edge when the line is held high by this pull-up.
    a_settings = gpiod.LineSettings(
        direction=Direction.INPUT,
        bias=Bias.PULL_UP,
        edge_detection=Edge.BOTH,
        debounce_period=BUTTON_DEBOUNCE,
    )
    b_settings = gpiod.LineSettings(
        direction=Direction.INPUT,
        bias=Bias.PULL_UP,
        edge_detection=Edge.FALLING,
        debounce_period=BUTTON_DEBOUNCE,
    )
    request = chip.request_lines(
        consumer="inky-arena-buttons",
        config={a_offset: a_settings, b_offset: b_settings},
    )
    return ButtonHardware(
        request=request,
        a_offset=a_offset,
        b_offset=b_offset,
        falling_event_type=gpiod.EdgeEvent.Type.FALLING_EDGE,
        rising_event_type=gpiod.EdgeEvent.Type.RISING_EDGE,
    )


class ButtonWatcher:
    def __init__(
        self,
        action_handler: ButtonActionHandler,
        request_factory: ButtonRequestFactory = _request_button_lines,
    ) -> None:
        self._action_handler = action_handler
        self._request_factory = request_factory
        self._hardware: ButtonHardware | None = None
        self._a_pressed_at: float | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        try:
            self._hardware = self._request_factory(CLAIMED_BUTTON_GPIOS)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            logging.warning("Buttons unavailable; preview rendering will continue: %s", exc)
            return False

        self._thread = threading.Thread(target=self._listen, name="inky-buttons", daemon=True)
        self._thread.start()
        logging.info("Buttons ready: A on GPIO %d, B on GPIO %d", BUTTON_A_GPIO, BUTTON_B_GPIO)
        return True

    def close(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._hardware is not None:
            self._hardware.request.release()
            self._hardware = None

    def _listen(self) -> None:
        if self._hardware is None:
            return

        try:
            while not self._stop_event.is_set():
                if not self._hardware.request.wait_edge_events(timeout=0.25):
                    continue
                for event in self._hardware.request.read_edge_events(max_events=16):
                    self._handle_event(event)
        except (OSError, RuntimeError):
            if not self._stop_event.is_set():
                logging.exception("Button watcher stopped unexpectedly")

    def _handle_event(self, event: ButtonEdgeEvent) -> None:
        if self._hardware is None:
            return

        event_time = event.timestamp_ns / 1_000_000_000 if event.timestamp_ns else time.monotonic()
        if event.line_offset == self._hardware.a_offset:
            if event.event_type == self._hardware.falling_event_type:
                self._a_pressed_at = event_time
                return
            if event.event_type == self._hardware.rising_event_type and self._a_pressed_at is not None:
                duration = max(0.0, event_time - self._a_pressed_at)
                self._a_pressed_at = None
                action = ButtonAction.A_LONG if duration >= LONG_PRESS_SECONDS else ButtonAction.A_SHORT
                logging.info("Button A %s press detected (%.2fs)", "long" if action is ButtonAction.A_LONG else "short", duration)
                self._action_handler(action)
                return

        if event.line_offset == self._hardware.b_offset and event.event_type == self._hardware.falling_event_type:
            logging.info("Button B pressed")
            self._action_handler(ButtonAction.B_TOGGLE_VOCABULARY)
