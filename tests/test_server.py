from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError

from humanlike_mcp import screen, server
from humanlike_mcp.config import ConfigManager
from humanlike_mcp.keyboard import Keyboard
from humanlike_mcp.mouse import Mouse

from .conftest import FakeKeyboardBackend, FakeMouseBackend, Recorder, typed_text

EXPECTED_TOOLS = {
    "screenshot",
    "get_screen_size",
    "get_cursor_position",
    "mouse_move",
    "click",
    "mouse_down",
    "mouse_up",
    "drag",
    "scroll",
    "type_text",
    "press_key",
    "hotkey",
    "wait",
    "get_config",
    "set_config",
    "reset_config",
    "list_presets",
    "use_preset",
}
ROOT = Path(__file__).resolve().parents[1]
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def fake_runtime(tmp_path: Path):
    rec = Recorder()
    mouse_backend = FakeMouseBackend(rec, start=(400, 400))
    mouse = Mouse(mouse_backend, rng=random.Random(1), sleep=rec.sleep)
    keyboard = Keyboard(
        FakeKeyboardBackend(rec), rng=random.Random(2), sleep=rec.sleep, failsafe_check=mouse_backend.check_failsafe
    )
    rt = server.Runtime(config=ConfigManager(env={}, cwd=tmp_path), mouse=mouse, keyboard=keyboard)
    server.set_runtime(rt)
    yield rt, rec, mouse_backend
    server.set_runtime(None)


def call(name: str, args: dict[str, Any] | None = None) -> Any:
    return asyncio.run(server.mcp.call_tool(name, args or {}))


def structured(result: Any) -> dict[str, Any]:
    if result.structured_content is not None:
        data = result.structured_content
        return data.get("result", data) if set(data) == {"result"} else data
    return json.loads(result.content[0].text)


def test_all_tools_listed() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.description


def test_config_tools_roundtrip(fake_runtime) -> None:
    cfg = structured(call("get_config"))
    assert cfg["preset"] == "natural"
    assert cfg["settings"]["typing"]["wpm"] == {"value": 70.0, "source": "default"}
    assert structured(call("set_config", {"updates": {"typing.wpm": 55}}))["updated"] == {"typing.wpm": 55.0}
    assert structured(call("get_config"))["settings"]["typing"]["wpm"]["source"] == "runtime"
    assert structured(call("use_preset", {"name": "fast"}))["preset"] == "fast"
    presets = structured(call("list_presets"))
    assert presets["active"] == "fast" and set(presets["presets"]) == {"natural", "careful", "fast", "instant"}
    structured(call("reset_config"))
    assert structured(call("get_config"))["preset"] == "natural"


def test_invalid_config_is_a_clear_tool_error(fake_runtime) -> None:
    with pytest.raises(ToolError, match="typing.wpm"):
        call("set_config", {"updates": {"typing.wpm": 9999}})
    with pytest.raises(ToolError, match="Unknown preset"):
        call("use_preset", {"name": "turbo"})
    with pytest.raises(ToolError, match="Unknown setting"):
        call("mouse_move", {"x": 1, "y": 1, "overrides": {"mouse.bogus": 1}})


def test_action_tools_use_backends(fake_runtime) -> None:
    rt, rec, _ = fake_runtime
    out = structured(call("mouse_move", {"x": 900, "y": 700, "speed": 2.0}))
    assert abs(out["to"][0] - 900) <= 3
    call("click", {"x": 100, "y": 100, "clicks": 2, "humanize": False})
    assert rec.events[-4:] == [("down", "left"), ("up", "left"), ("down", "left"), ("up", "left")]
    rec.events.clear()
    call("type_text", {"text": "Hi there", "typo_rate": 0})
    assert typed_text(rec.events) == "Hi there"
    rec.events.clear()
    call("hotkey", {"keys": "ctrl+s"})
    assert [e for e in rec.events if e[0] != "sleep"] == [
        ("press", "ctrl"), ("press", "s"), ("release", "s"), ("release", "ctrl")
    ]
    rec.events.clear()
    call("scroll", {"amount": -3, "overrides": {"scroll.tick_gap_ms": [30, 30]}})
    assert len(rec.of("scroll")) == 3
    call("drag", {"start_x": 10, "start_y": 10, "end_x": 50, "end_y": 60, "humanize": False})
    call("press_key", {"key": "enter", "presses": 2})
    call("mouse_down", {"button": "right"})
    call("mouse_up", {"button": "right"})
    pos = structured(call("get_cursor_position"))
    assert set(pos) == {"x", "y", "screen"}
    assert rt.config.settings.mouse.speed == 1.0  # per-call speed was not persisted


