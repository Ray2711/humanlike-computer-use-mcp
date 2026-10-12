"""Opt-in smoke test against the real desktop: set HUMANLIKE_LIVE_TESTS=1.

Moves the cursor a short distance and back, takes a screenshot, reads the cursor
position. Never clicks or types.
"""

from __future__ import annotations

import math
import os
import time

import pytest

from humanlike_mcp import screen
from humanlike_mcp.config import Settings
from humanlike_mcp.mouse import Mouse

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("HUMANLIKE_LIVE_TESTS") != "1", reason="set HUMANLIKE_LIVE_TESTS=1"),
]


def test_live_short_move_and_back() -> None:
    mouse = Mouse()
    s = Settings().with_updates({"mouse.landing_jitter_px": 0, "mouse.overshoot_enabled": False})
    start = mouse.position()
    bounds = mouse.backend.bounds()
    dx = 60 if start[0] + 60 < bounds.right - 5 else -60
    dy = 30 if start[1] + 30 < bounds.bottom - 5 else -30
    target = (start[0] + dx, start[1] + dy)
    try:
        t0 = time.perf_counter()
        out = mouse.move(*target, s)
        elapsed = time.perf_counter() - t0
        assert math.dist(mouse.position(), target) <= 1
        print(f"\nlive move {start} -> {target}: {out['points']} points in {elapsed * 1000:.0f} ms")
    finally:
        mouse.move(*start, s)
    assert math.dist(mouse.position(), start) <= 1


def test_live_screenshot_and_position() -> None:
    shot = screen.capture(0, max_width=800)
    assert shot.width == 800 and shot.data.startswith(b"\x89PNG")
    info = screen.screen_info()
    x, y = Mouse().position()
    vd = info["virtual_desktop"]
    assert vd["left"] <= x < vd["left"] + vd["width"]
    assert vd["top"] <= y < vd["top"] + vd["height"]
