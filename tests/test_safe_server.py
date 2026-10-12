from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
SAFE_SERVER = ROOT / "integrations" / "opencode" / "calibration-test" / "safe_server.py"
READ_ONLY = {"calibrate", "calibrate_submit", "calibrate_check", "get_config", "get_screen_size", "screenshot"}


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("safe_server", SAFE_SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_input_backends_refuse_every_action() -> None:
    safe = _load()
    mouse, keyboard = safe.NoInputMouse(), safe.NoInputKeyboard()
    for call in (
        lambda: mouse.move_to(1, 1),
        lambda: mouse.button_down("left"),
        lambda: mouse.button_up("left"),
        lambda: mouse.scroll(0, 1),
        lambda: keyboard.press("a"),
        lambda: keyboard.release("a"),
    ):
        with pytest.raises(safe.InputDisabled):
            call()


async def _session(tmp_path: Path) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("HUMANLIKE_")}
    params = StdioServerParameters(command=sys.executable, args=[str(SAFE_SERVER)], env=env, cwd=str(tmp_path))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            shot = await session.call_tool("screenshot", {"image": True})
            cal = await session.call_tool("calibrate", {})
            click = await session.call_tool("click", {"x": 10, "y": 10})
            return {"tools": {t.name for t in tools.tools}, "shot": shot, "cal": cal, "click": click}


def test_safe_server_exposes_only_read_only_tools(tmp_path: Path) -> None:
    out = asyncio.run(_session(tmp_path))
    assert out["tools"] <= READ_ONLY
    assert {"calibrate", "calibrate_submit", "screenshot"} <= out["tools"]
    assert [c.type for c in out["shot"].content] == ["text"]
    assert "image" in [c.type for c in out["cal"].content]
    assert out["click"].is_error
