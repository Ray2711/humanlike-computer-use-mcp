from __future__ import annotations

import math
import random

import numpy as np
import pytest

from humanlike_mcp.config import Settings
from humanlike_mcp.errors import FailSafeTriggered
from humanlike_mcp.mouse import Mouse

from .conftest import FakeMouseBackend, Recorder


def sleeps_between(events: list, first: tuple, second: tuple) -> float:
    i = events.index(first)
    j = events.index(second, i + 1)
    return sum(e[1] for e in events[i + 1 : j] if e[0] == "sleep")


def test_move_follows_path_and_lands_near_target(mouse: Mouse, rec: Recorder, settings: Settings) -> None:
    result = mouse.move(1200, 300, settings)
    moves = rec.of("move")
    assert len(moves) > 15
    assert math.dist(moves[-1][1:], (1200, 300)) <= settings.mouse.landing_jitter_px + 1
    assert result["to"] == list(moves[-1][1:])
    assert rec.slept > 0.2


def test_move_without_humanize_is_a_single_jump(mouse: Mouse, rec: Recorder, settings: Settings) -> None:
    s = settings.with_updates({"humanize.enabled": False})
    mouse.move(800, 600, s)
    assert rec.of("move") == [("move", 800, 600)]
    assert rec.slept == 0


def test_targets_are_clamped_and_kept_off_corners(mouse: Mouse, rec: Recorder, settings: Settings) -> None:
    s = settings.with_updates({"humanize.enabled": False})
    mouse.move(5000, 300, s)
    assert rec.events[-1] == ("move", 1919, 300)
    mouse.move(-50, -50, s)
    assert rec.events[-1] == ("move", 1, 1)
    s_off = s.with_updates({"safety.failsafe": False})
    mouse.move(-50, -50, s_off)
    assert rec.events[-1] == ("move", 0, 0)


def test_overshoot_then_correction(rec: Recorder, settings: Settings) -> None:
    np.random.seed(3)
    m = Mouse(FakeMouseBackend(rec, start=(100, 500)), rng=random.Random(1), sleep=rec.sleep)
    s = settings.with_updates({"mouse.overshoot_probability": 1.0, "mouse.landing_jitter_px": 0})
    result = m.move(1100, 500, s)
    assert result["overshoot"] is True
    xs = [e[1] for e in rec.of("move")]
    assert max(xs) > 1100
    assert rec.of("move")[-1][1:] == (1100, 500)


def test_no_overshoot_on_short_moves(rec: Recorder, settings: Settings) -> None:
    m = Mouse(FakeMouseBackend(rec, start=(100, 100)), rng=random.Random(1), sleep=rec.sleep)
    s = settings.with_updates({"mouse.overshoot_probability": 1.0})
    assert m.move(250, 150, s)["overshoot"] is False


def test_faster_speed_takes_less_time(settings: Settings) -> None:
    def total(speed: float) -> float:
        rec = Recorder()
        np.random.seed(5)
        m = Mouse(FakeMouseBackend(rec, start=(100, 100)), rng=random.Random(5), sleep=rec.sleep)
        m.move(900, 700, settings.with_updates({"mouse.speed": speed, "mouse.overshoot_enabled": False}))
        return rec.slept

    assert total(2.0) < total(1.0) * 0.75


def test_click_order_and_hold_timing(mouse: Mouse, rec: Recorder, settings: Settings) -> None:
    mouse.click(700, 400, settings, button="right")
    kinds = [e[0] for e in rec.events if e[0] != "sleep"]
    assert kinds[-2:] == ["down", "up"]
    assert all(k == "move" for k in kinds[:-2])
    hold = sleeps_between(rec.events, ("down", "right"), ("up", "right"))
    assert 0.060 <= hold <= 0.140
    last_move = max(i for i, e in enumerate(rec.events) if e[0] == "move")
    down = rec.events.index(("down", "right"))
    pause = sum(e[1] for e in rec.events[last_move + 1 : down] if e[0] == "sleep")
    assert pause >= 0.050