def test_failsafe_reported_as_tool_error(fake_runtime) -> None:
    _, _, backend = fake_runtime
    backend.pos = (0, 0)
    with pytest.raises(ToolError, match="Fail-safe triggered"):
        call("click", {"x": 500, "y": 500})
    with pytest.raises(ToolError, match="Fail-safe triggered"):
        call("type_text", {"text": "abc"})


def test_type_text_length_limit_is_tool_error(fake_runtime) -> None:
    with pytest.raises(ToolError, match="limit"):
        call("type_text", {"text": "x" * 50, "overrides": {"safety.max_text_length": 10}})


def test_wait_tool(fake_runtime) -> None:
    out = structured(call("wait", {"seconds": 0.05}))
    assert 0.0475 <= out["waited_s"] <= 0.0525
    call("use_preset", {"name": "instant"})
    assert structured(call("wait", {"seconds": 0.05}))["waited_s"] == 0.05


def test_wait_aborts_immediately_when_cursor_in_corner(fake_runtime) -> None:
    _, _, backend = fake_runtime
    backend.pos = (1919, 0)
    started = time.monotonic()
    with pytest.raises(ToolError, match="Fail-safe triggered"):
        call("wait", {"seconds": 5})
    assert time.monotonic() - started < 1


def test_wait_aborts_when_failsafe_triggers_mid_wait(fake_runtime) -> None:
    _, _, backend = fake_runtime

    def slam_to_corner() -> None:
        time.sleep(0.3)
        backend.pos = (0, 1079)

    threading.Thread(target=slam_to_corner, daemon=True).start()
    started = time.monotonic()
    with pytest.raises(ToolError, match="Fail-safe triggered"):
        call("wait", {"seconds": 10})
    elapsed = time.monotonic() - started
    assert 0.25 < elapsed < 1.5


def test_wait_ignores_corner_when_failsafe_disabled(fake_runtime) -> None:
    _, _, backend = fake_runtime
    backend.pos = (0, 0)
    call("set_config", {"updates": {"safety.failsafe": False}})
    out = structured(call("wait", {"seconds": 0.1}))
    assert out["waited_s"] > 0
    with pytest.raises(ToolError):
        call("wait", {"seconds": 301})


def test_screen_tools_in_process(fake_runtime) -> None:
    size = structured(call("get_screen_size"))
    assert size["width"] > 0 and size["height"] > 0 and size["monitors"]
    result = call("screenshot", {"max_width": 320})
    meta, image = result.content
    assert image.type == "image" and image.mime_type == "image/png"
    assert base64.b64decode(image.data).startswith(PNG_MAGIC)
    info = json.loads(meta.text)
    assert info["image_width"] == 320 and info["scale"] >= 1
    assert info["image_size"] == f"320x{info['image_height']}"
    assert info["screen_resolution"] == f"{info['region']['width']}x{info['region']['height']}"
    assert info["cursor_screen"] == [400, 400]
    assert "cursor_screenshot" in info
    assert size["resolution"] == f"{size['width']}x{size['height']}"
    default = json.loads(call("screenshot").content[0].text)
    assert default["image_width"] <= server.DEFAULT_SCREENSHOT_WIDTH
    jpeg = call("screenshot", {"monitor": 1, "format": "jpeg", "max_width": 200}).content[1]
    assert jpeg.mime_type == "image/jpeg"
    with pytest.raises(ToolError, match="monitor"):
        call("screenshot", {"monitor": 99})


def _set_view(rt: server.Runtime, left: int = 0, top: int = 0) -> None:
    rt.view = screen.View(screen.Bounds(left, top, 2560, 1440), 1280, 720)


