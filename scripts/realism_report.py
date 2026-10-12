"""Simulated realism report: mouse durations/speed profile and effective typing WPM.

No real input is sent. Usage:
    venv\\Scripts\\python.exe scripts\\realism_report.py [--overhead-ms 0.3]

``--overhead-ms`` is the real per-point cost of ``pyautogui.moveTo`` on this machine
(the live test prints it); it is added to the simulated sleep time.
"""

from __future__ import annotations

import argparse
import math
import random
import statistics

import numpy as np

from humanlike_mcp import humanize
from humanlike_mcp.config import PRESETS, Settings
from humanlike_mcp.mouse import Mouse
from humanlike_mcp.screen import Bounds

TEXT = (
    "The quick brown fox jumps over the lazy dog. Typing like a person means small pauses, "
    "occasional mistakes, and a rhythm that speeds up on familiar letter pairs. "
    "Hello, World! Meeting moved to 3:30 PM, see you there.\n"
    "Please review the attached report and send feedback by Friday."
)


class SimBackend:
    def __init__(self, start: tuple[int, int]) -> None:
        self.pos = start
        self.trace: list[tuple[int, int]] = []

    def position(self) -> tuple[int, int]:
        return self.pos

    def move_to(self, x: int, y: int) -> None:
        self.pos = (x, y)
        self.trace.append((x, y))

    def button_down(self, button: str) -> None: ...
    def button_up(self, button: str, *, force: bool = False) -> None: ...
    def scroll(self, dx: int, dy: int) -> None: ...

    def bounds(self) -> Bounds:
        return Bounds(-5000, -5000, 10000, 10000)

    def failsafe_points(self) -> list[tuple[int, int]]:
        return []

    def set_failsafe(self, enabled: bool) -> None: ...
    def check_failsafe(self) -> None: ...


def simulate_move(distance: float, s: Settings, seed: int, overhead: float) -> tuple[float, list[float], list[tuple[int, int]], bool]:
    rng = random.Random(seed)
    np.random.seed(seed)
    sleeps: list[float] = []
    angle = rng.uniform(0, 2 * math.pi)
    backend = SimBackend((0, 0))
    mouse = Mouse(backend, rng=rng, sleep=sleeps.append)
    out = mouse.move(distance * math.cos(angle), distance * math.sin(angle), s)
    total = sum(sleeps) + overhead * len(backend.trace)
    return total, sleeps, backend.trace, out["overshoot"]


def pct(values: list[float], p: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(p * len(values)))]


def mouse_report(name: str, s: Settings, overhead: float) -> None:
    print(f"\n== mouse ({name}) ==  distance: median [p10-p90] ms, points, overshoot%")
    for distance in (100, 250, 500, 800, 1200, 1800):
        runs = [simulate_move(distance, s, seed, overhead) for seed in range(200)]
        durations = [r[0] * 1000 for r in runs]
        points = statistics.median(len(r[2]) for r in runs)
        over = 100 * sum(r[3] for r in runs) / len(runs)
        print(
            f"  {distance:5d}px: {statistics.median(durations):6.0f} [{pct(durations, 0.1):4.0f}-{pct(durations, 0.9):4.0f}]"
            f"  pts={points:4.0f}  overshoot={over:4.1f}%"
        )


def speed_profile(s: Settings, overhead: float) -> None:
    bins = [0.0] * 10
    counts = [0] * 10
    for seed in range(100):
        _, sleeps, trace, _ = simulate_move(700, s.with_updates({"mouse.overshoot_enabled": False}), seed, overhead)
        prev = (0, 0)
        total_len = sum(math.dist(a, b) for a, b in zip([(0, 0)] + trace, trace))
        travelled = 0.0
        for point, delay in zip(trace, sleeps):
            step = math.dist(prev, point)
            travelled += step
            b = min(9, int(10 * travelled / max(total_len, 1)))
            bins[b] += step / (delay + overhead)
            counts[b] += 1
            prev = point
    velocity = [bins[i] / max(counts[i], 1) for i in range(10)]
    peak = max(velocity)
    print("\n== speed profile, 700 px move (px/s by 10% of path) ==")
    for i, v in enumerate(velocity):
        print(f"  {i * 10:3d}-{i * 10 + 10:3d}%: {v:7.0f}  {'#' * round(40 * v / peak)}")


def typing_report() -> None:
    print("\n== typing: configured vs effective WPM (mean of 50 plans, typos & pauses included) ==")
    for name in PRESETS:
        s = Settings().with_updates(PRESETS[name])
        if not s.humanize.enabled:
            continue
        rates = [
            humanize.effective_wpm(TEXT, humanize.plan_typing(random.Random(seed), TEXT, s.typing)) for seed in range(50)
        ]
        mean = statistics.mean(rates)
        err = 100 * (mean - s.typing.wpm) / s.typing.wpm
        print(
            f"  {name:8s}: target {s.typing.wpm:5.0f}  effective {mean:5.1f} ({err:+.1f}%)"
            f"  range {min(rates):.0f}-{max(rates):.0f}"
        )
    s = Settings().typing
    steps = humanize.plan_typing(random.Random(1), TEXT, s)
    intervals = [st.wait + st.hold for st in steps if not st.typo and not st.correction]
    print(
        f"  natural keystroke interval: median {statistics.median(intervals) * 1000:.0f} ms,"
        f" p10 {pct(intervals, 0.1) * 1000:.0f} ms, p90 {pct(intervals, 0.9) * 1000:.0f} ms"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overhead-ms", type=float, default=0.3)
    args = parser.parse_args()
    overhead = args.overhead_ms / 1000
    for name in ("natural", "careful", "fast"):
        mouse_report(name, Settings().with_updates(PRESETS[name]), overhead)
    speed_profile(Settings(), overhead)
    typing_report()


if __name__ == "__main__":
    main()
