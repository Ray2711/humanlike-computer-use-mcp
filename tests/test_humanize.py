from __future__ import annotations

import math
import random
import statistics

import numpy as np
import pytest

from humanlike_mcp import humanize as h
from humanlike_mcp.config import MouseSettings, ScrollSettings, TypingSettings

SAMPLE_TEXT = (
    "The quick brown fox jumps over the lazy dog. Typing like a person means small pauses, "
    "occasional mistakes, and a rhythm that speeds up on familiar letter pairs. "
    "Hello, World! This is 2026 and the server types at a believable pace.\n"
    "Second line: emails like jane.doe@example.com and numbers such as 3.14159 also appear."
)


# ---------------------------------------------------------------- distributions


def test_sample_range_stays_in_bounds_and_centres() -> None:
    rng = random.Random(1)
    values = [h.sample_range(rng, (60.0, 140.0)) for _ in range(5000)]
    assert min(values) >= 60 and max(values) <= 140
    assert abs(statistics.mean(values) - 100) < 2
    assert 15 < statistics.stdev(values) < 21


def test_sample_range_degenerate_and_seconds() -> None:
    rng = random.Random(1)
    assert h.sample_range(rng, (50.0, 50.0)) == 50.0
    assert h.sample_ms(rng, (100.0, 100.0)) == pytest.approx(0.1)


def test_lognormal_factor_has_mean_one() -> None:
    rng = random.Random(3)
    values = [h.lognormal_factor(rng, 0.25) for _ in range(20000)]
    assert statistics.mean(values) == pytest.approx(1.0, abs=0.02)
    assert min(values) > 0
    assert h.lognormal_factor(rng, 0) == 1.0


def test_seeded_rng_is_deterministic() -> None:
    a = h.plan_typing(random.Random(99), SAMPLE_TEXT, TypingSettings())
    b = h.plan_typing(random.Random(99), SAMPLE_TEXT, TypingSettings())
    c = h.plan_typing(random.Random(100), SAMPLE_TEXT, TypingSettings())
    assert a == b
    assert a != c


# ---------------------------------------------------------------- mouse geometry


def test_landing_offset_within_radius() -> None:
    rng = random.Random(5)
    for _ in range(2000):
        dx, dy = h.landing_offset(rng, 3.0)
        assert math.hypot(dx, dy) <= 3.0 + 0.71
    assert h.landing_offset(rng, 0) == (0, 0)


def test_should_overshoot_respects_distance_and_probability() -> None:
    rng = random.Random(0)
    always = MouseSettings(overshoot_probability=1.0)
    never = MouseSettings(overshoot_probability=0.0)
    off = MouseSettings(overshoot_probability=1.0, overshoot_enabled=False)
    assert h.should_overshoot(rng, 800, always)
    assert not h.should_overshoot(rng, 100, always)
    assert not h.should_overshoot(rng, 800, never)
    assert not h.should_overshoot(rng, 800, off)
    hits = sum(h.should_overshoot(rng, 800, MouseSettings()) for _ in range(4000))
    assert 0.25 < hits / 4000 < 0.35


def test_overshoot_point_lands_past_target() -> None:
    rng = random.Random(8)
    start, target = (100, 100), (900, 500)
    ux, uy = (800 / math.hypot(800, 400), 400 / math.hypot(800, 400))
    for _ in range(500):
        p = h.overshoot_point(rng, start, target, 25)
        along = (p[0] - target[0]) * ux + (p[1] - target[1]) * uy
        assert along > 0
        assert math.dist(p, target) <= 25 * 1.6
    assert h.overshoot_point(rng, target, target, 25) == target


def test_generate_path_reaches_target_with_bounded_steps() -> None:
    np.random.seed(0)
    params = h.path_params(MouseSettings(), 1000)
    path = h.generate_path((10, 20), (1010, 520), params)
    assert path[-1] == (1010, 520)
    assert len(path) > 20
    steps = [math.dist(a, b) for a, b in zip([(10, 20)] + path, path)]
    assert max(steps) <= params.max_step + 1.5
    assert all(a != b for a, b in zip(path, path[1:]))
    assert h.generate_path((5, 5), (5, 5), params) == []


def test_generate_path_is_curved_and_seedable() -> None:
    params = h.path_params(MouseSettings(), 800)
    np.random.seed(11)
    a = h.generate_path((0, 0), (800, 0), params)
    np.random.seed(11)
    b = h.generate_path((0, 0), (800, 0), params)
    assert a == b
    deviations = []
    for seed in range(30):
        np.random.seed(seed)
        path = h.generate_path((0, 0), (800, 0), params)
        deviations.append(max(abs(y) for _, y in path))
    assert statistics.mean(deviations) > 2