def test_screenshot_coordinates_are_scaled_to_screen(fake_runtime) -> None:
    rt, _, backend = fake_runtime
    _set_view(rt)
    out = structured(call("click", {"x": 100, "y": 50, "humanize": False}))
    assert out["coordinate_space"] == "screenshot"
    assert out["cursor"]["screen"] == [201, 101]
    assert out["cursor"]["screenshot"] == [100, 50]
    assert backend.position() == (201, 101)


def test_screen_coordinate_space_is_untouched(fake_runtime) -> None:
    rt, _, backend = fake_runtime
    _set_view(rt)
    out = structured(call("mouse_move", {"x": 300, "y": 200, "humanize": False, "coordinate_space": "screen"}))
    assert out["coordinate_space"] == "screen" and "note" not in out
    assert backend.position() == (300, 200)
    assert out["cursor"]["screenshot"] == [150, 100]


def test_without_screenshot_coordinates_fall_back_to_screen(fake_runtime) -> None:
    _, _, backend = fake_runtime
    out = structured(call("mouse_move", {"x": 321, "y": 123, "humanize": False}))
    assert out["coordinate_space"] == "screen" and "screenshot" in out["note"]
    assert backend.position() == (321, 123)
    assert "screenshot" not in out["cursor"]


def test_drag_and_scroll_use_screenshot_space(fake_runtime) -> None:
    rt, _, backend = fake_runtime
    backend._bounds = screen.Bounds(-1920, 0, 4480, 1440)
    _set_view(rt, left=-1920)
    out = structured(call("drag", {"start_x": 10, "start_y": 10, "end_x": 640, "end_y": 360, "humanize": False}))
    assert out["from"] == [-1899, 21] and out["to"] == [-639, 721]
    call("scroll", {"amount": 1, "x": 1, "y": 1, "humanize": False})
    assert backend.position() == (-1917, 3)


def test_cursor_position_reports_both_spaces(fake_runtime) -> None:
    rt, _, _ = fake_runtime
    pos = structured(call("get_cursor_position"))
    assert pos == {"x": 400, "y": 400, "screen": [400, 400]}
    _set_view(rt)
    assert structured(call("get_cursor_position"))["screenshot"] == [200, 200]
    _set_view(rt, left=5000)
    assert structured(call("get_cursor_position"))["screenshot"] is None


def test_view_mapping_roundtrip() -> None:
    view = screen.View(screen.Bounds(-1920, 0, 1920, 1080), 1280, 720)
    for ix, iy in [(0, 0), (639, 359), (1279, 719)]:
        sx, sy = view.to_screen(ix, iy)
        assert view.to_image(sx, sy) == (ix, iy)
    assert view.to_image(0, 0) is None
    assert view.to_screen(1279, 719) == (-1, 1079)


async def _stdio_session(tmp_path: Path) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("HUMANLIKE_")}
    env["HUMANLIKE_TYPING__WPM"] = "64"
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "humanlike_mcp.server"], env=env, cwd=str(tmp_path)
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            config = await session.call_tool("get_config", {})
            size = await session.call_tool("get_screen_size", {})
            shot = await session.call_tool("screenshot", {"max_width": 400})
            bad = await session.call_tool("set_config", {"updates": {"typing.wpm": -1}})
            return {"tools": tools, "config": config, "size": size, "shot": shot, "bad": bad}


def test_stdio_server_end_to_end(tmp_path: Path) -> None:
    out = asyncio.run(asyncio.wait_for(_stdio_session(tmp_path), timeout=90))
    assert {t.name for t in out["tools"].tools} == EXPECTED_TOOLS
    cfg = json.loads(out["config"].content[0].text)
    assert cfg["settings"]["typing"]["wpm"] == {"value": 64.0, "source": "env"}
    size = json.loads(out["size"].content[0].text)
    assert size["width"] > 0
    image = out["shot"].content[1]
    assert image.type == "image" and base64.b64decode(image.data).startswith(PNG_MAGIC)
    assert json.loads(out["shot"].content[0].text)["image_width"] == 400
    assert out["bad"].is_error and "minimum" in out["bad"].content[0].text
