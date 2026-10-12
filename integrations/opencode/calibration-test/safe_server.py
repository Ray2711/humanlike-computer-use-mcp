"""Input-free launcher of the MCP server for calibration experiments driven by a real model through opencode.

Only read-only tools are registered, `screenshot` never returns an image of the real screen, and the mouse and
keyboard backends refuse every action, so nothing the model does can move the cursor or press a key.
Run: venv/Scripts/python.exe integrations/opencode/calibration-test/safe_server.py
"""

from __future__ import annotations

import random
from typing import Annotated, Any

from pydantic import Field

from humanlike_mcp import server
from humanlike_mcp.config import ConfigManager
from humanlike_mcp.keyboard import Keyboard
from humanlike_mcp.mouse import Mouse
from humanlike_mcp.screen import Bounds, list_monitors

ALLOWED = {"calibrate", "calibrate_submit", "calibrate_check", "get_config", "get_screen_size"}


class InputDisabled(RuntimeError):
    pass


class NoInputMouse:
    def position(self) -> tuple[int, int]:
        primary = list_monitors()[1]
        return primary.left + primary.width // 2, primary.top + primary.height // 2

    def bounds(self) -> Bounds:
        return list_monitors()[1]

    def failsafe_points(self) -> list[tuple[int, int]]:
        return []

    def set_failsafe(self, enabled: bool) -> None:
        pass

    def check_failsafe(self) -> None:
        pass

    def __getattr__(self, name: str) -> Any:
        raise InputDisabled(f"mouse.{name} is disabled in the calibration test server")


class NoInputKeyboard:
    def __getattr__(self, name: str) -> Any:
        raise InputDisabled(f"keyboard.{name} is disabled in the calibration test server")


def build() -> None:
    for tool in server.mcp._tool_manager.list_tools():
        if tool.name not in ALLOWED:
            server.mcp.remove_tool(tool.name)

    @server.mcp.tool(structured_output=False, description=server.SCREENSHOT_DESCRIPTION + " (Test server: metadata only.)")
    def screenshot(
        monitor: Annotated[int, Field(description="1 = primary (default).", ge=0)] = 1,
        image: Annotated[bool, Field(description="Ignored here; no image of the real screen is returned.")] = False,
    ) -> list[Any]:
        rt = server.get_runtime()
        s = rt.config.settings
        return [server._metadata_only(rt, s, monitor, s.screenshot.coordinate_space)]

    rng = random.Random()
    server.set_runtime(
        server.Runtime(
            config=ConfigManager(),
            mouse=Mouse(NoInputMouse(), rng=rng),  # type: ignore[arg-type]
            keyboard=Keyboard(NoInputKeyboard(), rng=rng),  # type: ignore[arg-type]
        )
    )


if __name__ == "__main__":
    build()
    server.main()
