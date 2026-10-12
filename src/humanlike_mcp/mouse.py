"""Human-like mouse control: WindMouse paths (``windmouse``) replayed through ``pyautogui``."""

from __future__ import annotations

import math
import random
import time
from typing import Any, Callable, Protocol

from . import humanize
from .config import Settings
from .errors import FailSafeTriggered
from .screen import Bounds, enable_dpi_awareness, virtual_bounds

Point = tuple[int, int]
BUTTONS = ("left", "right", "middle")
OVERSHOOT_REACTION_MS = (60.0, 160.0)
SCROLL_SETTLE_MS = (40.0, 140.0)


class MouseBackend(Protocol):
    def position(self) -> Point: ...
    def move_to(self, x: int, y: int) -> None: ...
    def button_down(self, button: str) -> None: ...
    def button_up(self, button: str, *, force: bool = False) -> None: ...
    def scroll(self, dx: int, dy: int) -> None: ...
    def bounds(self) -> Bounds: ...
    def failsafe_points(self) -> list[Point]: ...
    def set_failsafe(self, enabled: bool) -> None: ...
    def check_failsafe(self) -> None: ...


class PyAutoGuiBackend:
    """Moves/clicks via pyautogui; scrolls via pynput (pyautogui's Windows scroll sends
    sub-notch deltas and has no real horizontal scroll)."""

    def __init__(self) -> None:
        enable_dpi_awareness()
        import pyautogui
        from pynput.mouse import Controller

        pyautogui.PAUSE = 0
        pyautogui.MINIMUM_DURATION = 0
        pyautogui.FAILSAFE = True
        self._pg = pyautogui
        self._scroller = Controller()

    def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except self._pg.FailSafeException as exc:
            raise FailSafeTriggered() from exc

    def position(self) -> Point:
        pos = self._pg.position()
        return (int(pos[0]), int(pos[1]))

    def move_to(self, x: int, y: int) -> None:
        self._call(self._pg.moveTo, x, y, _pause=False)

    def button_down(self, button: str) -> None:
        self._call(self._pg.mouseDown, button=button, _pause=False)

    def button_up(self, button: str, *, force: bool = False) -> None:
        if not force:
            self._call(self._pg.mouseUp, button=button, _pause=False)
            return
        previous = self._pg.FAILSAFE
        self._pg.FAILSAFE = False
        try:
            self._pg.mouseUp(button=button, _pause=False)
        finally:
            self._pg.FAILSAFE = previous

    def scroll(self, dx: int, dy: int) -> None:
        self.check_failsafe()
        self._scroller.scroll(dx, dy)

    def bounds(self) -> Bounds:
        return virtual_bounds()

    def failsafe_points(self) -> list[Point]:
        return [tuple(p) for p in self._pg.FAILSAFE_POINTS]  # type: ignore[misc]

    def set_failsafe(self, enabled: bool) -> None:
        self._pg.FAILSAFE = enabled

    def check_failsafe(self) -> None:
        self._call(self._pg.failSafeCheck)


