"""MCP server (stdio) exposing human-like computer-use tools."""

from __future__ import annotations

import json
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, TypeVar

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import __version__, calibration, screen
from .config import PRESET_DESCRIPTIONS, PRESETS, ConfigError, ConfigManager, Settings, env_var_name
from .errors import FailSafeTriggered
from .keyboard import Keyboard
from .mouse import Mouse

T = TypeVar("T")
WAIT_POLL_S = 0.2
MAX_CALIBRATION_FAILURES = 3

INSTRUCTIONS = """\
Computer-use tools that drive the real mouse and keyboard with human-like timing.
Take a screenshot first. Its first text line states the image size, the screen size and
which coordinates the mouse tools expect; follow that line exactly.
Coordinate spaces (coordinate_space argument; omit it to use the configured default):
- "screenshot": pixels in the latest screenshot image, exactly as you see them
  (x 0..image_width-1, y 0..image_height-1). The server converts them to the screen.
- "normalized": a 0-1000 grid over the latest screenshot (0,0 top-left, 1000,1000
  bottom-right, 500,500 centre), independent of the image size.
- "screen": physical screen pixels ((0, 0) = top-left of the primary monitor; other
  monitors may be negative).
- "calibrated": your own convention, measured with calibrate / calibrate_submit.
Out-of-range coordinates are rejected with an error naming the valid range; nothing is
clicked. If clicks land off-target (the cursor ends up somewhere other than where you
aimed) or out-of-range errors repeat, call calibrate once, report the numbered target
centres to calibrate_submit, and keep using coordinates exactly as you reported them;
the server then maps your convention to the screen. If you cannot see the screenshot
image, do not guess coordinates: tell the user that images are not reaching the model.
Every mouse result reports the cursor in every space. Moving the cursor into a corner of the primary screen aborts automation
(fail-safe). Settings can be changed with set_config / use_preset; action tools also
accept per-call overrides.
After every input action, call screenshot (after the action has returned, never in
parallel with it) and confirm the step succeeded before starting the next one; if it
did not (e.g. typed text is incomplete), fix it and screenshot again.
Images are expensive and some providers cap images per request (the client re-sends old
ones): when a step can be confirmed without looking (e.g. the cursor position in a mouse
result), use screenshot image=false. If a screenshot says the image budget is reached,
tell the user to compact or start a new session, then call reset_image_budget."""

DEFAULT_SCREENSHOT_WIDTH = Settings().screenshot.max_width
SCREENSHOT_DESCRIPTION = """Capture the screen. Returns one text block, then the image. The text starts with a plain
line stating the image size, the screen size and the coordinates mouse tools expect, followed by
JSON metadata (screen resolution, image size, scale, region, cursor in every coordinate space).
With image=false only the text is returned."""
BLIND_NOTE = (
    "If no image follows this text, or it was replaced by an error such as \"this model does not support "
    "image input\", the client is not passing images to the model: do not guess coordinates; tell the user "
    "to use a vision-capable model with image input enabled."
)

Button = Literal["left", "right", "middle"]
Space = Literal["screenshot", "normalized", "screen", "calibrated"]
CoordinateSpace = Annotated[
    Space | None,
    Field(
        description='How to read x/y. Omit to use the configured default (named in the screenshot\'s first line). '
        '"screenshot": pixels in the latest screenshot image. '
        '"normalized": 0-1000 grid over the latest screenshot (500,500 = centre). '
        '"screen": physical screen pixels. '
        '"calibrated": the convention measured by calibrate / calibrate_submit.'
    ),
]
XCoord = Annotated[float, Field(description="Target x in the active coordinate space (see the screenshot's first line).")]
YCoord = Annotated[float, Field(description="Target y in the active coordinate space (see the screenshot's first line).")]
OptX = Annotated[float | None, Field(description="Optional x in the active coordinate space; move here first.")]
OptY = Annotated[float | None, Field(description="Optional y in the active coordinate space; move here first.")]
Speed = Annotated[float | None, Field(description="Mouse speed multiplier for this call (1.0 = configured speed).", gt=0, le=20)]
Humanize = Annotated[bool | None, Field(description="Set false to skip human-like timing for this call.")]
Overrides = Annotated[
    dict[str, Any] | None,
    Field(description='Per-call setting overrides by dotted key, e.g. {"mouse.wind": 5, "click.hold_ms": [80, 120]}.'),
]


@dataclass
class Runtime:
    config: ConfigManager
    mouse: Mouse
    keyboard: Keyboard
    lock: threading.Lock = field(default_factory=threading.Lock)
    view: screen.View | None = None
    calibration_session: calibration.Session | None = None
    range_errors: int = 0
    images_sent: int = 0
    calibration_failures: int = 0


_runtime: Runtime | None = None
_runtime_lock = threading.Lock()


def create_runtime(config: ConfigManager | None = None) -> Runtime:
    rng = random.Random()
    mouse = Mouse(rng=rng)
    keyboard = Keyboard(rng=rng, failsafe_check=lambda: mouse.backend.check_failsafe())
    return Runtime(config=config or ConfigManager(), mouse=mouse, keyboard=keyboard)