def test_path_params_speed_and_distance_scaling() -> None:
    s = MouseSettings()
    base = h.path_params(s, 500, 1.0)
    fast = h.path_params(s, 500, 4.0)
    assert fast.max_step == pytest.approx(base.max_step * 2)
    assert fast.tick_delay == pytest.approx(base.tick_delay / 2)
    assert h.path_params(s, 2000).max_step > base.max_step > h.path_params(s, 100).max_step
    corr = h.correction_params(base)
    assert corr.wind < base.wind and corr.max_step < base.max_step


@pytest.mark.parametrize(("distance", "speed"), [(1800, 1.0), (1800, 1.6), (2500, 3.0), (300, 0.75)])
def test_paths_never_orbit_the_target(distance: int, speed: float) -> None:
    params = h.path_params(MouseSettings(), distance, speed)
    assert params.damped_distance >= MouseSettings().damped_distance
    worst = 0.0
    for seed in range(150):
        np.random.seed(seed)
        path = h.generate_path((0, 0), (distance, 0), params)
        length = sum(math.dist(a, b) for a, b in zip([(0, 0)] + path, path))
        worst = max(worst, length / distance)
    assert worst < 1.6


def test_arc_bends_path_but_keeps_endpoints() -> None:
    np.random.seed(2)
    params = h.path_params(MouseSettings(), 1000)
    path = h.generate_path((0, 0), (1000, 0), params)
    deviations = []
    for seed in range(200):
        bent = h.apply_arc(random.Random(seed), path, (0, 0), 0.04)
        assert bent[-1] == (1000, 0)
        assert all(a != b for a, b in zip(bent, bent[1:]))
        mid = min(bent, key=lambda p: abs(p[0] - 500))
        deviations.append(abs(mid[1]))
    assert 15 < statistics.mean(deviations) < 60
    assert max(deviations) <= 3 * 40 + 20
    assert h.apply_arc(random.Random(1), path, (0, 0), 0.0) == path


def test_simulated_move_durations_are_human() -> None:
    from humanlike_mcp.config import Settings
    from humanlike_mcp.mouse import Mouse
    from humanlike_mcp.screen import Bounds

    class Sim:
        pos = (0, 0)

        def position(self):
            return self.pos

        def move_to(self, x, y):
            self.pos = (x, y)

        def bounds(self):
            return Bounds(-5000, -5000, 10000, 10000)

        def failsafe_points(self):
            return []

        def set_failsafe(self, enabled):
            pass

        def check_failsafe(self):
            pass

    for distance in (250, 500, 800, 1200):
        durations = []
        for seed in range(60):
            np.random.seed(seed)
            sleeps: list[float] = []
            backend = Sim()
            Mouse(backend, rng=random.Random(seed), sleep=sleeps.append).move(distance, 0, Settings())
            durations.append(sum(sleeps))
        assert 0.3 <= statistics.median(durations) <= 0.9, (distance, statistics.median(durations))


def test_ease_profile_is_bell_shaped() -> None:
    m = h.ease_multipliers(100)
    assert statistics.mean(m) == pytest.approx(1.0)
    assert m[0] > 2 * min(m) and m[-1] > 2 * min(m)
    assert m.index(min(m)) < 50
    assert h.ease_multipliers(2) == [1.0, 1.0]


def test_path_delays_scale_with_tick() -> None:
    rng = random.Random(2)
    delays = h.path_delays(rng, 60, 0.01, ease=True)
    assert len(delays) == 60
    assert sum(delays) == pytest.approx(0.6, rel=0.1)
    flat = h.path_delays(rng, 60, 0.01, ease=False)
    assert all(0.0085 <= d <= 0.0115 for d in flat)


# ---------------------------------------------------------------- scroll


def test_scroll_gaps_count_and_ramp() -> None:
    s = ScrollSettings(flick_notches=(5.0, 5.0))
    rng = random.Random(4)
    assert h.scroll_gaps(rng, 1, s) == []
    gaps = h.scroll_gaps(rng, 5, s)
    assert len(gaps) == 4
    edges, middles = [], []
    for _ in range(500):
        g = h.scroll_gaps(rng, 5, s)
        edges += [g[0], g[3]]
        middles += [g[1], g[2]]
    assert statistics.mean(edges) > statistics.mean(middles) * 1.3


def test_scroll_gaps_include_regrip_pauses() -> None:
    s = ScrollSettings(flick_notches=(4.0, 4.0))
    gaps = h.scroll_gaps(random.Random(1), 12, s)
    assert len(gaps) == 11
    regrips = [g for g in gaps if g >= 0.15]
    assert len(regrips) == 2


