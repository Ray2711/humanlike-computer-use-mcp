"""MCP server (stdio) exposing human-like computer-use tools."""

from __future__ import annotations

import random
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Literal, TypeVar

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import __version__, screen
from .config import PRESET_DESCRIPTIONS, PRESETS, ConfigError, ConfigManager, Settings
from .errors import FailSafeTriggered
from .keyboard import Keyboard
from .mouse import Mouse

T = TypeVar("T")
WAIT_POLL_S = 0.2

INSTRUCTIONS = """\
Computer-use tools that drive the real mouse and keyboard with human-like timing.
Coordinates: by default (coordinate_space="screenshot") mouse tools take pixel coordinates
in the most recent screenshot image, exactly as you see them; the server converts them to
the real screen. Pass coordinate_space="screen" to use physical screen pixels instead
((0, 0) = top-left of the primary monitor; other monitors may be negative). Every mouse
result reports the cursor in both spaces. Take a screenshot first (it defaults to the
primary monitor at 1280 px wide). Moving the cursor into a corner
of the primary screen aborts automation (fail-safe). Settings can be changed with
set_config / use_preset; action tools also accept per-call overrides.
After every input action, call screenshot (after the action has returned, never in
parallel with it) and confirm the step succeeded before starting the next one; if it
did not (e.g. typed text is incomplete), fix it and screenshot again."""

DEFAULT_SCREENSHOT_WIDTH = 1280

Button = Literal["left", "right", "middle"]
CoordinateSpace = Annotated[
    Literal["screenshot", "screen"],
    Field(
        description='"screenshot" (default): x/y are pixels in the latest screenshot image. '
        '"screen": x/y are physical screen pixels.'
    ),
]
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


XY = Callable[[int | None, int | None], tuple[int | None, int | None]]
NO_SCREENSHOT_NOTE = "No screenshot taken yet, so x/y were treated as screen pixels. Call screenshot first."


def _cursor(rt: Runtime) -> dict[str, Any]:
    sx, sy = rt.mouse.position()
    info: dict[str, Any] = {"screen": [sx, sy]}
    if rt.view is not None:
        img = rt.view.to_image(sx, sy)
        info["screenshot"] = list(img) if img is not None else None
    return info


def _pointer_act(
    overrides: dict[str, Any] | None,
    named: dict[str, Any],
    space: str,
    action: Callable[[Runtime, Settings, XY], dict[str, Any]],
) -> dict[str, Any]:
    def run(rt: Runtime, s: Settings) -> dict[str, Any]:
        view = rt.view if space == "screenshot" else None

        def xy(x: int | None, y: int | None) -> tuple[int | None, int | None]:
            if x is None or y is None or view is None:
                return x, y
            return view.to_screen(x, y)

        result = action(rt, s, xy)
        result["coordinate_space"] = "screenshot" if view is not None else "screen"
        if space == "screenshot" and view is None:
            result["note"] = NO_SCREENSHOT_NOTE
        result["cursor"] = _cursor(rt)
        return result

    return _act(overrides, named, run)


mcp = MCPServer("humanlike-computer-use", instructions=INSTRUCTIONS, version=__version__)


# ------------------------------------------------------------------ screen


@mcp.tool(structured_output=False)
def screenshot(
    monitor: Annotated[int, Field(description="1 = primary (default), 2.. = others, 0 = all monitors (virtual desktop).", ge=0)] = 1,
    max_width: Annotated[
        int | None,
        Field(description=f"Downscale so the image is at most this wide (default {DEFAULT_SCREENSHOT_WIDTH}; null = full resolution).", ge=64),
    ] = DEFAULT_SCREENSHOT_WIDTH,
    format: Annotated[Literal["png", "jpeg"], Field(description="Image encoding.")] = "png",
    quality: Annotated[int, Field(description="JPEG quality (1-95).", ge=1, le=95)] = 80,
) -> list[Any]:
    """Capture the screen. Returns JSON metadata (screen resolution, image size, scale, cursor
    position in screen and image pixels) followed by the image. Mouse tools then accept pixel
    coordinates from this image directly."""

    def run() -> list[Any]:
        rt = get_runtime()
        shot = screen.capture(monitor, max_width, format, quality)
        rt.view = shot.view
        meta = shot.metadata()
        cursor = _cursor(rt)
        meta["cursor_screen"] = cursor["screen"]
        meta["cursor_screenshot"] = cursor.get("screenshot")
        meta["coordinates"] = (
            "Mouse tools take pixel coordinates in this image by default; "
            "cursor_screenshot is null when the cursor is outside the captured area."
        )
        return [meta, Image(data=shot.data, format=shot.format)]

    return _guard(run)


@mcp.tool()
def get_screen_size() -> dict[str, Any]:
    """Primary monitor resolution ("WxH"), the virtual desktop bounds, and every monitor's bounds."""
    return _guard(screen.screen_info)


@mcp.tool()
def get_cursor_position() -> dict[str, Any]:
    """Current cursor position in screen pixels (x, y) and, after a screenshot, in that image's pixels."""

    def run() -> dict[str, Any]:
        rt = get_runtime()
        cursor = _cursor(rt)
        x, y = cursor["screen"]
        return {"x": x, "y": y, **cursor}

    return _guard(run)