def get_runtime() -> Runtime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = create_runtime()
        return _runtime


def set_runtime(runtime: Runtime | None) -> None:
    global _runtime
    with _runtime_lock:
        _runtime = runtime


def _guard(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except (FailSafeTriggered, ConfigError, ValueError, KeyError) as exc:
        raise ToolError(str(exc)) from exc


def _settings(rt: Runtime, overrides: dict[str, Any] | None, named: dict[str, Any]) -> Settings:
    merged = dict(overrides or {})
    merged.update({k: v for k, v in named.items() if v is not None})
    return rt.config.effective(merged)


def _act(overrides: dict[str, Any] | None, named: dict[str, Any], action: Callable[[Runtime, Settings], T]) -> T:
    def run() -> T:
        rt = get_runtime()
        s = _settings(rt, overrides, named)
        with rt.lock:
            return action(rt, s)

    return _guard(run)


XY = Callable[[float | None, float | None], tuple[int | None, int | None]]
NO_SCREENSHOT_NOTE = "No screenshot taken yet, so x/y were treated as screen pixels. Call screenshot first."
CALIBRATE_HINT = (
    "If this repeats or clicks land off-target, call calibrate and then calibrate_submit to measure your "
    "coordinate convention."
)

_log_lock = threading.Lock()
_log_failures: set[str] = set()


def _debug_log(s: Settings, event: str, **data: Any) -> None:
    """Append one JSONL record to debug.log_path (no-op when unset); never raises."""
    path = s.debug.log_path
    if not path:
        return
    record = {"ts": round(time.time(), 3), "event": event, **data}
    try:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        with _log_lock, target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as exc:
        if path not in _log_failures:
            _log_failures.add(path)
            print(f"humanlike-computer-use-mcp: cannot write debug log {path}: {exc}", file=sys.stderr)


def _budget_info(rt: Runtime, s: Settings) -> dict[str, int]:
    limit = s.screenshot.max_session_images
    return {"used": rt.images_sent, "limit": limit, "remaining": max(0, limit - rt.images_sent)}


def _budget_exhausted(rt: Runtime, s: Settings) -> bool:
    limit = s.screenshot.max_session_images
    return limit > 0 and rt.images_sent >= limit


def _budget_reached_note(rt: Runtime, s: Settings) -> str:
    return (
        f"Image budget reached ({rt.images_sent} of {s.screenshot.max_session_images} images in this server "
        "session), so no image was attached. Ask the user to compact the conversation (/compact) or start a new "
        "session, then call reset_image_budget; until then rely on image=false and the cursor positions in "
        "mouse results."
    )


def _spend_image(rt: Runtime, s: Settings) -> str | None:
    """Count one returned image; return a budget line for the result text when a budget is set."""
    rt.images_sent += 1
    limit = s.screenshot.max_session_images
    if not limit:
        return None
    left = limit - rt.images_sent
    if left > 0:
        return (
            f"Image budget: {rt.images_sent} of {limit} images used in this server session ({left} left). "
            "Verify with image=false and mouse results where a picture isn't needed."
        )
    return (
        f"Image budget: this was the last image ({limit} of {limit}). Further screenshots return metadata only "
        "until the user compacts or starts a new session and reset_image_budget is called."
    )


def _fmt(value: float) -> str:
    return f"{round(value, 3):g}"


def _calibration(s: Settings) -> screen.Calibration:
    sx, ox, sy, oy = s.screenshot.calibration
    return screen.Calibration(sx, ox, sy, oy, s.screenshot.calibration_units)


def _view_info(view: screen.View | None) -> dict[str, Any] | None:
    if view is None:
        return None
    return {"image": [view.width, view.height], "region": view.region.as_dict()}


def _cursor(rt: Runtime) -> dict[str, Any]:
    sx, sy = rt.mouse.position()
    info: dict[str, Any] = {"screen": [sx, sy]}
    if rt.view is not None:
        img = rt.view.to_image(sx, sy)
        info["screenshot"] = list(img) if img is not None else None
        norm = rt.view.to_normalized(sx, sy)
        info["normalized"] = list(norm) if norm is not None else None
        s = rt.config.settings
        if s.screenshot.coordinate_space == "calibrated":
            cal = rt.view.to_calibrated(_calibration(s), sx, sy)
            info["calibrated"] = list(cal) if cal is not None else None
    return info


def _valid_range(
    view: screen.View, space: str, cal: screen.Calibration | None = None
) -> tuple[tuple[float, float], tuple[float, float]]:
    if space == "normalized":
        return (0, screen.NORMALIZED_MAX), (0, screen.NORMALIZED_MAX)
    if space == "screen":
        r = view.region
        return (r.left, r.right), (r.top, r.bottom)
    if space == "calibrated" and cal is not None:
        x0, y0 = cal.from_fraction(0.0, 0.0, view.width, view.height)
        x1, y1 = cal.from_fraction(1.0, 1.0, view.width, view.height)
        (xa, xb), (ya, yb) = sorted((x0, x1)), sorted((y0, y1))
        return (round(xa, 3), round(xb, 3)), (round(ya, 3), round(yb, 3))
    return (0, view.width - 1), (0, view.height - 1)


def _fits(view: screen.View, space: str, x: float | None, y: float | None, cal: screen.Calibration | None = None) -> bool:
    if space == "calibrated":
        if cal is None:
            return False
        u, v = cal.to_fraction(x or 0.0, y or 0.0, view.width, view.height)
        lo, hi = -screen.CALIBRATED_TOLERANCE, 1 + screen.CALIBRATED_TOLERANCE
        return (x is None or lo <= u <= hi) and (y is None or lo <= v <= hi)
    (x0, x1), (y0, y1) = _valid_range(view, space)
    return (x is None or x0 <= x <= x1) and (y is None or y0 <= y <= y1)


SPACE_MEANING = {
    "normalized": "a 0-1000 grid over the latest screenshot",
    "calibrated": "your calibrated convention over the latest screenshot",
}


def _range_error(
    view: screen.View, space: str, x: float | None, y: float | None, cal: screen.Calibration | None, count: int
) -> str:
    (x0, x1), (y0, y1) = _valid_range(view, space, cal)
    given = ", ".join(f"{n}={_fmt(v)}" for n, v in (("x", x), ("y", y)) if v is not None)
    meaning = SPACE_MEANING.get(space, f"pixels in the latest {view.width}x{view.height} screenshot")
    parts = [
        f'{given} is outside the valid range for coordinate_space="{space}" '
        f"(x {_fmt(x0)}..{_fmt(x1)}, y {_fmt(y0)}..{_fmt(y1)}; {meaning} of a "
        f"{view.region.width}x{view.region.height} screen). Nothing was done."
    ]
    if space != "screenshot" and _fits(view, "screenshot", x, y):
        parts.append('If these are pixels in the screenshot image, pass coordinate_space="screenshot".')
    if space != "screen" and _fits(view, "screen", x, y):
        parts.append('If these are physical screen pixels, pass coordinate_space="screen".')
    if space != "normalized" and _fits(view, "normalized", x, y):
        parts.append('If these are on a 0-1000 grid, pass coordinate_space="normalized".')
    if count >= 2:
        parts.append(
            f"This is out-of-range call {count} in this session, so your coordinate convention probably doesn't "
            "match: call calibrate, then calibrate_submit, and keep reporting coordinates the same way."
        )
    else:
        parts.append(CALIBRATE_HINT)
    return " ".join(parts)


def _map_xy(
    view: screen.View | None, space: str, x: float | None, y: float | None, cal: screen.Calibration
) -> tuple[int | None, int | None]:
    """Convert in-range x/y in ``space`` to screen pixels; missing axes stay None."""
    if x is None and y is None:
        return None, None
    if space == "screen" or view is None:
        return (round(x) if x is not None else None, round(y) if y is not None else None)
    if space == "normalized":
        sx, sy = view.from_normalized(x or 0, y or 0)
    elif space == "calibrated":
        sx, sy = view.from_calibrated(cal, x or 0.0, y or 0.0)
    else:
        sx, sy = view.to_screen(x or 0, y or 0)
    return (sx if x is not None else None, sy if y is not None else None)


def _pointer_act(
    tool: str,
    overrides: dict[str, Any] | None,
    named: dict[str, Any],
    space: str | None,
    action: Callable[[Runtime, Settings, XY], dict[str, Any]],
) -> dict[str, Any]:
    def run(rt: Runtime, s: Settings) -> dict[str, Any]:
        used = space or s.screenshot.coordinate_space
        view = rt.view
        cal = _calibration(s)
        note = None
        points: list[dict[str, Any]] = []
        record: dict[str, Any] = {"tool": tool, "requested_space": space, "view": _view_info(view)}
        try:
            if used != "screen" and view is None:
                if used in ("normalized", "calibrated"):
                    raise ValueError(
                        f'coordinate_space="{used}" needs a screenshot first: call screenshot, then pass x/y '
                        "in that space."
                    )
                used, note = "screen", NO_SCREENSHOT_NOTE

            def xy(x: float | None, y: float | None) -> tuple[int | None, int | None]:
                entry: dict[str, Any] = {"raw": [x, y]}
                points.append(entry)
                in_bounds = used == "screen" or view is None or (x is None and y is None) or _fits(view, used, x, y, cal)
                entry["in_bounds"] = in_bounds
                if not in_bounds:
                    rt.range_errors += 1
                    raise ValueError(_range_error(view, used, x, y, cal, rt.range_errors))
                mapped = _map_xy(view, used, x, y, cal)
                entry["screen"] = list(mapped)
                return mapped

            result = action(rt, s, xy)
        except Exception as exc:
            _debug_log(s, "mouse", **record, coordinate_space=used, points=points, error=str(exc))
            raise
        result["coordinate_space"] = used
        if note:
            result["note"] = note
        result["cursor"] = _cursor(rt)
        _debug_log(s, "mouse", **record, coordinate_space=used, points=points, cursor=result["cursor"])
        return result

    return _act(overrides, named, run)


mcp = MCPServer("humanlike-computer-use", instructions=INSTRUCTIONS, version=__version__)


# ------------------------------------------------------------------ screen


def _coordinates_line(view: screen.View, space: str, cal: screen.Calibration | None = None) -> str:
    (x0, x1), (y0, y1) = _valid_range(view, space, cal)
    xs, ys = f"x {_fmt(x0)}..{_fmt(x1)}", f"y {_fmt(y0)}..{_fmt(y1)}"
    if space == "normalized":
        return (
            f'Click coordinates (coordinate_space="normalized"): a 0-1000 grid over this image, not pixels; '
            f"{xs} left to right, {ys} top to bottom, 500,500 = centre."
        )
    if space == "screen":
        return (
            f'Click coordinates (coordinate_space="screen"): physical screen pixels, {xs}, {ys} '
            f"(screen = region offset + image pixel * scale {round(view.scale_x, 4)})."
        )
    if space == "calibrated":
        return (
            f'Click coordinates (coordinate_space="calibrated"): your own convention, exactly as you reported the '
            f"calibration targets ({xs}, {ys}); do not convert them."
        )
    return f'Click coordinates (coordinate_space="screenshot"): pixels in this image, {xs}, {ys}.'


def screenshot_header(view: screen.View, space: str, monitor: int, cal: screen.Calibration | None = None) -> str:
    """Plain-text first line(s) of a screenshot result: sizes and the coordinates mouse tools expect."""
    r = view.region
    return (
        f"Screenshot {view.width}x{view.height} of a {r.width}x{r.height} screen "
        f"(monitor {monitor}, top-left at screen {r.left},{r.top}). {_coordinates_line(view, space, cal)}\n{BLIND_NOTE}"
    )


def _text(header: str, meta: dict[str, Any]) -> str:
    return f"{header}\n{json.dumps(meta)}"


@mcp.tool(structured_output=False, description=SCREENSHOT_DESCRIPTION)
def screenshot(
    monitor: Annotated[int, Field(description="1 = primary (default), 2.. = others, 0 = all monitors (virtual desktop).", ge=0)] = 1,
    max_width: Annotated[
        int | None,
        Field(
            description="Downscale so the image is at most this wide. Omit for the configured default "
            f"(screenshot.max_width, {DEFAULT_SCREENSHOT_WIDTH} unless changed); 0 = full resolution; otherwise >= 64.",
            ge=0,
        ),
    ] = None,
    format: Annotated[Literal["png", "jpeg"], Field(description="Image encoding.")] = "png",
    quality: Annotated[int, Field(description="JPEG quality (1-95).", ge=1, le=95)] = 80,
    image: Annotated[
        bool,
        Field(
            description="false = metadata only (resolution, cursor), no image; saves context. "
            "Does not change the reference screenshot used for mouse coordinates."
        ),
    ] = True,
    grid: Annotated[
        int | None,
        Field(
            description="Overlay labeled grid lines every N units of the active coordinate space (0 = off, "
            "otherwise >= 10). Omit for the configured default (screenshot.grid).",
            ge=0,
            le=1000,
        ),
    ] = None,
) -> list[Any]:
    def run() -> list[Any]:
        rt = get_runtime()
        s = _settings(rt, None, {"screenshot.max_width": max_width, "screenshot.grid": grid})
        cfg = s.screenshot
        space = cfg.coordinate_space
        cal = _calibration(s)
        if not image:
            return [_metadata_only(rt, s, monitor, space, cal)]
        if _budget_exhausted(rt, s):
            _debug_log(s, "screenshot", monitor=monitor, image=None, budget=_budget_info(rt, s))
            return [_metadata_only(rt, s, monitor, space, cal, budget_note=_budget_reached_note(rt, s))]
        shot = screen.capture(
            monitor,
            cfg.max_width or None,
            format,
            quality,
            snap_multiple=cfg.snap_multiple,
            grid=cfg.grid,
            grid_space=space,
            calibration=cal if space == "calibrated" else None,
        )
        rt.view = shot.view
        meta = shot.metadata()
        (x0, x1), (y0, y1) = _valid_range(shot.view, space, cal)
        meta["coordinate_space"] = space
        meta["valid_range"] = {"x": [x0, x1], "y": [y0, y1]}
        if cfg.grid:
            meta["grid"] = cfg.grid
        cursor = _cursor(rt)
        meta["cursor_screen"] = cursor["screen"]
        meta["cursor_screenshot"] = cursor.get("screenshot")
        meta["cursor_normalized"] = cursor.get("normalized")
        if "calibrated" in cursor:
            meta["cursor_calibrated"] = cursor["calibrated"]
        budget_line = _spend_image(rt, s)
        if budget_line:
            meta["image_budget"] = _budget_info(rt, s)
        _debug_log(
            s,
            "screenshot",
            monitor=monitor,
            image=[shot.width, shot.height],
            region=shot.region.as_dict(),
            scale=[round(shot.view.scale_x, 6), round(shot.view.scale_y, 6)],
            coordinate_space=space,
            calibration=[*cal.as_list(), cal.units] if space == "calibrated" else None,
            grid=cfg.grid,
            budget=_budget_info(rt, s) if budget_line else None,
        )
        header = screenshot_header(shot.view, space, monitor, cal)
        if budget_line:
            header = f"{header}\n{budget_line}"
        return [_text(header, meta), Image(data=shot.data, format=shot.format)]

    return _guard(run)


def _metadata_only(
    rt: Runtime,
    s: Settings,
    monitor: int,
    space: str,
    cal: screen.Calibration | None = None,
    budget_note: str | None = None,
) -> str:
    monitors = screen.list_monitors()
    if not 0 <= monitor < len(monitors):
        raise ValueError(f"monitor must be between 0 and {len(monitors) - 1}")
    region = monitors[monitor]
    cursor = _cursor(rt)
    view = rt.view
    reason = budget_note or "No image captured (image=false)."
    if view is None:
        header = (
            f"{reason} Monitor {monitor} is {region.width}x{region.height}. "
            "No reference screenshot yet: take one with an image before clicking."
        )
    else:
        header = (
            f"{reason} Monitor {monitor} is {region.width}x{region.height}. Mouse coordinates "
            f"still refer to the last {view.width}x{view.height} screenshot. {_coordinates_line(view, space, cal)}"
        )
    meta: dict[str, Any] = {
        "image": None,
        "screen_resolution": f"{region.width}x{region.height}",
        "region": region.as_dict(),
        "coordinate_space": space,
        "cursor_screen": cursor["screen"],
        "cursor_screenshot": cursor.get("screenshot"),
        "cursor_normalized": cursor.get("normalized"),
        "reference_image_size": f"{view.width}x{view.height}" if view is not None else None,
    }
    if s.screenshot.max_session_images:
        meta["image_budget"] = _budget_info(rt, s)
    return _text(header, meta)


@mcp.tool()
def get_screen_size() -> dict[str, Any]:
    """Primary monitor resolution ("WxH"), the virtual desktop bounds, and every monitor's bounds."""
    return _guard(screen.screen_info)


@mcp.tool()
def get_cursor_position() -> dict[str, Any]:
    """Current cursor position in screen pixels (x, y) and, after a screenshot, in that image's pixels
    ("screenshot") and on its 0-1000 grid ("normalized")."""

    def run() -> dict[str, Any]:
        rt = get_runtime()
        cursor = _cursor(rt)
        x, y = cursor["screen"]
        return {"x": x, "y": y, **cursor}

    return _guard(run)


@mcp.tool(structured_output=False)
def calibrate(
    monitor: Annotated[int, Field(description="Monitor whose screenshot size to mimic (1 = primary).", ge=0)] = 1,
    format: Annotated[Literal["png", "jpeg"], Field(description="Image encoding.")] = "png",
) -> list[Any]:
    """Measure your coordinate convention. Returns a synthetic test image (NOT the real screen; nothing is
    captured or clicked) with the same size as a normal screenshot, showing numbered targets. Report each
    target's centre exactly as you would pass it to click, via calibrate_submit."""

    def run() -> list[Any]:
        rt = get_runtime()
        s = rt.config.settings
        cfg = s.screenshot
        monitors = screen.list_monitors()
        if not 0 <= monitor < len(monitors):
            raise ValueError(f"monitor must be between 0 and {len(monitors) - 1}")
        if _budget_exhausted(rt, s):
            raise ValueError(_budget_reached_note(rt, s).replace("no image was attached", "calibrate can't run"))
        region = monitors[monitor]
        w, h = screen.target_size(region.width, region.height, cfg.max_width or None, cfg.snap_multiple)
        session = calibration.make_session(w, h, region)
        data = calibration.render(session, format)
        rt.calibration_session = session
        n = len(session.targets)
        example = ",".join(f"[x{i},y{i}]" for i in range(1, n + 1))
        text = (
            "Calibration image: synthetic, NOT the real screen; nothing was clicked.\n"
            f"Report the centre of each numbered target (1 to {n}) as [x, y]: the exact numbers you would pass "
            "to click to hit it. Answer directly from the image, the way you would point at it; do not calculate, "
            "convert, rescale or check the numbers against an image size (they do not have to be pixels). "
            "If you have no natural convention, use a 0-1000 grid on both axes (0,0 = top-left corner, "
            f"1000,1000 = bottom-right corner). Then call calibrate_submit with points=[{example}]."
        )
        budget_line = _spend_image(rt, s)
        if budget_line:
            text = f"{text}\n{budget_line}"
        _debug_log(
            s,
            "calibrate",
            monitor=monitor,
            image=[w, h],
            region=region.as_dict(),
            targets=[list(t) for t in session.targets],
        )
        return [text, Image(data=data, format=format)]

    return _guard(run)


def _toml_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(repr(float(v)) for v in value) + "]"
    return f'"{value}"'


def _env_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(repr(float(v)) for v in value)
    return str(value)


def _persistence(updates: dict[str, Any]) -> dict[str, Any]:
    lines = ["[screenshot]"] + [f"{key.split('.', 1)[1]} = {_toml_value(v)}" for key, v in updates.items()]
    return {
        "toml": "\n".join(lines) + "\n",
        "env": {env_var_name(key): _env_value(v) for key, v in updates.items()},
    }


@mcp.tool()
def calibrate_submit(
    points: Annotated[
        list[list[float]],
        Field(description="Centres of the calibration targets in order 1..N, as [[x1,y1],[x2,y2],...].")
    ],
    apply: Annotated[bool, Field(description="Make the measured convention this session's default (default true).")] = True,
) -> dict[str, Any]:
    """Fit the points reported for the latest calibrate image. Detects your convention (image pixels, 0-1000,
    0-1, resized pixels, or a custom linear map), reports the residual in pixels and, if it is consistent,
    makes it the default coordinate_space for this session. Returns the TOML/env values to make it permanent."""

    def run() -> dict[str, Any]:
        rt = get_runtime()
        session = rt.calibration_session
        if session is None:
            raise ValueError("Call calibrate first; it shows the numbered targets to report here.")
        s = rt.config.settings
        result: calibration.FitResult | None = None
        try:
            result = calibration.fit(session, points)
        except calibration.CalibrationError as exc:
            error = str(exc)
            if "inconsistent" in error:
                error = calibration.axis_mismatch(session, points) or error
        else:
            builtin = result.quality == "good" and result.chosen.space != "calibrated"
            hint = None if builtin else calibration.axis_mismatch(session, points)
            if hint and rt.calibration_failures == 0:
                result, error = None, hint
        if result is None:
            rt.calibration_failures += 1
            _debug_log(s, "calibrate_submit", points=points, error=error, failures=rt.calibration_failures)
            if rt.calibration_failures >= MAX_CALIBRATION_FAILURES:
                error += (
                    f" This is failed calibration attempt {rt.calibration_failures}: stop calibrating, keep the "
                    "current coordinate_space and tell the user that calibration did not converge."
                )
            raise ValueError(error)
        rt.calibration_failures = 0
        chosen = result.chosen
        cal = chosen.calibration
        updates: dict[str, Any] = {"screenshot.coordinate_space": chosen.space}
        if chosen.space == "calibrated":
            updates["screenshot.calibration"] = cal.as_list()
            updates["screenshot.calibration_units"] = cal.units
        if apply:
            rt.config.set(updates)
            rt.range_errors = 0
        fitted = result.fitted
        scale = session.region.width / session.width
        next_step = (
            "Calibration is done; do not call calibrate again. Keep reporting coordinates exactly as you did for "
            "the targets and omit coordinate_space; the server maps them to the screen. Take a screenshot before "
            "the next click."
        )
        if result.quality == "usable":
            next_step += (
                f" Accuracy is limited (up to about {chosen.max_error_px * scale:.0f} screen px): aim for the centre "
                "of large controls and verify each click with a screenshot."
            )
        out: dict[str, Any] = {
            "matched": chosen.name,
            "coordinate_space": chosen.space,
            "applied": apply,
            "quality": result.quality,
            "max_error_px": round(chosen.max_error_px, 2),
            "rms_error_px": round(chosen.rms_error_px, 2),
            "max_error_screen_px": round(chosen.max_error_px * scale, 1),
            "per_target_error_px": [round(e, 1) for e in chosen.errors_px],
            "tolerance_px": round(result.tolerance_px, 1),
            "usable_tolerance_px": round(result.usable_tolerance_px, 1),
            "image": [session.width, session.height],
            "fit": {**fitted.summary(), "calibration": _cal_dict(fitted.calibration)},
            "hypotheses": [h.summary() for h in result.hypotheses[:6]],
            "persist": _persistence(updates),
            "next": next_step,
        }
        if result.dropped is not None:
            out["ignored_target"] = result.dropped
            out["warning"] = f"Target {result.dropped} did not fit the others and was ignored."
        if chosen.space == "calibrated":
            out["calibration"] = _cal_dict(cal)
        _debug_log(
            s,
            "calibrate_submit",
            points=points,
            matched=chosen.name,
            coordinate_space=chosen.space,
            max_error_px=out["max_error_px"],
            quality=result.quality,
            ignored_target=result.dropped,
            applied=apply,
            calibration=_cal_dict(cal),
        )
        return out

    return _guard(run)


def _cal_dict(cal: screen.Calibration) -> dict[str, Any]:
    return {"values": cal.as_list(), "units": cal.units}


# ------------------------------------------------------------------ mouse


@mcp.tool()
def mouse_move(
    x: XCoord,
    y: YCoord,
    speed: Speed = None,
    humanize: Humanize = None,
    coordinate_space: CoordinateSpace = None,
    overrides: Overrides = None,
) -> dict[str, Any]:
    """Move the cursor along a curved, human-like path (WindMouse) with variable speed,
    occasional overshoot-and-correct, and a small landing offset."""
    named = {"mouse.speed": speed, "humanize.enabled": humanize}

    def action(rt: Runtime, s: Settings, xy: XY) -> dict[str, Any]:
        tx, ty = xy(x, y)
        return rt.mouse.move(tx, ty, s)

    return _pointer_act("mouse_move", overrides, named, coordinate_space, action)


@mcp.tool()
def click(
    x: OptX = None,
    y: OptY = None,
    button: Button = "left",
    clicks: Annotated[int, Field(description="1 = single, 2 = double, 3 = triple click.", ge=1, le=3)] = 1,
    speed: Speed = None,
    humanize: Humanize = None,
    coordinate_space: CoordinateSpace = None,
    overrides: Overrides = None,
) -> dict[str, Any]:
    """Click with a human hesitation before pressing and a natural press duration."""
    named = {"mouse.speed": speed, "humanize.enabled": humanize}

    def action(rt: Runtime, s: Settings, xy: XY) -> dict[str, Any]:
        tx, ty = xy(x, y)
        return rt.mouse.click(tx, ty, s, button=button, clicks=clicks)

    return _pointer_act("click", overrides, named, coordinate_space, action)


@mcp.tool()
def mouse_down(
    button: Button = "left",
    x: OptX = None,
    y: OptY = None,
    humanize: Humanize = None,
    coordinate_space: CoordinateSpace = None,
    overrides: Overrides = None,
) -> dict[str, Any]:
    """Press and hold a mouse button (pair with mouse_up)."""
    named = {"humanize.enabled": humanize}

    def action(rt: Runtime, s: Settings, xy: XY) -> dict[str, Any]:
        tx, ty = xy(x, y)
        return rt.mouse.button_down(button, s, tx, ty)

    return _pointer_act("mouse_down", overrides, named, coordinate_space, action)


@mcp.tool()
def mouse_up(button: Button = "left") -> dict[str, Any]:
    """Release a mouse button (always allowed, even while the fail-safe is active)."""
    return _pointer_act("mouse_up", None, {}, "screen", lambda rt, s, xy: rt.mouse.button_up(button, s))


@mcp.tool()
def drag(
    start_x: XCoord,
    start_y: YCoord,
    end_x: XCoord,
    end_y: YCoord,
    button: Button = "left",
    speed: Speed = None,
    humanize: Humanize = None,
    coordinate_space: CoordinateSpace = None,
    overrides: Overrides = None,
) -> dict[str, Any]:
    """Drag from start to end: move to start, press, pause, carry along a slower human path, settle, release."""
    named = {"mouse.speed": speed, "humanize.enabled": humanize}

    def action(rt: Runtime, s: Settings, xy: XY) -> dict[str, Any]:
        sx, sy = xy(start_x, start_y)
        ex, ey = xy(end_x, end_y)
        return rt.mouse.drag(sx, sy, ex, ey, s, button=button)

    return _pointer_act("drag", overrides, named, coordinate_space, action)


@mcp.tool()
def scroll(
    amount: Annotated[int, Field(description="Wheel notches. Vertical: positive = up. Horizontal: positive = right.", ge=-500, le=500)],
    x: OptX = None,
    y: OptY = None,
    horizontal: bool = False,
    humanize: Humanize = None,
    coordinate_space: CoordinateSpace = None,
    overrides: Overrides = None,
) -> dict[str, Any]:
    """Scroll the wheel notch by notch in natural flicks (gaps ramp down then up, with regrip pauses)."""
    named = {"humanize.enabled": humanize}

    def action(rt: Runtime, s: Settings, xy: XY) -> dict[str, Any]:
        tx, ty = xy(x, y)
        return rt.mouse.scroll(amount, s, x=tx, y=ty, horizontal=horizontal)

    return _pointer_act("scroll", overrides, named, coordinate_space, action)


# ------------------------------------------------------------------ keyboard


@mcp.tool()
def type_text(
    text: Annotated[str, Field(description="Text to type. Supports Unicode and newlines (typed as Enter).")],
    wpm: Annotated[float | None, Field(description="Typing speed for this call.", ge=5, le=300)] = None,
    typo_rate: Annotated[float | None, Field(description="Probability of a corrected typo per key (0 = none).", ge=0, le=0.5)] = None,
    humanize: Humanize = None,
    overrides: Overrides = None,
) -> dict[str, Any]:
    """Type text into the focused window with human rhythm: variable key timing, pauses at
    word/sentence boundaries, real Shift presses, and occasional typos fixed with Backspace."""
    named = {"humanize.enabled": humanize}

    def action(rt: Runtime, s: Settings) -> dict[str, Any]:
        record: dict[str, Any] = {"text_length": len(text)}
        if s.debug.log_text:
            record["text"] = text
        try:
            result = rt.keyboard.type_text(text, s, wpm=wpm, typo_rate=typo_rate)
        except Exception as exc:
            _debug_log(s, "type_text", **record, error=str(exc))
            raise
        _debug_log(s, "type_text", **record)
        return result

    return _act(overrides, named, action)


@mcp.tool()
def press_key(
    key: Annotated[str, Field(description="Key name (enter, esc, tab, f5, pageup, left, delete, win, ...) or a single character.")],
    presses: Annotated[int, Field(ge=1, le=100)] = 1,
    humanize: Humanize = None,
    overrides: Overrides = None,
) -> dict[str, Any]:
    """Press and release a key one or more times with natural hold and repeat timing."""
    named = {"humanize.enabled": humanize}
    return _act(overrides, named, lambda rt, s: rt.keyboard.press_key(key, s, presses=presses))


@mcp.tool()
def hotkey(
    keys: Annotated[str | list[str], Field(description='Combination like "ctrl+shift+t" or ["ctrl", "c"].')],
    humanize: Humanize = None,
    overrides: Overrides = None,
) -> dict[str, Any]:
    """Press a key combination: modifiers first with short staggered gaps, then the key; release in reverse order."""
    named = {"humanize.enabled": humanize}
    return _act(overrides, named, lambda rt, s: rt.keyboard.hotkey(keys, s))


@mcp.tool()
def wait(seconds: Annotated[float, Field(description="How long to wait (0-300 s).", ge=0, le=300)]) -> dict[str, Any]:
    """Pause, with a slight random variation (±5%) when humanization is enabled.
    Watches the corner fail-safe while waiting and aborts early with a fail-safe error."""

    def run() -> dict[str, Any]:
        rt = get_runtime()
        s = rt.config.settings
        actual = seconds * random.uniform(0.95, 1.05) if s.humanize.enabled else seconds
        check: Callable[[], None] | None = None
        if s.safety.failsafe:
            backend = rt.mouse.backend
            backend.set_failsafe(True)
            check = backend.check_failsafe
        deadline = time.monotonic() + actual
        while True:
            if check is not None:
                check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(WAIT_POLL_S, remaining))
        return {"waited_s": round(actual, 3)}

    return _guard(run)


