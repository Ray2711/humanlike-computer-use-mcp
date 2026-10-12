"""Pure timing and randomness models. No input side effects, so everything here is unit-testable."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from windmouse.core import wind_mouse

from .config import MouseSettings, Range, ScrollSettings, TypingSettings

Point = tuple[int, int]

MAX_PATH_POINTS = 5000
MIN_KEY_GAP_S = 0.005
REFERENCE_DISTANCE_PX = 500.0
DISTANCE_EXPONENT = 0.7
DISTANCE_SCALE_MIN = 0.45
DISTANCE_SCALE_MAX = 3.0


def sample_range(rng: random.Random, bounds: Range) -> float:
    """Truncated Gaussian centred in ``bounds`` (about 95% of mass inside before truncation)."""
    lo, hi = bounds
    if hi <= lo:
        return lo
    mean, sd = (lo + hi) / 2, (hi - lo) / 4
    for _ in range(12):
        value = rng.gauss(mean, sd)
        if lo <= value <= hi:
            return value
    return min(hi, max(lo, value))


def sample_ms(rng: random.Random, bounds_ms: Range) -> float:
    """Sample a millisecond range and return seconds."""
    return sample_range(rng, bounds_ms) / 1000.0


def lognormal_factor(rng: random.Random, sigma: float) -> float:
    """Multiplicative noise with mean 1."""
    if sigma <= 0:
        return 1.0
    return math.exp(rng.gauss(-sigma * sigma / 2, sigma))


# ---------------------------------------------------------------- mouse


def landing_offset(rng: random.Random, radius: float) -> Point:
    if radius <= 0:
        return (0, 0)
    r = min(radius, abs(rng.gauss(0, radius / 2)))
    theta = rng.uniform(0, 2 * math.pi)
    return (round(r * math.cos(theta)), round(r * math.sin(theta)))


def should_overshoot(rng: random.Random, distance: float, s: MouseSettings) -> bool:
    return (
        s.overshoot_enabled
        and s.overshoot_max_px > 0
        and distance >= s.overshoot_min_distance
        and rng.random() < s.overshoot_probability
    )


def overshoot_point(rng: random.Random, start: Point, target: Point, max_px: float) -> Point:
    """A point slightly past ``target`` along the direction of travel, with some sideways error."""
    dx, dy = target[0] - start[0], target[1] - start[1]
    dist = math.hypot(dx, dy)
    if dist == 0 or max_px <= 0:
        return target
    ux, uy = dx / dist, dy / dist
    along = min(rng.uniform(0.35, 1.0) * max_px, dist * 0.15)
    side = rng.gauss(0, along * 0.3)
    return (round(target[0] + ux * along - uy * side), round(target[1] + uy * along + ux * side))


@dataclass(frozen=True)
class PathParams:
    gravity: float
    wind: float
    max_step: float
    damped_distance: float
    tick_delay: float


def path_params(s: MouseSettings, distance: float, speed: float = 1.0) -> PathParams:
    """WindMouse parameters for one segment.

    ``max_step`` grows sub-linearly with distance so duration grows roughly
    logarithmically, as in Fitts' law; ``speed`` is split evenly between step size and tick delay.
    """
    speed = max(speed, 0.01)
    ratio = max(distance, 1.0) / REFERENCE_DISTANCE_PX
    distance_scale = min(DISTANCE_SCALE_MAX, max(DISTANCE_SCALE_MIN, ratio**DISTANCE_EXPONENT))
    root = math.sqrt(speed)
    step_scale = distance_scale * root
    return PathParams(
        gravity=s.gravity,
        wind=s.wind,
        max_step=max(1.0, s.max_step * step_scale),
        # Large steps with a small damping zone make WindMouse orbit the target.
        damped_distance=s.damped_distance * max(1.0, step_scale),
        tick_delay=s.tick_delay / root,
    )


def correction_params(params: PathParams) -> PathParams:
    """Short, calm, low-wind segment used after an overshoot."""
    return PathParams(
        gravity=params.gravity,
        wind=params.wind * 0.25,
        max_step=max(2.0, params.max_step * 0.4),
        damped_distance=params.damped_distance,
        tick_delay=params.tick_delay * 1.2,
    )


def generate_path(start: Point, end: Point, params: PathParams) -> list[Point]:
    """WindMouse path (via the ``windmouse`` package) from ``start`` to ``end``, excluding ``start``.

    Seeding: ``windmouse`` draws from NumPy's global RNG (``numpy.random.seed``).
    """
    if start == end:
        return []
    points: list[Point] = []
    for x, y in wind_mouse(
        start[0],
        start[1],
        end[0],
        end[1],
        params.gravity,
        params.wind,
        params.max_step,
        params.damped_distance,
    ):
        point = (int(x), int(y))
        if not points or points[-1] != point:
            points.append(point)
        if len(points) >= MAX_PATH_POINTS:
            break
    if not points or points[-1] != end:
        points.append(end)
    return points


def apply_arc(rng: random.Random, path: list[Point], start: Point, strength: float) -> list[Point]:
    """Bend a path sideways by ``A * sin(pi * t)`` (t = progress along the chord), like a wrist arc.

    ``A`` is drawn from N(0, strength * distance) and capped at 3 standard deviations.
    The endpoints are unchanged.
    """
    if strength <= 0 or len(path) < 3:
        return path
    end = path[-1]
    dx, dy = end[0] - start[0], end[1] - start[1]
    dist = math.hypot(dx, dy)
    if dist < 1:
        return path
    ux, uy = dx / dist, dy / dist
    sd = strength * dist
    amplitude = max(-3 * sd, min(3 * sd, rng.gauss(0, sd)))
    bent: list[Point] = []
    for x, y in path[:-1]:
        t = min(1.0, max(0.0, ((x - start[0]) * ux + (y - start[1]) * uy) / dist))
        offset = amplitude * math.sin(math.pi * t)
        point = (round(x - uy * offset), round(y + ux * offset))
        if not bent or bent[-1] != point:
            bent.append(point)
    if bent and bent[-1] == end:
        bent.pop()
    bent.append(end)
    return bent


def ease_multipliers(n: int, floor: float = 0.25, peak_at: float = 0.42) -> list[float]:
    """Per-step delay multipliers (mean 1) for a bell-shaped speed profile.

    Speed rises quickly, peaks a bit before the midpoint, and tapers into the target.
    """
    if n <= 2:
        return [1.0] * n
    warp = math.log(0.5) / math.log(peak_at)
    raw = []
    for i in range(n):
        t = (i + 0.5) / n
        velocity = floor + (1 - floor) * math.sin(math.pi * t**warp)
        raw.append(1.0 / velocity)
    mean = sum(raw) / n
    return [r / mean for r in raw]


def path_delays(rng: random.Random, n: int, tick_delay: float, ease: bool) -> list[float]:
    multipliers = ease_multipliers(n) if ease else [1.0] * n
    return [tick_delay * m * rng.uniform(0.85, 1.15) for m in multipliers]


# ---------------------------------------------------------------- scroll


def scroll_gaps(rng: random.Random, ticks: int, s: ScrollSettings) -> list[float]:
    """Gaps (seconds) between consecutive wheel notches.

    Notches come in flicks of a few notches; within a flick the gaps start long,
    shorten, then lengthen again. Between flicks there is a finger "regrip" pause.
    """
    if ticks <= 1:
        return []
    gaps: list[float] = []
    remaining = ticks
    while remaining > 0:
        lo, hi = s.flick_notches
        flick = min(remaining, max(1, round(rng.uniform(lo, hi))))
        for i in range(flick - 1):
            base = sample_ms(rng, s.tick_gap_ms)
            if s.ramp and flick > 2:
                t = (i + 0.5) / (flick - 1)
                base *= 0.6 + 0.9 * abs(2 * t - 1) ** 2
            gaps.append(base)
        remaining -= flick
        if remaining > 0:
            gaps.append(sample_ms(rng, s.regrip_pause_ms))
    return gaps


# ---------------------------------------------------------------- typing

SHIFTED_SYMBOLS = frozenset('~!@#$%^&*()_+{}|:"<>?')
PUNCTUATION = frozenset(".,;:!?")
WORD_BREAKS = frozenset(" \n\t")

_QWERTY_ROWS: tuple[tuple[str, float], ...] = (
    ("1234567890", 0.0),
    ("qwertyuiop", 0.5),
    ("asdfghjkl", 0.75),
    ("zxcvbnm", 1.25),
)
LEFT_HAND = frozenset("12345qwertasdfgzxcvb")

COMMON_BIGRAMS = frozenset(
    "th he in er an re on at en nd ti es or te of ed is it al ar st to nt ng se ha as ou io le ve co me "
    "de hi ri ro ic ne ea ra ce li ch ll be ma si om ur".split()
)


def _build_neighbours() -> dict[str, tuple[str, ...]]:
    positions: dict[str, tuple[float, float]] = {}
    for row, (keys, offset) in enumerate(_QWERTY_ROWS):
        for col, key in enumerate(keys):
            positions[key] = (col + offset, float(row))
    neighbours: dict[str, tuple[str, ...]] = {}
    for key, (x, y) in positions.items():
        near = [
            other
            for other, (ox, oy) in positions.items()
            if other != key and abs(oy - y) <= 1 and math.hypot(ox - x, oy - y) <= 1.3
        ]
        neighbours[key] = tuple(sorted(near))
    return neighbours


QWERTY_NEIGHBOURS = _build_neighbours()


def needs_shift(char: str) -> bool:
    return char.isupper() or char in SHIFTED_SYMBOLS


def bigram_factor(prev: str | None, char: str, speedup: float) -> float:
    if prev is None:
        return 1.0
    pair = (prev + char).lower()
    if pair in COMMON_BIGRAMS:
        return speedup
    a, b = prev.lower(), char.lower()
    if a.isalpha() and b.isalpha() and (a in LEFT_HAND) != (b in LEFT_HAND):
        return (1.0 + speedup) / 2
    return 1.0


def typo_for(rng: random.Random, char: str) -> str | None:
    options = QWERTY_NEIGHBOURS.get(char.lower())
    if not options:
        return None
    wrong = rng.choice(options)
    return wrong.upper() if char.isupper() else wrong


@dataclass(frozen=True)
class KeyStep:
    """One keystroke. ``char`` is the character to produce; ``"\\b"`` means Backspace.

    ``wait`` is the idle time before pressing, ``shift_lead`` the extra time between
    pressing Shift and the key (only used when the key needs Shift), ``hold`` the
    key-down duration.
    """

    char: str
    wait: float
    hold: float
    shift_lead: float = 0.0
    typo: bool = False
    correction: bool = False

    @property
    def duration(self) -> float:
        return self.wait + self.shift_lead + self.hold


BACKSPACE = "\b"


def _context_extra(rng: random.Random, prev: str | None, char: str, s: TypingSettings) -> float:
    extra = 0.0
    if prev is None:
        return extra
    if prev == "\n":
        extra += sample_ms(rng, s.newline_pause_ms)
    elif prev in PUNCTUATION:
        extra += sample_ms(rng, s.punctuation_pause_ms)
    elif prev == " " and char not in WORD_BREAKS:
        extra += sample_ms(rng, s.space_pause_ms)
    if prev in WORD_BREAKS and char not in WORD_BREAKS and rng.random() < s.think_pause_probability:
        extra += sample_ms(rng, s.think_pause_ms)
    return extra


def _mean(bounds: Range) -> float:
    return (bounds[0] + bounds[1]) / 2000.0


def _expected_overhead(text: str, s: TypingSettings, typo_rate: float, base: float) -> tuple[float, float]:
    """Expected extra seconds from context pauses/shift/typos, and the sum of bigram factors."""
    extra = 0.0
    factors = 0.0
    prev: str | None = None
    for char in text:
        factors += bigram_factor(prev, char, s.bigram_speedup)
        if prev == "\n":
            extra += _mean(s.newline_pause_ms)
        elif prev is not None and prev in PUNCTUATION:
            extra += _mean(s.punctuation_pause_ms)
        elif prev == " " and char not in WORD_BREAKS:
            extra += _mean(s.space_pause_ms)
        if prev is not None and prev in WORD_BREAKS and char not in WORD_BREAKS:
            extra += s.think_pause_probability * _mean(s.think_pause_ms)
        if needs_shift(char):
            extra += _mean(s.shift_extra_ms)
        if char.lower() in QWERTY_NEIGHBOURS:
            extra += typo_rate * (_mean(s.typo_notice_ms) + 3.0 * base)
        prev = char
    return extra, factors


def char_interval_unit(text: str, s: TypingSettings, wpm: float, typo_rate: float) -> float:
    """Mean press-to-press interval for a plain keystroke, calibrated so the whole
    text (pauses, Shift, typos included) averages out to ``wpm`` (5 chars per word)."""
    base = 12.0 / wpm
    if not text:
        return base
    extra, factors = _expected_overhead(text, s, typo_rate, base)
    return max(base * 0.35, (len(text) * base - extra) / max(factors, 1e-9))


def plan_typing(
    rng: random.Random,
    text: str,
    s: TypingSettings,
    *,
    wpm: float | None = None,
    typo_rate: float | None = None,
    enabled: bool = True,
) -> list[KeyStep]:
    """Plan every keystroke (including typos and their Backspace corrections) for ``text``."""
    if not enabled:
        return [KeyStep(char=c, wait=0.0, hold=0.0) for c in text]
    wpm = s.wpm if wpm is None else wpm
    rate = s.typo_rate if typo_rate is None else typo_rate
    unit = char_interval_unit(text, s, wpm, rate)
    steps: list[KeyStep] = []
    prev_hold = 0.0

    def emit(char: str, prev: str | None, *, interval_scale: float = 1.0, extra: float = 0.0, **flags: bool) -> None:
        nonlocal prev_hold
        interval = unit * bigram_factor(prev, char, s.bigram_speedup) * lognormal_factor(rng, s.wpm_variance)
        interval = interval * interval_scale + extra
        shift_lead = sample_ms(rng, s.shift_extra_ms) if needs_shift(char) else 0.0
        hold = sample_ms(rng, s.key_hold_ms)
        wait = interval if not steps else max(MIN_KEY_GAP_S, interval - prev_hold)
        steps.append(KeyStep(char=char, wait=wait, hold=hold, shift_lead=shift_lead, **flags))
        prev_hold = hold

    i = 0
    no_typo_at: set[int] = set()
    n = len(text)
    while i < n:
        char = text[i]
        prev = text[i - 1] if i > 0 else None
        extra = _context_extra(rng, prev, char, s)
        if i not in no_typo_at and rate > 0 and char.lower() in QWERTY_NEIGHBOURS and rng.random() < rate:
            no_typo_at.add(i)
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt.isalpha() and char.isalpha() and nxt.lower() != char.lower() and rng.random() < 0.2:
                emit(nxt, prev, extra=extra, typo=True)
                emit(char, nxt, typo=True)
                erase = 2
            else:
                wrong = typo_for(rng, char) or char
                emit(wrong, prev, extra=extra, typo=True)
                ahead = 0
                if rng.random() < 0.35:
                    ahead = rng.choice((1, 1, 2))
                    while ahead and any(c in WORD_BREAKS for c in text[i + 1 : i + 1 + ahead]):
                        ahead -= 1
                    ahead = min(ahead, n - i - 1)
                last = wrong
                for c in text[i + 1 : i + 1 + ahead]:
                    emit(c, last, typo=True)
                    last = c
                erase = 1 + ahead
            for k in range(erase):
                notice = sample_ms(rng, s.typo_notice_ms) if k == 0 else 0.0
                emit(BACKSPACE, None, interval_scale=0.75 if k else 0.5, extra=notice, correction=True)
            emit(char, prev, extra=unit * 0.3)
            i += 1
            continue
        emit(char, prev, extra=extra)
        i += 1
    return steps


def plan_duration(steps: list[KeyStep]) -> float:
    return sum(step.duration for step in steps)


def effective_wpm(text: str, steps: list[KeyStep]) -> float:
    total = plan_duration(steps)
    return (len(text) / 5.0) / (total / 60.0) if total > 0 else float("inf")


def apply_keystrokes(steps: list[KeyStep]) -> str:
    """Simulate the text an editor would end up with (used by tests and sanity checks)."""
    out: list[str] = []
    for step in steps:
        if step.char == BACKSPACE:
            if out:
                out.pop()
        else:
            out.append(step.char)
    return "".join(out)
