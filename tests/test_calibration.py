from __future__ import annotations

import asyncio
import base64
import io
import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image

from humanlike_mcp import calibration, screen, server
from humanlike_mcp.config import ConfigError, ConfigManager, Settings
from humanlike_mcp.keyboard import Keyboard
from humanlike_mcp.mouse import Mouse

from .conftest import FakeKeyboardBackend, FakeMouseBackend, Recorder

SCREEN = screen.Bounds(0, 0, 2560, 1440)


@pytest.fixture
def fake_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def no_capture(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("calibration must not capture the screen")

    monkeypatch.setattr(screen, "list_monitors", lambda: [SCREEN, SCREEN])
    monkeypatch.setattr(screen, "capture", no_capture)
    np.random.seed(7)
    rec = Recorder()
    backend = FakeMouseBackend(rec, start=(400, 400), bounds=SCREEN)
    mouse = Mouse(backend, rng=random.Random(1), sleep=rec.sleep)
    keyboard = Keyboard(FakeKeyboardBackend(rec), rng=random.Random(2), sleep=rec.sleep)
    rt = server.Runtime(config=ConfigManager(env={}, cwd=tmp_path), mouse=mouse, keyboard=keyboard)
    server.set_runtime(rt)
    yield rt, rec, backend
    server.set_runtime(None)


def call(name: str, args: dict[str, Any] | None = None) -> Any:
    return asyncio.run(server.mcp.call_tool(name, args or {}))


def structured(result: Any) -> dict[str, Any]:
    if result.structured_content is not None:
        data = result.structured_content
        return data.get("result", data) if set(data) == {"result"} else data
    return json.loads(result.content[0].text)


def session(width: int = 1280, height: int = 720) -> calibration.Session:
    return calibration.make_session(width, height, SCREEN)


def resized(s: calibration.Session, rw: int, rh: int) -> list[list[float]]:
    """Target centres as a model that sees the image resized to rw x rh would report them."""
    return [[round((tx + 0.5) * rw / s.width - 0.5), round((ty + 0.5) * rh / s.height - 0.5)] for tx, ty in s.targets]


# ------------------------------------------------------------------ image


def test_calibrate_returns_synthetic_image_without_screen_access(fake_runtime) -> None:
    rt, rec, _ = fake_runtime
    result = call("calibrate")
    assert result.structured_content is None
    text, image = result.content
    assert "NOT the real screen" in text.text and "points=[[x1,y1]," in text.text
    assert "calibrate_submit" in text.text and "1280" not in text.text and "720" not in text.text
    picture = Image.open(io.BytesIO(base64.b64decode(image.data)))
    assert picture.size == (1280, 720)
    sess = rt.calibration_session
    assert sess is not None and (sess.width, sess.height) == (1280, 720)
    assert 4 <= len(sess.targets) <= 6
    for x, y in sess.targets:
        assert picture.convert("RGB").getpixel((x, y)) == (0, 0, 0)
    assert rt.view is None and not rec.events


def test_calibrate_follows_screenshot_settings(fake_runtime) -> None:
    rt, _, _ = fake_runtime
    call("set_config", {"updates": {"screenshot.max_width": 1000, "screenshot.snap_multiple": 28}})
    result = call("calibrate", {"format": "jpeg"})
    picture = Image.open(io.BytesIO(base64.b64decode(result.content[1].data)))
    expected = screen.target_size(2560, 1440, 1000, 28)
    assert picture.format == "JPEG" and picture.size == expected == (980, 560)
    assert rt.calibration_session.width == 980


def test_targets_are_asymmetric_and_inside() -> None:
    s = session(1092, 616)
    xs = sorted(x for x, _ in s.targets)
    ys = sorted(y for _, y in s.targets)
    assert len(set(xs)) == len(xs) and len(set(ys)) == len(ys)
    assert all(0 <= x < 1092 and 0 <= y < 616 for x, y in s.targets)


# ------------------------------------------------------------------ fitting


def test_fit_identity_pixels() -> None:
    s = session()
    result = calibration.fit(s, [list(t) for t in s.targets])
    assert result.chosen.space == "screenshot" and result.chosen.max_error_px < 1


def test_fit_normalized_0_1000() -> None:
    s = session()
    points = [[round((tx + 0.5) / s.width * 1000), round((ty + 0.5) / s.height * 1000)] for tx, ty in s.targets]
    result = calibration.fit(s, points)
    assert result.chosen.space == "normalized" and result.chosen.max_error_px < 2


def test_fit_fractions_0_1() -> None:
    s = session()
    points = [[round((tx + 0.5) / s.width, 3), round((ty + 0.5) / s.height, 3)] for tx, ty in s.targets]
    result = calibration.fit(s, points)
    chosen = result.chosen
    assert chosen.name == "0-1 fractions" and chosen.space == "calibrated"
    assert chosen.calibration.units == "absolute"


def test_fit_known_smart_resize_candidate() -> None:
    s = session()
    rw, rh = calibration.smart_resize(720, 1280, 28, 602112)
    result = calibration.fit(s, resized(s, rw, rh))
    assert result.chosen.space == "calibrated" and f"{rw}x{rh}" in result.chosen.name
    assert result.chosen.calibration.units == "image"


def test_fit_arbitrary_resize_1092x616() -> None:
    s = session()
    result = calibration.fit(s, resized(s, 1092, 616))
    cal = result.chosen.calibration
    assert result.chosen.space == "calibrated" and cal.units == "image"
    assert cal.sx == pytest.approx(1280 / 1092, abs=0.01) and cal.sy == pytest.approx(720 / 616, abs=0.01)
    assert result.chosen.max_error_px < 2


def test_fit_tolerates_noise() -> None:
    s = session()
    rng = random.Random(3)
    points = [[tx + rng.uniform(-4, 4), ty + rng.uniform(-4, 4)] for tx, ty in s.targets]
    result = calibration.fit(s, points)
    assert result.chosen.space == "screenshot"
    assert 0 < result.chosen.max_error_px < result.tolerance_px


def test_fit_rejects_inconsistent_points() -> None:
    s = session()
    rng = random.Random(5)
    points = [[rng.uniform(0, 1280), rng.uniform(0, 720)] for _ in s.targets]
    with pytest.raises(calibration.CalibrationError, match="inconsistent.*0-1000 grid instead") as err:
        calibration.fit(s, points)
    assert "screenshot" not in str(err.value)


RUN1_POINTS = [[77, 120], [903, 100], [500, 360], [140, 600], [830, 600], [640, 240]]


def test_fit_recovers_observed_model_run_by_ignoring_copied_y() -> None:
    s = session()
    assert s.targets == ((102, 86), (1152, 65), (640, 360), (179, 619), (1062, 562), (819, 216))
    result = calibration.fit(s, RUN1_POINTS)
    assert result.quality == "good" and result.dropped == 5
    assert result.chosen.space == "calibrated" and result.chosen.max_error_px < result.tolerance_px
    assert result.chosen.calibration.sx == pytest.approx(1280 / 1000, abs=0.02)


def test_fit_accepts_spread_noise_as_usable() -> None:
    s = session()
    offsets = [(20, -18), (-22, 15), (18, 20), (-20, -16), (21, -19), (-19, 22)]
    points = [[tx + dx, ty + dy] for (tx, ty), (dx, dy) in zip(s.targets, offsets)]
    result = calibration.fit(s, points)
    assert result.quality == "usable" and result.dropped is None
    assert result.tolerance_px < result.chosen.max_error_px <= result.usable_tolerance_px


def test_fit_ignores_one_mislabeled_target() -> None:
    s = session()
    points = [list(t) for t in s.targets]
    points[3] = [900, 100]
    result = calibration.fit(s, points)
    assert result.quality == "good" and result.dropped == 4
    assert result.chosen.max_error_px < 1


RUN4_POINTS = [[77, 122], [1203, 101], [640, 360], [141, 598], [1101, 583], [870, 290]]


def test_fit_rejects_imprecise_pixel_guesses() -> None:
    with pytest.raises(calibration.CalibrationError, match="0-1000 grid instead"):
        calibration.fit(session(), RUN4_POINTS)


def test_fit_after_ignoring_outlier_keeps_size_independent_map() -> None:
    s = session()
    run5 = [[118, 118], [890, 95], [500, 490], [135, 855], [825, 775], [638, 295]]
    result = calibration.fit(s, run5)
    assert result.dropped == 1 and result.quality == "good"
    assert result.chosen.max_error_px < result.tolerance_px < result.chosen.errors_px[0] / 2
    cal = result.chosen.calibration
    assert cal.units == "absolute" and cal.sx == pytest.approx(0.001, rel=0.02)


def test_fit_prefers_named_convention_after_ignoring_outlier() -> None:
    s = session()
    points = [[round((tx + 0.5) / s.width * 1000), round((ty + 0.5) / s.height * 1000)] for tx, ty in s.targets]
    points[2] = [100, 900]
    result = calibration.fit(s, points)
    assert result.dropped == 3 and result.chosen.space == "normalized"
    assert result.chosen.max_error_px < 2


RUN6_POINTS = [[80, 120], [900, 100], [500, 360], [140, 600], [830, 580], [640, 280]]


def test_fit_prefers_builtin_convention_over_overfitted_free_fit() -> None:
    run7 = [[100, 120], [900, 100], [500, 500], [140, 860], [830, 780], [640, 300]]
    result = calibration.fit(session(), run7)
    assert result.quality == "good" and result.chosen.space == "normalized" and result.dropped == 1
    assert result.chosen.max_error_px < result.tolerance_px


def test_axis_mismatch_names_the_wrong_axis() -> None:
    hint = calibration.axis_mismatch(session(), RUN6_POINTS)
    assert hint is not None
    assert "x values match the 0-1000 normalized grid" in hint and "y values do not" in hint
    assert "0 to 1000" in hint and "top edge to the bottom edge" in hint


def test_axis_mismatch_none_for_consistent_or_random_points() -> None:
    s = session()
    assert calibration.axis_mismatch(s, [list(t) for t in s.targets]) is None
    rng = random.Random(5)
    assert calibration.axis_mismatch(s, [[rng.uniform(0, 1280), rng.uniform(0, 720)] for _ in s.targets]) is None


def test_fit_good_quality_reports_no_drop() -> None:
    result = calibration.fit(session(), [list(t) for t in session().targets])
    assert result.quality == "good" and result.dropped is None


def test_fit_detects_swapped_axes() -> None:
    s = session()
    with pytest.raises(calibration.CalibrationError, match=r"\[y, x\]"):
        calibration.fit(s, [[ty, tx] for tx, ty in s.targets])


def test_fit_validates_point_count_and_shape() -> None:
    s = session()
    with pytest.raises(calibration.CalibrationError, match="Expected 6 points"):
        calibration.fit(s, [[1, 2]])
    with pytest.raises(calibration.CalibrationError, match="Point 2"):
        calibration.fit(s, [[1, 2], [3]] + [[5, 6]] * 4)


# ------------------------------------------------------------------ server flow


def test_submit_requires_calibrate_first(fake_runtime) -> None:
    with pytest.raises(ToolError, match="Call calibrate first"):
        call("calibrate_submit", {"points": [[1, 2]] * 6})


def test_submit_bad_residual_changes_nothing(fake_runtime) -> None:
    rt, _, _ = fake_runtime
    call("calibrate")
    rng = random.Random(9)
    with pytest.raises(ToolError, match="inconsistent"):
        call("calibrate_submit", {"points": [[rng.uniform(0, 1280), rng.uniform(0, 720)] for _ in range(6)]})
    assert rt.config.settings.screenshot.coordinate_space == "screenshot"


def test_submit_usable_fit_is_applied_with_warning(fake_runtime) -> None:
    rt, _, _ = fake_runtime
    call("calibrate")
    offsets = [(20, -18), (-22, 15), (18, 20), (-20, -16), (21, -19), (-19, 22)]
    points = [[tx + dx, ty + dy] for (tx, ty), (dx, dy) in zip(rt.calibration_session.targets, offsets)]
    out = structured(call("calibrate_submit", {"points": points}))
    assert out["quality"] == "usable" and out["applied"] and "ignored_target" not in out
    assert out["max_error_screen_px"] == pytest.approx(out["max_error_px"] * 2, abs=0.2)
    assert "do not call calibrate again" in out["next"] and "Accuracy is limited" in out["next"]
    assert rt.config.settings.screenshot.coordinate_space == "screenshot"


def test_submit_mixed_axes_gets_one_targeted_retry(fake_runtime) -> None:
    rt, _, _ = fake_runtime
    call("calibrate")
    with pytest.raises(ToolError, match="y values do not"):
        call("calibrate_submit", {"points": RUN6_POINTS})
    assert rt.config.settings.screenshot.coordinate_space == "screenshot"
    corrected = [[80, 120], [900, 91], [500, 500], [140, 860], [830, 781], [640, 300]]
    out = structured(call("calibrate_submit", {"points": corrected}))
    assert out["coordinate_space"] == "normalized" and out["quality"] == "good"


def test_submit_mixed_axes_accepted_as_usable_on_second_try(fake_runtime) -> None:
    call("calibrate")
    with pytest.raises(ToolError):
        call("calibrate_submit", {"points": RUN6_POINTS})
    out = structured(call("calibrate_submit", {"points": RUN6_POINTS}))
    assert out["quality"] == "usable" and out["applied"]


def test_submit_reports_ignored_target(fake_runtime) -> None:
    call("calibrate")
    with pytest.raises(ToolError, match="x values match the 0-1000 normalized grid"):
        call("calibrate_submit", {"points": RUN1_POINTS})
    out = structured(call("calibrate_submit", {"points": RUN1_POINTS}))
    assert out["quality"] == "good" and out["ignored_target"] == 5 and "Target 5" in out["warning"]
    assert out["coordinate_space"] == "calibrated"


def test_submit_repeated_failures_tell_model_to_stop(fake_runtime) -> None:
    rt, _, _ = fake_runtime
    rng = random.Random(9)
    for attempt in range(1, 4):
        call("calibrate")
        with pytest.raises(ToolError) as err:
            call("calibrate_submit", {"points": [[rng.uniform(0, 1280), rng.uniform(0, 720)] for _ in range(6)]})
        assert ("stop calibrating" in str(err.value)) == (attempt == 3)
        assert "do not resubmit" in str(err.value)
    call("calibrate")
    call("calibrate_submit", {"points": [list(t) for t in rt.calibration_session.targets]})
    assert rt.calibration_failures == 0


def test_submit_builtin_convention_switches_space(fake_runtime) -> None:
    rt, _, _ = fake_runtime
    call("calibrate")
    s = rt.calibration_session
    points = [[round((tx + 0.5) / s.width * 1000), round((ty + 0.5) / s.height * 1000)] for tx, ty in s.targets]
    out = structured(call("calibrate_submit", {"points": points}))
    assert out["coordinate_space"] == "normalized" and out["applied"]
    assert out["persist"]["env"] == {"HUMANLIKE_SCREENSHOT__COORDINATE_SPACE": "normalized"}
    assert rt.config.settings.screenshot.coordinate_space == "normalized"


def test_submit_resized_convention_persists_and_survives_max_width(fake_runtime, tmp_path: Path) -> None:
    rt, _, backend = fake_runtime
    rt.range_errors = 3
    call("calibrate")
    s = rt.calibration_session
    out = structured(call("calibrate_submit", {"points": resized(s, 1092, 616)}))
    assert out["coordinate_space"] == "calibrated" and out["max_error_px"] < 2
    assert out["calibration"]["units"] == "image" and rt.range_errors == 0
    cfg = rt.config.settings.screenshot
    assert cfg.coordinate_space == "calibrated" and list(cfg.calibration) == out["calibration"]["values"]

    toml = out["persist"]["toml"]
    assert toml.startswith("[screenshot]\n") and 'coordinate_space = "calibrated"' in toml
    (tmp_path / "persisted").mkdir()
    (tmp_path / "persisted" / "humanlike.toml").write_text(toml, encoding="utf-8")
    from_file = ConfigManager(env={}, cwd=tmp_path / "persisted").settings.screenshot
    from_env = ConfigManager(env=out["persist"]["env"], cwd=tmp_path).settings.screenshot
    assert from_file == from_env == cfg

    rt.view = screen.View(SCREEN, 1280, 720)
    centre = [round(640.5 * 1092 / 1280 - 0.5), round(360.5 * 616 / 720 - 0.5)]
    click = structured(call("click", {"x": centre[0], "y": centre[1], "humanize": False}))
    assert click["coordinate_space"] == "calibrated"
    assert abs(backend.pos[0] - 1281) <= 3 and abs(backend.pos[1] - 721) <= 3
    assert click["cursor"]["calibrated"] is not None

    rt.view = screen.View(SCREEN, 640, 360)
    half = [round(320.5 * 1092 / 1280 - 0.5), round(180.5 * 616 / 720 - 0.5)]
    call("mouse_move", {"x": half[0], "y": half[1], "humanize": False})
    assert abs(backend.pos[0] - 1281) <= 6 and abs(backend.pos[1] - 721) <= 6


def test_calibrated_space_in_drag_and_scroll(fake_runtime) -> None:
    rt, rec, backend = fake_runtime
    call("set_config", {"updates": {
        "screenshot.coordinate_space": "calibrated",
        "screenshot.calibration": [1.0, 0.0, 1.0, 0.0],
        "screenshot.calibration_units": "absolute",
    }})
    rt.view = screen.View(SCREEN, 1280, 720)
    out = structured(call("drag", {"start_x": 0.25, "start_y": 0.5, "end_x": 0.75, "end_y": 0.5, "humanize": False}))
    assert out["coordinate_space"] == "calibrated"
    assert out["from"] == [640, 720] and out["to"] == [1920, 720]
    call("scroll", {"amount": 1, "x": 0.1, "y": 0.2, "humanize": False})
    assert backend.pos == (256, 288) and rec.of("scroll")
    with pytest.raises(ToolError, match=r"x 0\.\.1, y 0\.\.1"):
        call("click", {"x": 1.5, "y": 0.5, "humanize": False})


def test_calibrated_space_needs_screenshot(fake_runtime) -> None:
    call("set_config", {"updates": {"screenshot.coordinate_space": "calibrated"}})
    with pytest.raises(ToolError, match="needs a screenshot first"):
        call("click", {"x": 10, "y": 10})


def test_repeated_range_errors_suggest_calibration(fake_runtime) -> None:
    rt, _, _ = fake_runtime
    rt.view = screen.View(SCREEN, 1280, 720)
    with pytest.raises(ToolError, match="If this repeats.*calibrate") as first:
        call("click", {"x": 5000, "y": 10, "humanize": False})
    assert "out-of-range call" not in str(first.value)
    with pytest.raises(ToolError, match="out-of-range call 2.*calibrate, then calibrate_submit"):
        call("click", {"x": 5000, "y": 10, "humanize": False})


def test_calibration_vector_setting_validation() -> None:
    settings = ConfigManager(env={"HUMANLIKE_SCREENSHOT__CALIBRATION": "1.2, -0.01, 1.1, 0"}).settings
    assert settings.screenshot.calibration == (1.2, -0.01, 1.1, 0.0)
    manager = ConfigManager(env={})
    with pytest.raises(ConfigError):
        manager.set({"screenshot.calibration": [1, 2, 3]})
    with pytest.raises(ConfigError):
        manager.set({"screenshot.calibration": [0, 0, 1, 0]})
    with pytest.raises(ConfigError):
        manager.set({"screenshot.calibration_units": "pixels"})
    assert Settings().debug.log_path == ""


# ------------------------------------------------------------------ debug log


def test_debug_log_records_mapping_and_redacts_text(fake_runtime, tmp_path: Path) -> None:
    rt, _, _ = fake_runtime
    log = tmp_path / "logs" / "debug.jsonl"
    call("set_config", {"updates": {"debug.log_path": str(log)}})
    rt.view = screen.View(SCREEN, 1280, 720)
    call("click", {"x": 100, "y": 50, "humanize": False})
    with pytest.raises(ToolError):
        call("click", {"x": 2000, "y": 50, "humanize": False})
    call("type_text", {"text": "secret pw", "typo_rate": 0, "humanize": False})
    call("calibrate")
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    events = [r["event"] for r in records]
    assert events == ["mouse", "mouse", "type_text", "calibrate"]

    ok, bad, typed, cal = records
    assert ok["tool"] == "click" and ok["coordinate_space"] == "screenshot"
    assert ok["points"] == [{"raw": [100.0, 50.0], "in_bounds": True, "screen": [201, 101]}]
    assert ok["view"] == {"image": [1280, 720], "region": SCREEN.as_dict()}
    assert bad["points"][0]["in_bounds"] is False and "outside the valid range" in bad["error"]
    assert typed == {"ts": typed["ts"], "event": "type_text", "text_length": 9}
    assert "secret" not in log.read_text(encoding="utf-8")
    assert cal["image"] == [1280, 720] and len(cal["targets"]) == 6

    call("set_config", {"updates": {"debug.log_text": True}})
    call("type_text", {"text": "shown", "typo_rate": 0, "humanize": False})
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[-1])["text"] == "shown"