# ------------------------------------------------------------------ config


@mcp.tool()
def get_config() -> dict[str, Any]:
    """Current settings, each with the layer it came from (default, preset, file, env, runtime)."""
    return get_runtime().config.describe()


@mcp.tool()
def set_config(
    updates: Annotated[
        dict[str, Any],
        Field(description='Dotted keys to values, e.g. {"typing.wpm": 55, "click.hold_ms": [70, 120]}.'),
    ],
) -> dict[str, Any]:
    """Change settings for this session (validated; all-or-nothing). Undo with reset_config."""
    return _guard(lambda: {"updated": get_runtime().config.set(updates)})


@mcp.tool()
def reset_config() -> dict[str, Any]:
    """Undo all runtime changes (set_config and use_preset) and reset the image budget counter."""
    rt = get_runtime()
    rt.config.reset()
    rt.images_sent = 0
    return {"preset": rt.config.preset, "status": "runtime changes cleared; image budget reset"}


@mcp.tool()
def reset_image_budget() -> dict[str, Any]:
    """Reset the count of images returned in this server session (screenshot.max_session_images). Call it only
    after the user has compacted the conversation or started a new session, so older images are gone."""
    rt = get_runtime()
    before = rt.images_sent
    rt.images_sent = 0
    return {"previous": before, **_budget_info(rt, rt.config.settings)}


