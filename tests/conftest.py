from __future__ import annotations

import random
from typing import Any

import numpy as np
import pytest

from humanlike_mcp.config import ConfigManager, Settings
from humanlike_mcp.errors import FailSafeTriggered
from humanlike_mcp.keyboard import US_SHIFTED, Keyboard
from humanlike_mcp.mouse import Mouse
from humanlike_mcp.screen import Bounds


class Recorder:
    """Shared, ordered log of input events and sleeps."""

    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []

    def sleep(self, seconds: float) -> None:
        self.events.append(("sleep", seconds))

    @property
    def slept(self) -> float:
        return sum(e[1] for e in self.events if e[0] == "sleep")

    def of(self, *kinds: str) -> list[tuple[Any, ...]]:
        return [e for e in self.events if e[0] in kinds]


class FakeMouseBackend:
    def __init__(self, rec: Recorder, start: tuple[int, int] = (500, 500), bounds: Bounds | None = None) -> None:
        self.rec = rec
        self.pos = start
        self._bounds = bounds or Bounds(0, 0, 1920, 1080)
        self.failsafe = True
        self.fail_after_moves: int | None = None
        self.moves = 0

    def position(self) -> tuple[int, int]:
        return self.pos

    def move_to(self, x: int, y: int) -> None:
        self.check_failsafe()
        self.moves += 1
        if self.fail_after_moves is not None and self.moves > self.fail_after_moves:
            raise FailSafeTriggered()
        self.pos = (x, y)
        self.rec.events.append(("move", x, y))

    def button_down(self, button: str) -> None:
        self.rec.events.append(("down", button))

    def button_up(self, button: str, *, force: bool = False) -> None:
        self.rec.events.append(("up", button))

    def scroll(self, dx: int, dy: int) -> None:
        self.rec.events.append(("scroll", dx, dy))

    def bounds(self) -> Bounds:
        return self._bounds

    def failsafe_points(self) -> list[tuple[int, int]]:
        b = self._bounds
        return [(b.left, b.top), (b.left, b.bottom), (b.right, b.top), (b.right, b.bottom)]

    def set_failsafe(self, enabled: bool) -> None:
        self.failsafe = enabled

    def check_failsafe(self) -> None:
        if self.failsafe and self.pos in self.failsafe_points():
            raise FailSafeTriggered()


class FakeKeyboardBackend:
    shift = "shift"

    def __init__(self, rec: Recorder) -> None:
        self.rec = rec
        self.fail_on_press: str | None = None

    def press(self, key: Any) -> None:
        if key == self.fail_on_press:
            raise RuntimeError(f"press {key} failed")
        self.rec.events.append(("press", key))

    def release(self, key: Any) -> None:
        self.rec.events.append(("release", key))

    def char_key(self, char: str) -> tuple[Any, bool]:
        special = {"\n": "enter", "\t": "tab", " ": "space"}
        if char in special:
            return special[char], False
        if char in US_SHIFTED:
            return US_SHIFTED[char], True
        if char.isascii() and char.isupper():
            return char.lower(), True
        return char, False

    def named_key(self, name: str) -> Any:
        return name

    def hotkey_char(self, char: str) -> Any:
        return char


SHIFT_OF = {v: k for k, v in US_SHIFTED.items()}


def typed_text(events: list[tuple[Any, ...]]) -> str:
    """Reconstruct what an editor would show from press events."""
    out: list[str] = []
    shift = False
    for kind, *rest in events:
        if kind not in ("press", "release"):
            continue
        key = rest[0]
        if key == "shift":
            shift = kind == "press"
            continue
        if kind != "press":
            continue
        if key == "backspace":
            if out:
                out.pop()
        elif key == "enter":
            out.append("\n")
        elif key == "tab":
            out.append("\t")
        elif key == "space":
            out.append(" ")
        elif shift:
            out.append(SHIFT_OF.get(key, key.upper()))
        else:
            out.append(key)
    return "".join(out)


@pytest.fixture
def rec() -> Recorder:
    return Recorder()


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def mouse(rec: Recorder) -> Mouse:
    np.random.seed(1234)
    return Mouse(FakeMouseBackend(rec), rng=random.Random(42), sleep=rec.sleep)


@pytest.fixture
def keyboard(rec: Recorder) -> Keyboard:
    return Keyboard(FakeKeyboardBackend(rec), rng=random.Random(7), sleep=rec.sleep)


@pytest.fixture
def config(tmp_path: Any) -> ConfigManager:
    return ConfigManager(env={}, cwd=tmp_path)