def test_double_click_gap(mouse: Mouse, rec: Recorder, settings: Settings) -> None:
    mouse.click(None, None, settings, clicks=2)
    assert [e[0] for e in rec.events if e[0] != "sleep"] == ["down", "up", "down", "up"]
    ups = [i for i, e in enumerate(rec.events) if e[0] == "up"]
    downs = [i for i, e in enumerate(rec.events) if e[0] == "down"]
    gap = sum(e[1] for e in rec.events[ups[0] + 1 : downs[1]] if e[0] == "sleep")
    assert 0.080 <= gap <= 0.180


def test_click_validation(mouse: Mouse, settings: Settings) -> None:
    with pytest.raises(ValueError):
        mouse.click(None, None, settings, button="side")
    with pytest.raises(ValueError):
        mouse.click(None, None, settings, clicks=5)


def test_drag_sequence(mouse: Mouse, rec: Recorder, settings: Settings) -> None:
    mouse.drag(300, 300, 900, 600, settings)
    non_sleep = [e for e in rec.events if e[0] != "sleep"]
    down = non_sleep.index(("down", "left"))
    up = non_sleep.index(("up", "left"))
    assert down < up == len(non_sleep) - 1
    assert all(e[0] == "move" for e in non_sleep[down + 1 : up])
    assert non_sleep[up - 1] == ("move", 900, 600)
    assert math.dist(non_sleep[down - 1][1:], (300, 300)) <= 3
    first_carry = next(i for i, e in enumerate(rec.events) if e[0] == "move" and i > rec.events.index(("down", "left")))
    pre_hold = sum(e[1] for e in rec.events[rec.events.index(("down", "left")) + 1 : first_carry] if e[0] == "sleep")
    assert 0.080 <= pre_hold <= 0.200


def test_drag_releases_button_on_failsafe(rec: Recorder, settings: Settings) -> None:
    backend = FakeMouseBackend(rec, start=(100, 100))
    m = Mouse(backend, rng=random.Random(1), sleep=rec.sleep)
    m.move(300, 300, settings.with_updates({"humanize.enabled": False}))
    backend.fail_after_moves = 5
    with pytest.raises(FailSafeTriggered):
        m.drag(300, 300, 1000, 900, settings)
    assert ("down", "left") in rec.events
    assert rec.events[-1] == ("up", "left")


def test_failsafe_blocks_actions(rec: Recorder, settings: Settings) -> None:
    m = Mouse(FakeMouseBackend(rec, start=(0, 0)), rng=random.Random(1), sleep=rec.sleep)
    with pytest.raises(FailSafeTriggered):
        m.move(500, 500, settings)
    with pytest.raises(FailSafeTriggered):
        m.click(None, None, settings)
    assert not rec.of("down", "move")
    m.move(500, 500, settings.with_updates({"safety.failsafe": False}))
    assert rec.of("move")


def test_scroll_ticks(mouse: Mouse, rec: Recorder, settings: Settings) -> None:
    mouse.scroll(-7, settings)
    assert rec.of("scroll") == [("scroll", 0, -1)] * 7
    gaps = [e[1] for e in rec.events if e[0] == "sleep"]
    assert len(gaps) == 6
    rec.events.clear()
    mouse.scroll(3, settings, horizontal=True)
    assert rec.of("scroll") == [("scroll", 1, 0)] * 3


def test_scroll_moves_first_and_instant_mode(mouse: Mouse, rec: Recorder, settings: Settings) -> None:
    s = settings.with_updates({"humanize.enabled": False})
    mouse.scroll(5, s, x=400, y=400)
    assert [e for e in rec.events if e[0] != "sleep"] == [("move", 400, 400), ("scroll", 0, 5)]
    assert mouse.scroll(0, s)["notches"] == 0


def test_mouse_down_up(mouse: Mouse, rec: Recorder, settings: Settings) -> None:
    mouse.button_down("middle", settings)
    mouse.button_up("middle", settings)
    assert rec.of("down", "up") == [("down", "middle"), ("up", "middle")]