# ------------------------------------------------------------------ mouse


@mcp.tool()
def mouse_move(
    x: Annotated[int, Field(description="Target x (screenshot pixels by default).")],
    y: Annotated[int, Field(description="Target y (screenshot pixels by default).")],
    speed: Speed = None,
    humanize: Humanize = None,
    coordinate_space: CoordinateSpace = "screenshot",
    overrides: Overrides = None,
) -> dict[str, Any]:
    """Move the cursor along a curved, human-like path (WindMouse) with variable speed,
    occasional overshoot-and-correct, and a small landing offset."""
    named = {"mouse.speed": speed, "humanize.enabled": humanize}

    def action(rt: Runtime, s: Settings, xy: XY) -> dict[str, Any]:
        tx, ty = xy(x, y)
        return rt.mouse.move(tx, ty, s)

    return _pointer_act(overrides, named, coordinate_space, action)


@mcp.tool()
def click(
    x: Annotated[int | None, Field(description="Move here first (omit to click in place).")] = None,
    y: Annotated[int | None, Field(description="Move here first (omit to click in place).")] = None,
    button: Button = "left",
    clicks: Annotated[int, Field(description="1 = single, 2 = double, 3 = triple click.", ge=1, le=3)] = 1,
    speed: Speed = None,
    humanize: Humanize = None,
    coordinate_space: CoordinateSpace = "screenshot",
    overrides: Overrides = None,
) -> dict[str, Any]:
    """Click with a human hesitation before pressing and a natural press duration."""
    named = {"mouse.speed": speed, "humanize.enabled": humanize}

    def action(rt: Runtime, s: Settings, xy: XY) -> dict[str, Any]:
        tx, ty = xy(x, y)
        return rt.mouse.click(tx, ty, s, button=button, clicks=clicks)

    return _pointer_act(overrides, named, coordinate_space, action)


@mcp.tool()
def mouse_down(
    button: Button = "left",
    x: Annotated[int | None, Field(description="Optionally move here first.")] = None,
    y: Annotated[int | None, Field(description="Optionally move here first.")] = None,
    humanize: Humanize = None,
    coordinate_space: CoordinateSpace = "screenshot",
    overrides: Overrides = None,
) -> dict[str, Any]:
    """Press and hold a mouse button (pair with mouse_up)."""
    named = {"humanize.enabled": humanize}

    def action(rt: Runtime, s: Settings, xy: XY) -> dict[str, Any]:
        tx, ty = xy(x, y)
        return rt.mouse.button_down(button, s, tx, ty)

    return _pointer_act(overrides, named, coordinate_space, action)


@mcp.tool()
def mouse_up(button: Button = "left") -> dict[str, Any]:
    """Release a mouse button (always allowed, even while the fail-safe is active)."""
    return _pointer_act(None, {}, "screen", lambda rt, s, xy: rt.mouse.button_up(button, s))


@mcp.tool()
def drag(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    button: Button = "left",
    speed: Speed = None,
    humanize: Humanize = None,
    coordinate_space: CoordinateSpace = "screenshot",
    overrides: Overrides = None,
) -> dict[str, Any]:
    """Drag from start to end: move to start, press, pause, carry along a slower human path, settle, release."""
    named = {"mouse.speed": speed, "humanize.enabled": humanize}

    def action(rt: Runtime, s: Settings, xy: XY) -> dict[str, Any]:
        sx, sy = xy(start_x, start_y)
        ex, ey = xy(end_x, end_y)
        return rt.mouse.drag(sx, sy, ex, ey, s, button=button)

    return _pointer_act(overrides, named, coordinate_space, action)


@mcp.tool()
def scroll(
    amount: Annotated[int, Field(description="Wheel notches. Vertical: positive = up. Horizontal: positive = right.", ge=-500, le=500)],
    x: Annotated[int | None, Field(description="Optionally move here first.")] = None,
    y: Annotated[int | None, Field(description="Optionally move here first.")] = None,
    horizontal: bool = False,
    humanize: Humanize = None,
    coordinate_space: CoordinateSpace = "screenshot",
    overrides: Overrides = None,
) -> dict[str, Any]:
    """Scroll the wheel notch by notch in natural flicks (gaps ramp down then up, with regrip pauses)."""
    named = {"humanize.enabled": humanize}

    def action(rt: Runtime, s: Settings, xy: XY) -> dict[str, Any]:
        tx, ty = xy(x, y)
        return rt.mouse.scroll(amount, s, x=tx, y=ty, horizontal=horizontal)

    return _pointer_act(overrides, named, coordinate_space, action)


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
    return _act(overrides, named, lambda rt, s: rt.keyboard.type_text(text, s, wpm=wpm, typo_rate=typo_rate))


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
    """Undo all runtime changes (set_config and use_preset)."""
    rt = get_runtime()
    rt.config.reset()
    return {"preset": rt.config.preset, "status": "runtime changes cleared"}


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


def main() -> None:
    screen.enable_dpi_awareness()
    try:
        get_runtime()
    except ConfigError as exc:
        print(f"humanlike-computer-use-mcp: configuration error: {exc}", file=sys.stderr)
        sys.exit(2)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