@mcp.tool()
def list_presets() -> dict[str, Any]:
    """Available presets with descriptions and the settings each one changes."""
    rt = get_runtime()
    return {
        "active": rt.config.preset,
        "presets": {
            name: {
                "description": PRESET_DESCRIPTIONS[name],
                "changes": {k: list(v) if isinstance(v, tuple) else v for k, v in values.items()},
            }
            for name, values in PRESETS.items()
        },
    }


@mcp.tool()
def use_preset(name: Annotated[str, Field(description="natural, careful, fast or instant.")]) -> dict[str, Any]:
    """Switch preset for this session (clears runtime set_config changes; file/env settings still apply on top)."""

    def run() -> dict[str, Any]:
        rt = get_runtime()
        rt.config.use_preset(name)
        return {"preset": rt.config.preset}

    return _guard(run)


SPACE_SUMMARY = {
    "screenshot": "pixels in the latest screenshot image",
    "normalized": "a 0-1000 grid over the latest screenshot",
    "screen": "physical screen pixels",
    "calibrated": "your convention measured by calibrate / calibrate_submit",
}


def display_hint(settings: Settings, primary: screen.Bounds) -> str:
    """One line naming this machine's screen size, the default screenshot size and coordinate space."""
    cfg = settings.screenshot
    w, h = screen.target_size(primary.width, primary.height, cfg.max_width or None, cfg.snap_multiple)
    return (
        f"This machine: primary monitor {primary.width}x{primary.height} physical pixels; a default screenshot of it "
        f'is {w}x{h}. Default coordinate_space: "{cfg.coordinate_space}" ({SPACE_SUMMARY[cfg.coordinate_space]}).'
    )


def apply_display_hint(rt: Runtime) -> str | None:
    """Append the display hint to the server instructions and the screenshot tool description."""
    try:
        monitors = screen.list_monitors()
    except Exception:  # noqa: BLE001 - a hint is optional; the server must still start
        return None
    hint = display_hint(rt.config.settings, monitors[1] if len(monitors) > 1 else monitors[0])
    mcp._lowlevel_server.instructions = f"{INSTRUCTIONS}\n{hint}"
    tool = mcp._tool_manager.get_tool("screenshot")
    if tool is not None:
        tool.description = f"{SCREENSHOT_DESCRIPTION}\n{hint}"
    return hint


def main() -> None:
    screen.enable_dpi_awareness()
    try:
        rt = get_runtime()
    except ConfigError as exc:
        print(f"humanlike-computer-use-mcp: configuration error: {exc}", file=sys.stderr)
        sys.exit(2)
    apply_display_hint(rt)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
