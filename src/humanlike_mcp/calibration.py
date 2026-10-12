"""Measure a model's click-coordinate convention from a synthetic target image (pure; no screen access)."""

from __future__ import annotations

import io
import math
import statistics
from dataclasses import dataclass, replace
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from .screen import Bounds, Calibration

TARGET_FRACTIONS: tuple[tuple[float, float], ...] = (
    (0.08, 0.12),
    (0.90, 0.09),
    (0.50, 0.50),
    (0.14, 0.86),
    (0.83, 0.78),
    (0.64, 0.30),
)
RESIZE_FACTORS = (28, 32)
RESIZE_MAX_PIXELS: tuple[int | None, ...] = (None, 1003520, 802816, 602112, 401408, 200704)
RESIZE_MIN_PIXELS = 3136


class CalibrationError(ValueError):
    """The reported points don't fit any consistent linear mapping."""


@dataclass(frozen=True)
class Session:
    """One calibration image: its size, the screen area it stands for, and the true target centres."""

    width: int
    height: int
    region: Bounds
    targets: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Hypothesis:
    name: str
    space: str
    calibration: Calibration
    max_error_px: float
    rms_error_px: float
    errors_px: tuple[float, ...]

    def summary(self) -> dict[str, object]:
        return {
            "name": self.name,
            "coordinate_space": self.space,
            "max_error_px": round(self.max_error_px, 2),
            "rms_error_px": round(self.rms_error_px, 2),
        }


@dataclass(frozen=True)
class FitResult:
    chosen: Hypothesis
    fitted: Hypothesis
    hypotheses: tuple[Hypothesis, ...]
    tolerance_px: float
    quality: str = "good"
    usable_tolerance_px: float = 0.0
    dropped: int | None = None


def make_session(width: int, height: int, region: Bounds) -> Session:
    targets = tuple(
        (min(width - 1, round(fx * width)), min(height - 1, round(fy * height))) for fx, fy in TARGET_FRACTIONS
    )
    return Session(width, height, region, targets)


