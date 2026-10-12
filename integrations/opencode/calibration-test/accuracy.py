"""Offline click-accuracy report for calibration runs (reads the debug log; sends no input).

Every point the model reported for a calibration target is exactly what it would pass to click, so each
applied calibration is cross-validated by mapping the *other* runs' points to screen pixels and comparing
them with the true target centres. Hit rates are given for typical UI element sizes.

Usage: venv\\Scripts\\python.exe integrations/opencode/calibration-test/accuracy.py [log] [--replay]
(--replay re-judges every logged submission with the current fitting code instead.)
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

from humanlike_mcp import calibration, screen

ELEMENTS = {"16 px icon": (16, 16), "32 px icon": (32, 32), "button 100x32": (100, 32), "text field 300x40": (300, 40)}


def load(path: Path) -> list[dict[str, Any]]:
    """One entry per calibrate image: its view, true target centres and every submission for it."""
    sessions: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["event"] == "calibrate":
            region = screen.Bounds(**rec["region"])
            sessions.append({"view": screen.View(region, *rec["image"]), "targets": rec["targets"], "submits": []})
        elif rec["event"] == "calibrate_submit" and sessions:
            sessions[-1]["submits"].append(rec)
    return sessions


def to_screen(view: screen.View, space: str, cal: screen.Calibration, x: float, y: float) -> tuple[float, float]:
    if space == "normalized":
        return view.from_normalized(x, y)
    if space == "calibrated":
        return view.from_calibrated(cal, x, y)
    return view.to_screen(x, y)


def errors(view: screen.View, space: str, cal: screen.Calibration, points, targets) -> list[tuple[float, float]]:
    out = []
    for (x, y), (tx, ty) in zip(points, targets):
        sx, sy = to_screen(view, space, cal, x, y)
        truth = view.to_screen(tx, ty)
        out.append((sx - truth[0], sy - truth[1]))
    return out


def hit_rate(errs: list[tuple[float, float]], w: int, h: int) -> float:
    return sum(abs(dx) <= w / 2 and abs(dy) <= h / 2 for dx, dy in errs) / len(errs)


def summary(label: str, errs: list[tuple[float, float]]) -> str:
    dist = [math.hypot(dx, dy) for dx, dy in errs]
    hits = ", ".join(f"{name} {hit_rate(errs, *size):.0%}" for name, size in ELEMENTS.items())
    return (
        f"| {label} | {len(dist)} | {statistics.median(dist):.0f} | {max(dist):.0f} | {hits} |"
    )


def replay(sessions: list[dict[str, Any]]) -> None:
    """Judge every logged submission with the current fitting code."""
    print("| image | submission | verdict | space | max_error_px | ignored |")
    print("|---|---|---|---|---|---|")
    for n, s in enumerate(sessions, start=1):
        view = s["view"]
        session = calibration.Session(view.width, view.height, view.region, tuple(map(tuple, s["targets"])))
        for k, sub in enumerate(s["submits"], start=1):
            try:
                result = calibration.fit(session, sub["points"])
            except calibration.CalibrationError:
                hint = calibration.axis_mismatch(session, sub["points"])
                print(f"| {n} | {k} | rejected{' (axis hint)' if hint else ''} | | | |")
                continue
            builtin = result.quality == "good" and result.chosen.space != "calibrated"
            hint = None if builtin else calibration.axis_mismatch(session, sub["points"])
            verdict = f"{result.quality}{' (axis hint first)' if hint else ''}"
            c = result.chosen
            print(f"| {n} | {k} | {verdict} | {c.space} | {c.max_error_px:.1f} | {result.dropped or ''} |")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = Path(args[0] if args else ".calib-runs/server.jsonl")
    sessions = load(path)
    if "--replay" in sys.argv:
        replay(sessions)
        return
    first_points = [(s, s["submits"][0]["points"]) for s in sessions if s["submits"]]
    applied = [
        (s, sub) for s in sessions for sub in s["submits"] if sub.get("calibration") and sub.get("quality") == "good"
    ]
    identity = screen.Calibration()

    def native(s: dict[str, Any], points: list[list[float]]) -> bool:
        limit = 0.03 * s["view"].region.width
        return max(math.hypot(*e) for e in errors(s["view"], "normalized", identity, points, s["targets"])) <= limit

    groups = {
        "all": first_points,
        "native 0-1000 pointing": [(s, p) for s, p in first_points if native(s, p)],
        "other modes": [(s, p) for s, p in first_points if not native(s, p)],
    }
    print(f"Screen {sessions[0]['view'].region.width}x{sessions[0]['view'].region.height}; errors in screen px.")
    print(f"First submissions per calibrate image: {len(first_points)}, native mode: {len(groups['native 0-1000 pointing'])}.")
    for group, members in groups.items():
        if not members:
            continue
        rows = [f"\n### {group}\n", "| mapping | clicks | median px | max px | hit rate |", "|---|---|---|---|---|"]
        for label, space in (("no calibration (default screenshot pixels)", "screenshot"), ("plain normalized", "normalized")):
            errs: list[tuple[float, float]] = []
            for s, points in members:
                errs += errors(s["view"], space, identity, points, s["targets"])
            rows.append(summary(label, errs))
        seen: set[tuple[str, tuple[float, ...]]] = set()
        for own, sub in applied:
            key = (sub["coordinate_space"], tuple(sub["calibration"]["values"]))
            if key in seen:
                continue
            seen.add(key)
            cal = screen.Calibration(*sub["calibration"]["values"], units=sub["calibration"]["units"])
            cross: list[tuple[float, float]] = []
            for s, points in members:
                if s is not own:
                    cross += errors(s["view"], sub["coordinate_space"], cal, points, s["targets"])
            if cross:
                rows.append(summary(f"applied {sub['coordinate_space']} {sub['calibration']['values']}", cross))
        print("\n".join(rows))


if __name__ == "__main__":
    main()
