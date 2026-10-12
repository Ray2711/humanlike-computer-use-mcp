"""Screenshots, screen geometry and DPI awareness."""

from __future__ import annotations

import ctypes
import io
import math
import sys
import threading
from dataclasses import dataclass
from typing import Any, Callable

import mss
from PIL import Image

_dpi_lock = threading.Lock()
_dpi_done = False


def enable_dpi_awareness() -> None:
    """Make the process per-monitor DPI aware so pyautogui, mss and pynput share physical pixels.

    Must run before pyautogui is imported (it sets a weaker awareness level on import).
    """
    global _dpi_done
    with _dpi_lock:
        if _dpi_done or sys.platform != "win32":
            _dpi_done = True
            return
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass
        _dpi_done = True


def _mss() -> Any:
    factory = getattr(mss, "MSS", None) or mss.mss
    return factory()


@dataclass(frozen=True)
class Bounds:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width - 1

    @property
    def bottom(self) -> int:
        return self.top + self.height - 1

    def clamp(self, x: float, y: float) -> tuple[int, int]:
        return (min(self.right, max(self.left, round(x))), min(self.bottom, max(self.top, round(y))))

    def as_dict(self) -> dict[str, int]:
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}


def _bounds(mon: dict[str, Any]) -> Bounds:
    return Bounds(int(mon["left"]), int(mon["top"]), int(mon["width"]), int(mon["height"]))


def list_monitors() -> list[Bounds]:
    """Index 0 is the whole virtual desktop; 1..N are individual monitors (mss convention)."""
    enable_dpi_awareness()
    with _mss() as sct:
        return [_bounds(m) for m in sct.monitors]


def virtual_bounds() -> Bounds:
    return list_monitors()[0]


def screen_info() -> dict[str, Any]:
    monitors = list_monitors()
    primary = monitors[1] if len(monitors) > 1 else monitors[0]
    return {
        "resolution": f"{primary.width}x{primary.height}",
        "width": primary.width,
        "height": primary.height,
        "virtual_desktop": monitors[0].as_dict(),
        "monitors": [{"index": i, **m.as_dict()} for i, m in enumerate(monitors[1:], start=1)],
    }


NORMALIZED_MAX = 1000
CALIBRATED_TOLERANCE = 0.002


@dataclass(frozen=True)
class Calibration:
    """Linear map from a model's reported coordinates to image fractions (0 = left/top edge, 1 = right/bottom).

    With ``units="image"`` reported values are first divided by the current image size (pixel centres),
    so pixel-like conventions keep working when the screenshot size changes; ``"absolute"`` uses them as-is
    (fixed grids such as 0-1000 or 0-1).
    """

    sx: float = 1.0
    ox: float = 0.0
    sy: float = 1.0
    oy: float = 0.0
    units: str = "image"

    def to_fraction(self, x: float, y: float, width: int, height: int) -> tuple[float, float]:
        if self.units == "image":
            x, y = (x + 0.5) / width, (y + 0.5) / height
        return self.sx * x + self.ox, self.sy * y + self.oy

    def from_fraction(self, u: float, v: float, width: int, height: int) -> tuple[float, float]:
        x, y = (u - self.ox) / self.sx, (v - self.oy) / self.sy
        if self.units == "image":
            x, y = x * width - 0.5, y * height - 0.5
        return x, y

    def as_list(self) -> list[float]:
        return [self.sx, self.ox, self.sy, self.oy]


@dataclass(frozen=True)
class View:
    """Mapping between a screenshot's image pixels and physical screen pixels."""

    region: Bounds
    width: int
    height: int

    @property
    def scale_x(self) -> float:
        return self.region.width / self.width

    @property
    def scale_y(self) -> float:
        return self.region.height / self.height

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """Centre of the screen-pixel block covered by image pixel (x, y)."""
        return (
            math.floor(self.region.left + (x + 0.5) * self.scale_x),
            math.floor(self.region.top + (y + 0.5) * self.scale_y),
        )

    def to_image(self, x: float, y: float) -> tuple[int, int] | None:
        ix = math.floor((x - self.region.left) / self.scale_x)
        iy = math.floor((y - self.region.top) / self.scale_y)
        if not (0 <= ix < self.width and 0 <= iy < self.height):
            return None
        return (ix, iy)

    def from_normalized(self, x: float, y: float) -> tuple[int, int]:
        """Screen pixel for a point on the 0-1000 grid over this view (1000 = right/bottom edge)."""
        r = self.region
        return (
            min(r.right, round(r.left + x * r.width / NORMALIZED_MAX)),
            min(r.bottom, round(r.top + y * r.height / NORMALIZED_MAX)),
        )

    def to_normalized(self, x: float, y: float) -> tuple[int, int] | None:
        r = self.region
        if not (r.left <= x <= r.right and r.top <= y <= r.bottom):
            return None
        return (round((x - r.left) * NORMALIZED_MAX / r.width), round((y - r.top) * NORMALIZED_MAX / r.height))

    def from_calibrated(self, cal: Calibration, x: float, y: float) -> tuple[int, int]:
        u, v = cal.to_fraction(x, y, self.width, self.height)
        r = self.region
        return (
            min(r.right, max(r.left, math.floor(r.left + u * r.width))),
            min(r.bottom, max(r.top, math.floor(r.top + v * r.height))),
        )

    def to_calibrated(self, cal: Calibration, x: float, y: float) -> tuple[float, float] | None:
        r = self.region
        if not (r.left <= x <= r.right and r.top <= y <= r.bottom):
            return None
        u, v = (x - r.left + 0.5) / r.width, (y - r.top + 0.5) / r.height
        cx, cy = cal.from_fraction(u, v, self.width, self.height)
        return (round(cx, 3), round(cy, 3))