def render(session: Session, image_format: str = "png") -> bytes:
    """Dark background with numbered crosshair targets; the centre pixel of each crosshair is the target."""
    w, h = session.width, session.height
    image = Image.new("RGB", (w, h), (30, 34, 42))
    draw = ImageDraw.Draw(image)
    radius = max(8, round(min(w, h) * 0.03))
    font = ImageFont.load_default(size=max(12, round(radius * 1.4)))
    banner = ImageFont.load_default(size=max(10, round(min(w, h) * 0.025)))
    draw.text((w // 2, round(h * 0.95)), "CALIBRATION - not the real screen", fill=(150, 150, 160), font=banner, anchor="mm")
    for number, (x, y) in enumerate(session.targets, start=1):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(255, 255, 255), outline=(230, 30, 30), width=max(2, radius // 4))
        inner = max(3, radius // 2)
        draw.ellipse((x - inner, y - inner, x + inner, y + inner), outline=(230, 30, 30), width=max(1, radius // 8))
        reach = round(radius * 1.7)
        draw.line((x - reach, y, x + reach, y), fill=(255, 220, 0), width=1)
        draw.line((x, y - reach, x, y + reach), fill=(255, 220, 0), width=1)
        draw.line((x - radius, y, x + radius, y), fill=(0, 0, 0), width=1)
        draw.line((x, y - radius, x, y + radius), fill=(0, 0, 0), width=1)
        text = str(number)
        left_side = x > w * 0.7
        anchor = "rb" if left_side else "lb"
        pos = (x - reach if left_side else x + reach, y - radius // 2)
        if y < h * 0.2:
            anchor = anchor[0] + "t"
            pos = (pos[0], y + radius // 2)
        box = draw.textbbox(pos, text, font=font, anchor=anchor)
        draw.rectangle((box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3), fill=(0, 0, 0))
        draw.text(pos, text, fill=(255, 220, 0), font=font, anchor=anchor)
    buffer = io.BytesIO()
    if image_format == "jpeg":
        image.save(buffer, format="JPEG", quality=90)
    else:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def smart_resize(height: int, width: int, factor: int, max_pixels: int | None) -> tuple[int, int]:
    """Qwen-VL style resize: both sides multiples of ``factor``, area within the pixel budget. Returns (w, h)."""
    h_bar = max(factor, round(height / factor) * factor)
    w_bar = max(factor, round(width / factor) * factor)
    if max_pixels is not None and h_bar * w_bar > max_pixels:
        beta = math.sqrt(height * width / max_pixels)
        h_bar = max(factor, math.floor(height / beta / factor) * factor)
        w_bar = max(factor, math.floor(width / beta / factor) * factor)
    elif h_bar * w_bar < RESIZE_MIN_PIXELS:
        beta = math.sqrt(RESIZE_MIN_PIXELS / (height * width))
        h_bar = math.ceil(height * beta / factor) * factor
        w_bar = math.ceil(width * beta / factor) * factor
    return w_bar, h_bar


def _conventions(session: Session) -> list[tuple[str, str, Calibration]]:
    w, h, r = session.width, session.height, session.region
    out: list[tuple[str, str, Calibration]] = [
        ("pixels in the image", "screenshot", Calibration(1.0, 0.0, 1.0, 0.0, "image")),
        ("0-1000 normalized grid", "normalized", Calibration(0.001, 0.0, 0.001, 0.0, "absolute")),
        ("0-1 fractions", "calibrated", Calibration(1.0, 0.0, 1.0, 0.0, "absolute")),
        (
            f"physical screen pixels ({r.width}x{r.height})",
            "screen",
            Calibration(1 / r.width, -r.left / r.width, 1 / r.height, -r.top / r.height, "absolute"),
        ),
    ]
    seen = {(w, h)}
    for factor in RESIZE_FACTORS:
        for max_pixels in RESIZE_MAX_PIXELS:
            rw, rh = smart_resize(h, w, factor, max_pixels)
            if (rw, rh) in seen:
                continue
            seen.add((rw, rh))
            budget = f", max_pixels {max_pixels}" if max_pixels else ""
            out.append(
                (
                    f"pixels of the image resized to {rw}x{rh} (smart_resize factor {factor}{budget})",
                    "calibrated",
                    Calibration(w / rw, 0.0, h / rh, 0.0, "image"),
                )
            )
    return out


def _score(session: Session, name: str, space: str, cal: Calibration, points: Sequence[tuple[float, float]]) -> Hypothesis:
    errors = []
    for (rx, ry), (tx, ty) in zip(points, session.targets):
        u, v = cal.to_fraction(rx, ry, session.width, session.height)
        errors.append(math.hypot(u * session.width - 0.5 - tx, v * session.height - 0.5 - ty))
    rms = math.sqrt(sum(e * e for e in errors) / len(errors))
    return Hypothesis(name, space, cal, max(errors), rms, tuple(errors))


def _line(xs: Sequence[float], ts: Sequence[float]) -> tuple[float, float]:
    n = len(xs)
    mx, mt = sum(xs) / n, sum(ts) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var < 1e-12:
        raise CalibrationError("All reported points have the same x or the same y, so no mapping can be fitted.")
    a = sum((x - mx) * (t - mt) for x, t in zip(xs, ts)) / var
    return a, mt - a * mx


def _least_squares(session: Session, points: Sequence[tuple[float, float]], units: str) -> Calibration:
    w, h = session.width, session.height
    if units == "image":
        xs = [(x + 0.5) / w for x, _ in points]
        ys = [(y + 0.5) / h for _, y in points]
    else:
        xs = [x for x, _ in points]
        ys = [y for _, y in points]
    sx, ox = _line(xs, [(tx + 0.5) / w for tx, _ in session.targets])
    sy, oy = _line(ys, [(ty + 0.5) / h for _, ty in session.targets])
    if abs(sx) < 1e-9 or abs(sy) < 1e-9:
        raise CalibrationError("The reported points don't change with the target positions.")
    return Calibration(round(sx, 6), round(ox, 6), round(sy, 6), round(oy, 6), units)


def tolerance_px(session: Session) -> float:
    return max(12.0, 0.015 * max(session.width, session.height))


def usable_tolerance_px(session: Session) -> float:
    """Largest residual still worth applying: far better than an uncalibrated convention mismatch."""
    return max(24.0, 0.03 * max(session.width, session.height))


def _check_points(session: Session, points: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
    n = len(session.targets)
    if len(points) != n:
        raise CalibrationError(f"Expected {n} points, one per target 1..{n} in order, got {len(points)}.")
    clean: list[tuple[float, float]] = []
    for i, p in enumerate(points, start=1):
        if len(p) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in p):
            raise CalibrationError(f"Point {i} must be [x, y] with two finite numbers, got {list(p)!r}.")
        clean.append((float(p[0]), float(p[1])))
    return clean


def _units(named: Sequence[Hypothesis], points: Sequence[tuple[float, float]]) -> str:
    if named[0].calibration.units == "absolute" or max(abs(v) for p in points for v in p) <= 1.5:
        return "absolute"
    return "image"


def _leave_one_out(
    session: Session, points: list[tuple[float, float]], units: str, tol: float
) -> tuple[int, Hypothesis] | None:
    """Best fit that ignores one point, if every other point then fits and the ignored one clearly doesn't."""
    if len(points) < 5:
        return None
    best: tuple[int, Hypothesis] | None = None
    for i in range(len(points)):
        rest = points[:i] + points[i + 1 :]
        sub = replace(session, targets=session.targets[:i] + session.targets[i + 1 :])
        try:
            cal = _least_squares(sub, rest, units)
        except CalibrationError:
            continue
        on_rest = _score(sub, "per-axis linear fit", "calibrated", cal, rest)
        on_all = _score(session, "per-axis linear fit", "calibrated", cal, points)
        if on_rest.max_error_px <= tol and on_all.errors_px[i] > 2 * tol:
            if best is None or on_rest.max_error_px < best[1].max_error_px:
                best = (i, replace(on_all, max_error_px=on_rest.max_error_px, rms_error_px=on_rest.rms_error_px))
    return best


AXIS_SCALES = {
    "screenshot": lambda session, axis: f"0 to {(session.width if axis == 'x' else session.height) - 1} pixels",
    "normalized": lambda session, axis: "0 to 1000 (the far edge is 1000 even though the image is not square)",
}


def axis_mismatch(session: Session, raw_points: Sequence[Sequence[float]]) -> str | None:
    """Guidance when one axis matches a built-in convention but the other doesn't (a mixed convention)."""
    points = _check_points(session, raw_points)
    tol = tolerance_px(session)
    for name, space, cal in _conventions(session):
        if space not in AXIS_SCALES:
            continue
        ex, ey = [], []
        for (rx, ry), (tx, ty) in zip(points, session.targets):
            u, v = cal.to_fraction(rx, ry, session.width, session.height)
            ex.append(abs(u * session.width - 0.5 - tx))
            ey.append(abs(v * session.height - 0.5 - ty))
        for good, bad, errors in (("x", "y", ey), ("y", "x", ex)):
            if max(ex if good == "x" else ey) <= tol / 2 and max(errors) > tol:
                left, right = ("top", "bottom") if bad == "y" else ("left", "right")
                return (
                    f"Your {good} values match the {name} exactly, but your {bad} values do not (off by up to "
                    f"{max(errors):.0f} px). Use the same convention for {bad}: it runs "
                    f"{AXIS_SCALES[space](session, bad)} from the {left} edge to the {right} edge. Re-report all "
                    f"points with {bad} on the same scale as {good} and call calibrate_submit again; nothing was "
                    "changed."
                )
    return None


def _prefer_named(scored: Sequence[Hypothesis], fitted: Hypothesis, limit: float, slack: float) -> Hypothesis:
    near = [hyp for hyp in scored if hyp.max_error_px <= min(limit, fitted.max_error_px + slack)]
    return near[0] if near else fitted


def _robust_named(scored: Sequence[Hypothesis], tol: float) -> tuple[Hypothesis, int | None] | None:
    """A built-in convention that hits most targets almost exactly, missing at most one.

    A free fit through a few noisy points overfits; a convention with no free parameters that matches the rest
    generalises better to real clicks.
    """
    for hyp in scored:
        misses = [i for i, e in enumerate(hyp.errors_px) if e > tol]
        if len(misses) > 1 or statistics.median(hyp.errors_px) > tol / 4:
            continue
        rest = [e for i, e in enumerate(hyp.errors_px) if i not in misses]
        rms = math.sqrt(sum(e * e for e in rest) / len(rest))
        return replace(hyp, max_error_px=max(rest), rms_error_px=rms), (misses[0] if misses else None)
    return None


def fit(session: Session, raw_points: Sequence[Sequence[float]]) -> FitResult:
    """Compare known conventions and a free per-axis linear fit; return the one to use.

    ``quality`` is "good" when the residual is within ``tolerance_px`` (possibly after ignoring one clear
    outlier) and "usable" when it is within ``usable_tolerance_px``. Raises ``CalibrationError`` beyond that.
    """
    points = _check_points(session, raw_points)
    tol, usable = tolerance_px(session), usable_tolerance_px(session)
    slack = max(3.0, 0.005 * max(session.width, session.height))
    scored = [_score(session, name, space, cal, points) for name, space, cal in _conventions(session)]
    named = tuple(sorted(scored, key=lambda hyp: hyp.max_error_px))
    units = _units(named, points)
    fitted = _score(session, "per-axis linear fit", "calibrated", _least_squares(session, points, units), points)
    if fitted.max_error_px <= tol:
        chosen = _prefer_named(scored, fitted, tol, slack)
        if chosen is not fitted:
            return FitResult(chosen, fitted, named, tol, "good", usable)
    robust = _robust_named(scored, tol)
    if robust is not None:
        hyp, miss = robust
        return FitResult(hyp, fitted, named, tol, "good", usable, dropped=None if miss is None else miss + 1)
    if fitted.max_error_px <= tol:
        return FitResult(fitted, fitted, named, tol, "good", usable)
    outlier = _leave_one_out(session, points, units, tol)
    if outlier is not None:
        index, hyp = outlier
        sub = replace(session, targets=session.targets[:index] + session.targets[index + 1 :])
        rest = points[:index] + points[index + 1 :]
        on_rest = [_score(sub, name, space, cal, rest) for name, space, cal in _conventions(session)]
        best = _prefer_named(on_rest, hyp, tol, slack)
        if best is not hyp:
            full = _score(session, best.name, best.space, best.calibration, points)
            best = replace(full, max_error_px=best.max_error_px, rms_error_px=best.rms_error_px)
        return FitResult(best, hyp, named, tol, "good", usable, dropped=index + 1)
    if fitted.max_error_px <= usable:
        return FitResult(_prefer_named(scored, fitted, usable, slack), fitted, named, tol, "usable", usable)
    swapped = [(y, x) for x, y in points]
    try:
        swapped_fit = _score(session, "swapped", "calibrated", _least_squares(session, swapped, units), swapped)
    except CalibrationError:
        swapped_fit = None
    if swapped_fit is not None and swapped_fit.max_error_px <= usable:
        raise CalibrationError(
            "The points fit well only with x and y swapped: you reported [y, x]. "
            "Report each target as [x, y] (horizontal first) and call calibrate_submit again."
        )
    per_target = ", ".join(f"{i}: {e:.0f}px" for i, e in enumerate(fitted.errors_px, start=1))
    raise CalibrationError(
        f"The reported points are inconsistent: even the best linear mapping misses by up to "
        f"{fitted.max_error_px:.0f} px (usable up to {usable:.0f} px; per target {per_target}). Nothing was "
        "changed. Check that the points are in target order 1..N, that each marks the centre of its crosshair, "
        "and that x and y use the same convention; do not resubmit the same points. If you estimated pixel "
        "positions, point at the targets on a 0-1000 grid instead (0,0 = top-left corner, 1000,1000 = "
        "bottom-right corner on both axes), answering directly from the calibration image."
    )
