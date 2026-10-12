"""Screenshots, screen geometry and DPI awareness."""

from __future__ import annotations

import ctypes
import io
import math
import sys
import threading
from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True)
class Capture:
    data: bytes
    format: str
    width: int
    height: int
    region: Bounds
    scale: float

    @property
    def view(self) -> View:
        return View(self.region, self.width, self.height)

    def metadata(self) -> dict[str, Any]:
        return {
            "screen_resolution": f"{self.region.width}x{self.region.height}",
            "image_size": f"{self.width}x{self.height}",
            "image_width": self.width,
            "image_height": self.height,
            "format": self.format,
            "region": self.region.as_dict(),
            "scale": self.scale,
            "to_screen": "screen_x = region.left + image_x * scale; screen_y = region.top + image_y * scale",
        }


def capture(monitor: int = 0, max_width: int | None = None, image_format: str = "png", quality: int = 80) -> Capture:
    """Grab ``monitor`` (0 = all monitors) and optionally downscale to ``max_width`` pixels wide."""
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
    scale = 1.0
    if max_width and image.width > max_width:
        scale = image.width / max_width
        image = image.resize((max_width, max(1, round(image.height / scale))), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    if fmt == "jpeg":
        image.save(buffer, format="JPEG", quality=max(1, min(95, quality)))
    else:
        image.save(buffer, format="PNG", optimize=False)
    return Capture(buffer.getvalue(), fmt, image.width, image.height, region, round(scale, 6))