def target_size(width: int, height: int, max_width: int | None = None, snap_multiple: int = 0) -> tuple[int, int]:
    """Image size for a ``width`` x ``height`` capture: downscaled to ``max_width``, then each side
    rounded down to a multiple of ``snap_multiple`` (when > 1)."""
    w, h = width, height
    if max_width and w > max_width:
        h = max(1, round(h * max_width / w))
        w = max_width
    if snap_multiple > 1:
        w = max(snap_multiple, w // snap_multiple * snap_multiple)
        h = max(snap_multiple, h // snap_multiple * snap_multiple)
    return w, h


def _ticks(
    pixels: int, offset: int, length: int, step: int, space: str, axis: Callable[[float], float] | None = None
) -> list[tuple[int, int]]:
    """(image pixel, label) pairs every ``step`` units of ``space`` along one image axis.

    For ``"calibrated"``, ``axis`` maps an image fraction (0..1) to the reported coordinate.
    """
    if space == "calibrated" and axis is not None:
        lo, hi = sorted((axis(0.0), axis(1.0)))
        values = range(math.ceil(lo / step) * step, math.floor(hi) + 1, step)
        span = axis(1.0) - axis(0.0)
        positions = [(v - axis(0.0)) / span * pixels for v in values]
    elif space == "normalized":
        values = range(0, NORMALIZED_MAX + 1, step)
        positions = [v * pixels / NORMALIZED_MAX for v in values]
    elif space == "screen":
        values = range(math.ceil(offset / step) * step, offset + length, step)
        positions = [(v - offset) * pixels / length for v in values]
    else:
        values = range(0, pixels, step)
        positions = [float(v) for v in values]
    return [(min(pixels - 1, max(0, math.floor(p))), v) for p, v in zip(positions, values)]


def _calibrated_axes(cal: Calibration, view: View) -> tuple[Callable[[float], float], Callable[[float], float]]:
    def x_axis(u: float) -> float:
        return cal.from_fraction(u, 0.0, view.width, view.height)[0]

    def y_axis(v: float) -> float:
        return cal.from_fraction(0.0, v, view.width, view.height)[1]

    return x_axis, y_axis


def draw_grid(
    image: Image.Image, view: View, step: int, space: str, calibration: Calibration | None = None
) -> Image.Image:
    """Overlay faint lines with labels every ``step`` units of ``space`` (labels are valid coordinates)."""
    from PIL import ImageDraw, ImageFont

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()
    line = (255, 0, 255, 80)

    def label(pos: tuple[int, int], text: str) -> None:
        left, top, right, bottom = draw.textbbox(pos, text, font=font)
        draw.rectangle((left - 1, top - 1, right + 1, bottom + 1), fill=(0, 0, 0, 170))
        draw.text(pos, text, fill=(255, 255, 0, 255), font=font)

    x_axis, y_axis = _calibrated_axes(calibration, view) if calibration is not None else (None, None)
    for px, value in _ticks(view.width, view.region.left, view.region.width, step, space, x_axis):
        draw.line([(px, 0), (px, image.height - 1)], fill=line, width=1)
        label((min(px + 2, max(0, image.width - 30)), 1), str(value))
    for py, value in _ticks(view.height, view.region.top, view.region.height, step, space, y_axis):
        draw.line([(0, py), (image.width - 1, py)], fill=line, width=1)
        if py > 12:
            label((1, min(py + 2, max(0, image.height - 14))), str(value))
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


@dataclass(frozen=True)
class Capture:
    data: bytes
    format: str
    width: int
    height: int
    region: Bounds

    @property
    def view(self) -> View:
        return View(self.region, self.width, self.height)

    @property
    def scale(self) -> float:
        return round(self.view.scale_x, 6)

    def metadata(self) -> dict[str, Any]:
        view = self.view
        return {
            "screen_resolution": f"{self.region.width}x{self.region.height}",
            "image_size": f"{self.width}x{self.height}",
            "image_width": self.width,
            "image_height": self.height,
            "format": self.format,
            "region": self.region.as_dict(),
            "scale": self.scale,
            "scale_x": round(view.scale_x, 6),
            "scale_y": round(view.scale_y, 6),
            "to_screen": "screen_x = region.left + image_x * scale_x; screen_y = region.top + image_y * scale_y",
        }


def capture(
    monitor: int = 0,
    max_width: int | None = None,
    image_format: str = "png",
    quality: int = 80,
    *,
    snap_multiple: int = 0,
    grid: int = 0,
    grid_space: str = "screenshot",
    calibration: Calibration | None = None,
) -> Capture:
    """Grab ``monitor`` (0 = all monitors), optionally downscale to ``max_width`` pixels wide,
    snap the size to ``snap_multiple`` and overlay a coordinate grid every ``grid`` units."""
    enable_dpi_awareness()
    fmt = image_format.lower()
    if fmt == "jpg":
        fmt = "jpeg"
    if fmt not in ("png", "jpeg"):
        raise ValueError("format must be 'png' or 'jpeg'")
    with _mss() as sct:
        if not 0 <= monitor < len(sct.monitors):
            raise ValueError(f"monitor must be between 0 and {len(sct.monitors) - 1}")
        region = _bounds(sct.monitors[monitor])
        shot = sct.grab(sct.monitors[monitor])
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    size = target_size(image.width, image.height, max_width, snap_multiple)
    if size != image.size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    if grid > 0:
        image = draw_grid(image, View(region, image.width, image.height), grid, grid_space, calibration)
    buffer = io.BytesIO()
    if fmt == "jpeg":
        image.save(buffer, format="JPEG", quality=max(1, min(95, quality)))
    else:
        image.save(buffer, format="PNG", optimize=False)
    return Capture(buffer.getvalue(), fmt, image.width, image.height, region)
