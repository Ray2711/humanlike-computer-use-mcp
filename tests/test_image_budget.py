from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from humanlike_mcp import screen, server
from humanlike_mcp.config import ConfigManager
from humanlike_mcp.keyboard import Keyboard
from humanlike_mcp.mouse import Mouse

from .conftest import FakeKeyboardBackend, FakeMouseBackend, Recorder

SCREEN = screen.Bounds(0, 0, 2560, 1440)
PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture
def budget_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    captures: list[int] = []

    def fake_capture(monitor: int, max_width: int | None, image_format: str, quality: int, **kwargs: Any) -> screen.Capture:
        captures.append(monitor)
        w, h = screen.target_size(SCREEN.width, SCREEN.height, max_width, kwargs.get("snap_multiple", 0))
        return screen.Capture(PNG, image_format, w, h, SCREEN)

    monkeypatch.setattr(screen, "list_monitors", lambda: [SCREEN, SCREEN])
    monkeypatch.setattr(screen, "capture", fake_capture)
    rec = Recorder()
    backend = FakeMouseBackend(rec, start=(400, 400), bounds=SCREEN)
    mouse = Mouse(backend, rng=random.Random(1), sleep=rec.sleep)
    keyboard = Keyboard(FakeKeyboardBackend(rec), rng=random.Random(2), sleep=rec.sleep)
    env = {"HUMANLIKE_SCREENSHOT__MAX_SESSION_IMAGES": "3"}
    rt = server.Runtime(config=ConfigManager(env=env, cwd=tmp_path), mouse=mouse, keyboard=keyboard)
    server.set_runtime(rt)
    yield rt, captures
    server.set_runtime(None)


def call(name: str, args: dict[str, Any] | None = None) -> Any:
    return asyncio.run(server.mcp.call_tool(name, args or {}))


def text_of(result: Any) -> str:
    return result.content[0].text


def meta_of(result: Any) -> dict[str, Any]:
    text = text_of(result)
    return json.loads(text[text.index("\n{") + 1 :])


def has_image(result: Any) -> bool:
    return any(getattr(c, "type", "") == "image" for c in result.content)


def test_budget_counts_down_then_returns_metadata_only(budget_runtime) -> None:
    rt, captures = budget_runtime
    first = call("screenshot")
    assert has_image(first) and "1 of 3 images used" in text_of(first) and "(2 left)" in text_of(first)
    assert meta_of(first)["image_budget"] == {"used": 1, "limit": 3, "remaining": 2}
    call("screenshot")
    third = call("screenshot")
    assert has_image(third) and "this was the last image (3 of 3)" in text_of(third)

    fourth = call("screenshot")
    assert not has_image(fourth) and len(captures) == 3
    header = text_of(fourth).splitlines()[0]
    assert header.startswith("Image budget reached (3 of 3")
    assert "/compact" in header and "reset_image_budget" in header and "image=false" in header
    assert "still refer to the last 1280x720 screenshot" in header
    assert meta_of(fourth)["image"] is None and meta_of(fourth)["image_budget"]["remaining"] == 0
    assert rt.view is not None and rt.view.width == 1280


def test_metadata_only_screenshots_are_free(budget_runtime) -> None:
    rt, captures = budget_runtime
    for _ in range(5):
        result = call("screenshot", {"image": False})
        assert not has_image(result)
    assert rt.images_sent == 0 and not captures
    assert meta_of(result)["image_budget"] == {"used": 0, "limit": 3, "remaining": 3}


def test_calibrate_counts_and_is_refused_when_exhausted(budget_runtime) -> None:
    rt, _ = budget_runtime
    result = call("calibrate")
    assert has_image(result) and "1 of 3 images used" in result.content[0].text
    call("screenshot")
    call("screenshot")
    assert rt.images_sent == 3
    with pytest.raises(ToolError, match="Image budget reached.*calibrate can't run"):
        call("calibrate")
    assert rt.images_sent == 3


def test_reset_image_budget_tool_and_reset_config(budget_runtime) -> None:
    rt, _ = budget_runtime
    for _ in range(3):
        call("screenshot")
    result = call("reset_image_budget")
    out = result.structured_content or json.loads(text_of(result))
    assert out == {"previous": 3, "used": 0, "limit": 3, "remaining": 3}
    assert has_image(call("screenshot"))
    call("set_config", {"updates": {"typing.wpm": 50}})
    call("reset_config")
    assert rt.images_sent == 0 and rt.config.settings.typing.wpm != 50


def test_unlimited_by_default_still_counts_without_noise(budget_runtime) -> None:
    rt, captures = budget_runtime
    call("set_config", {"updates": {"screenshot.max_session_images": 0}})
    for _ in range(6):
        result = call("screenshot")
        assert has_image(result) and "Image budget" not in text_of(result)
        assert "image_budget" not in meta_of(result)
    assert rt.images_sent == 6 and len(captures) == 6


def test_budget_raised_at_runtime_unblocks(budget_runtime) -> None:
    rt, _ = budget_runtime
    for _ in range(3):
        call("screenshot")
    assert not has_image(call("screenshot"))
    call("set_config", {"updates": {"screenshot.max_session_images": 4}})
    result = call("screenshot")
    assert has_image(result) and "this was the last image (4 of 4)" in text_of(result)


def test_budget_setting_validation(tmp_path: Path) -> None:
    assert ConfigManager(env={}, cwd=tmp_path).settings.screenshot.max_session_images == 0
    with pytest.raises(Exception, match="max_session_images"):
        ConfigManager(env={"HUMANLIKE_SCREENSHOT__MAX_SESSION_IMAGES": "-1"}, cwd=tmp_path)