class Mouse:
    def __init__(
        self,
        backend: MouseBackend | None = None,
        *,
        rng: random.Random | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._backend = backend
        self.rng = rng or random.Random()
        self._sleep = sleep

    @property
    def backend(self) -> MouseBackend:
        if self._backend is None:
            self._backend = PyAutoGuiBackend()
        return self._backend

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._sleep(seconds)

    def prepare(self, s: Settings) -> None:
        self.backend.set_failsafe(s.safety.failsafe)
        if s.safety.failsafe:
            self.backend.check_failsafe()

    def position(self) -> Point:
        return self.backend.position()

    def resolve_target(self, x: float, y: float, s: Settings) -> Point:
        """Clamp to the virtual desktop and keep off the fail-safe corner pixels."""
        bounds = self.backend.bounds()
        point = bounds.clamp(x, y)
        if s.safety.failsafe and point in self.backend.failsafe_points():
            cx, cy = bounds.left + bounds.width / 2, bounds.top + bounds.height / 2
            point = (point[0] + (1 if point[0] < cx else -1), point[1] + (1 if point[1] < cy else -1))
        return point

    def _replay(self, points: list[Point], params: humanize.PathParams, ease: bool) -> None:
        delays = humanize.path_delays(self.rng, len(points), params.tick_delay, ease)
        for (px, py), delay in zip(points, delays):
            self.backend.move_to(px, py)
            self.sleep(delay)

    def _move(self, x: float, y: float, s: Settings, *, speed: float, overshoot: bool, jitter: bool) -> dict[str, Any]:
        started = time.perf_counter()
        start = self.position()
        target = self.resolve_target(x, y, s)
        if not s.humanize.enabled:
            self.backend.move_to(*target)
            return {"from": list(start), "to": list(target), "overshoot": False, "points": 1, "duration_ms": 0}
        if jitter:
            dx, dy = humanize.landing_offset(self.rng, s.mouse.landing_jitter_px)
            target = self.resolve_target(target[0] + dx, target[1] + dy, s)
        distance = math.dist(start, target)
        params = humanize.path_params(s.mouse, distance, speed)
        did_overshoot = overshoot and humanize.should_overshoot(self.rng, distance, s.mouse)
        points = 0
        if did_overshoot:
            past = humanize.overshoot_point(self.rng, start, target, s.mouse.overshoot_max_px)
            past = self.resolve_target(*past, s)
            first = humanize.apply_arc(self.rng, humanize.generate_path(start, past, params), start, s.mouse.arc_strength)
            self._replay(first, params, s.mouse.ease)
            self.sleep(humanize.sample_ms(self.rng, OVERSHOOT_REACTION_MS))
            correction = humanize.correction_params(params)
            second = humanize.generate_path(past, target, correction)
            self._replay(second, correction, s.mouse.ease)
            points = len(first) + len(second)
        else:
            path = humanize.apply_arc(self.rng, humanize.generate_path(start, target, params), start, s.mouse.arc_strength)
            self._replay(path, params, s.mouse.ease)
            points = len(path)
        return {
            "from": list(start),
            "to": list(target),
            "overshoot": did_overshoot,
            "points": points,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }

    def move(self, x: float, y: float, s: Settings) -> dict[str, Any]:
        self.prepare(s)
        return self._move(x, y, s, speed=s.mouse.speed, overshoot=True, jitter=True)

    def _press_release(self, button: str, s: Settings) -> None:
        self.backend.button_down(button)
        try:
            if s.humanize.enabled:
                self.sleep(humanize.sample_ms(self.rng, s.click.hold_ms))
        except BaseException:
            self.backend.button_up(button, force=True)
            raise
        self.backend.button_up(button)

    def click(
        self, x: float | None, y: float | None, s: Settings, *, button: str = "left", clicks: int = 1
    ) -> dict[str, Any]:
        _check_button(button)
        if not 1 <= clicks <= 3:
            raise ValueError("clicks must be 1, 2 or 3")
        self.prepare(s)
        moved = None
        if x is not None or y is not None:
            cx, cy = self.position()
            moved = self._move(cx if x is None else x, cy if y is None else y, s, speed=s.mouse.speed, overshoot=True, jitter=True)
        if s.humanize.enabled:
            self.sleep(humanize.sample_ms(self.rng, s.click.pre_click_pause_ms))
        for i in range(clicks):
            self._press_release(button, s)
            if i < clicks - 1 and s.humanize.enabled:
                self.sleep(humanize.sample_ms(self.rng, s.click.double_click_gap_ms))
        return {"button": button, "clicks": clicks, "position": list(self.position()), "move": moved}

    def button_down(self, button: str, s: Settings, x: float | None = None, y: float | None = None) -> dict[str, Any]:
        _check_button(button)
        self.prepare(s)
        if x is not None or y is not None:
            cx, cy = self.position()
            self._move(cx if x is None else x, cy if y is None else y, s, speed=s.mouse.speed, overshoot=True, jitter=True)
            if s.humanize.enabled:
                self.sleep(humanize.sample_ms(self.rng, s.click.pre_click_pause_ms))
        self.backend.button_down(button)
        return {"button": button, "state": "down", "position": list(self.position())}

    def button_up(self, button: str, s: Settings) -> dict[str, Any]:
        _check_button(button)
        self.backend.set_failsafe(s.safety.failsafe)
        self.backend.button_up(button, force=True)
        return {"button": button, "state": "up", "position": list(self.position())}

    def drag(
        self, start_x: float, start_y: float, end_x: float, end_y: float, s: Settings, *, button: str = "left"
    ) -> dict[str, Any]:
        _check_button(button)
        self.prepare(s)
        human = s.humanize.enabled
        approach = self._move(start_x, start_y, s, speed=s.mouse.speed, overshoot=True, jitter=True)
        if human:
            self.sleep(humanize.sample_ms(self.rng, s.click.pre_click_pause_ms))
        self.backend.button_down(button)
        try:
            if human:
                self.sleep(humanize.sample_ms(self.rng, s.drag.pre_move_hold_ms))
            carry = self._move(
                end_x, end_y, s, speed=s.mouse.speed * s.drag.speed_factor, overshoot=False, jitter=False
            )
            if human:
                self.sleep(humanize.sample_ms(self.rng, s.drag.release_dwell_ms))
        except BaseException:
            self.backend.button_up(button, force=True)
            raise
        self.backend.button_up(button, force=True)
        return {"button": button, "from": approach["to"], "to": carry["to"], "duration_ms": carry["duration_ms"]}

    def scroll(
        self, amount: int, s: Settings, *, x: float | None = None, y: float | None = None, horizontal: bool = False
    ) -> dict[str, Any]:
        self.prepare(s)
        if x is not None or y is not None:
            cx, cy = self.position()
            self._move(cx if x is None else x, cy if y is None else y, s, speed=s.mouse.speed, overshoot=True, jitter=True)
            if s.humanize.enabled:
                self.sleep(humanize.sample_ms(self.rng, SCROLL_SETTLE_MS))
        if amount == 0:
            return {"notches": 0, "horizontal": horizontal}
        sign = 1 if amount > 0 else -1

        def notch(count: int) -> None:
            if horizontal:
                self.backend.scroll(count, 0)
            else:
                self.backend.scroll(0, count)

        if not s.humanize.enabled:
            notch(amount)
        else:
            ticks = abs(amount)
            gaps = humanize.scroll_gaps(self.rng, ticks, s.scroll)
            for i in range(ticks):
                notch(sign)
                if i < len(gaps):
                    self.sleep(gaps[i])
        return {"notches": amount, "horizontal": horizontal, "position": list(self.position())}


def _check_button(button: str) -> None:
    if button not in BUTTONS:
        raise ValueError(f"button must be one of {list(BUTTONS)}")