# ---------------------------------------------------------------- typing


def test_qwerty_neighbours() -> None:
    assert set(h.QWERTY_NEIGHBOURS["s"]) == {"a", "d", "w", "e", "z", "x"}
    assert "q" not in h.QWERTY_NEIGHBOURS["s"]
    assert "2" in h.QWERTY_NEIGHBOURS["1"]
    rng = random.Random(0)
    assert h.typo_for(rng, "S") in {"A", "D", "W", "E", "Z", "X"}
    assert h.typo_for(rng, "é") is None


def test_bigram_and_hand_alternation() -> None:
    assert h.bigram_factor("t", "h", 0.8) == 0.8
    assert h.bigram_factor("a", "k", 0.8) == pytest.approx(0.9)
    assert h.bigram_factor("s", "d", 0.8) == 1.0
    assert h.bigram_factor(None, "a", 0.8) == 1.0


@pytest.mark.parametrize("seed", range(25))
def test_typos_are_always_corrected(seed: int) -> None:
    text = "Quick Brown Foxes, typing 123 words!\nÜnïcode ✓ stays intact."
    steps = h.plan_typing(random.Random(seed), text, TypingSettings(typo_rate=0.3))
    assert h.apply_keystrokes(steps) == text


def test_typo_rate_matches_configuration() -> None:
    s = TypingSettings(typo_rate=0.05)
    text = SAMPLE_TEXT * 4
    eligible = sum(1 for c in text if c.lower() in h.QWERTY_NEIGHBOURS)
    typos = 0
    for seed in range(20):
        steps = h.plan_typing(random.Random(seed), text, s)
        typos += sum(1 for i, st in enumerate(steps) if st.correction and (i == 0 or not steps[i - 1].correction))
    rate = typos / (eligible * 20)
    assert 0.035 < rate < 0.065


def test_typo_sequence_shape() -> None:
    s = TypingSettings(typo_rate=0.5)
    steps = h.plan_typing(random.Random(3), "hello world", s)
    first_bs = next(i for i, st in enumerate(steps) if st.char == h.BACKSPACE)
    assert steps[first_bs - 1].typo
    assert steps[first_bs].wait >= 0.2 * 0.5
    assert h.apply_keystrokes(steps) == "hello world"


def test_no_typos_when_disabled_or_zero() -> None:
    steps = h.plan_typing(random.Random(1), SAMPLE_TEXT, TypingSettings(typo_rate=0.0))
    assert not any(st.typo or st.correction for st in steps)
    assert "".join(st.char for st in steps) == SAMPLE_TEXT
    instant = h.plan_typing(random.Random(1), SAMPLE_TEXT, TypingSettings(), enabled=False)
    assert all(st.wait == 0 and st.hold == 0 for st in instant)
    assert "".join(st.char for st in instant) == SAMPLE_TEXT


def test_delays_within_bounds() -> None:
    s = TypingSettings()
    steps = h.plan_typing(random.Random(12), SAMPLE_TEXT, s)
    for st in steps:
        assert 0.030 <= st.hold <= 0.090
        assert st.wait >= h.MIN_KEY_GAP_S
        if h.needs_shift(st.char):
            assert 0.040 <= st.shift_lead <= 0.090
        else:
            assert st.shift_lead == 0


def test_context_pauses_are_longer() -> None:
    s = TypingSettings(typo_rate=0.0, think_pause_probability=0.0)
    text = "ab. cd ef, gh ij kl. mn op qr st uv wx"
    after_space, inside = [], []
    for seed in range(200):
        steps = h.plan_typing(random.Random(seed), text, s)
        for i, st in enumerate(steps[1:], start=1):
            prev = text[i - 1]
            if prev == " ":
                after_space.append(st.wait)
            elif prev.isalpha() and st.char.isalpha():
                inside.append(st.wait)
    assert statistics.mean(after_space) > statistics.mean(inside) + 0.08


@pytest.mark.parametrize("wpm", [45.0, 70.0, 100.0])
def test_effective_wpm_matches_configuration(wpm: float) -> None:
    s = TypingSettings(wpm=wpm)
    rates = [h.effective_wpm(SAMPLE_TEXT, h.plan_typing(random.Random(seed), SAMPLE_TEXT, s)) for seed in range(40)]
    assert statistics.mean(rates) == pytest.approx(wpm, rel=0.15)


def test_wpm_override_and_variance() -> None:
    s = TypingSettings()
    slow = h.plan_typing(random.Random(1), SAMPLE_TEXT, s, wpm=30)
    fast = h.plan_typing(random.Random(1), SAMPLE_TEXT, s, wpm=120)
    assert h.plan_duration(slow) > 2.5 * h.plan_duration(fast)