def test_debug_log_off_by_default(fake_runtime, tmp_path: Path) -> None:
    rt, _, _ = fake_runtime
    rt.view = screen.View(SCREEN, 1280, 720)
    call("click", {"x": 100, "y": 50, "humanize": False})
    assert not list(tmp_path.rglob("*.jsonl"))


def test_debug_log_screenshot_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder()
    backend = FakeMouseBackend(rec, start=(400, 400))
    mouse = Mouse(backend, rng=random.Random(1), sleep=rec.sleep)
    keyboard = Keyboard(FakeKeyboardBackend(rec), rng=random.Random(2), sleep=rec.sleep)
    log = tmp_path / "shot.jsonl"
    env = {"HUMANLIKE_DEBUG__LOG_PATH": str(log), "HUMANLIKE_SCREENSHOT__COORDINATE_SPACE": "normalized"}
    server.set_runtime(server.Runtime(config=ConfigManager(env=env, cwd=tmp_path), mouse=mouse, keyboard=keyboard))
    try:
        call("screenshot", {"max_width": 320})
    finally:
        server.set_runtime(None)
    record = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert record["event"] == "screenshot" and record["coordinate_space"] == "normalized"
    assert record["image"][0] <= 320 and set(record["region"]) == {"left", "top", "width", "height"}
    assert len(record["scale"]) == 2
