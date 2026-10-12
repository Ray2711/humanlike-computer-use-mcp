from __future__ import annotations

from pathlib import Path

import pytest

from humanlike_mcp.config import (
    FIELDS,
    PRESETS,
    ConfigError,
    ConfigManager,
    ScrollSettings,
    Settings,
    TypingSettings,
    env_var_name,
    parse_env,
)

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_natural_defaults_match_plan() -> None:
    s = Settings()
    assert (s.mouse.gravity, s.mouse.wind, s.mouse.max_step, s.mouse.damped_distance) == (9, 3, 15, 12)
    assert s.mouse.overshoot_probability == 0.3 and s.mouse.overshoot_min_distance == 300
    assert s.click.hold_ms == (60, 140) and s.click.pre_click_pause_ms == (50, 250)
    assert s.drag.speed_factor == 0.7
    assert s.typing.wpm == 70 and s.typing.typo_rate == 0.02 and s.typing.layout == "qwerty"
    assert s.keys.modifier_gap_ms == (20, 60)
    assert s.safety.failsafe is True and s.safety.max_text_length == 10000
    assert s.humanize.enabled is True


def test_example_toml_lists_every_setting_with_defaults(tmp_path: Path) -> None:
    manager = ConfigManager(env={}, config_path=ROOT / "humanlike.example.toml", cwd=tmp_path)
    assert manager.settings == Settings()
    text = (ROOT / "humanlike.example.toml").read_text(encoding="utf-8")
    for key in FIELDS:
        assert f"{key.split('.')[1]} =" in text, key


@pytest.mark.parametrize("name", list(PRESETS))
def test_presets_are_valid(name: str, config: ConfigManager) -> None:
    config.use_preset(name)
    assert config.preset == name
    for key, value in PRESETS[name].items():
        assert config.settings.flat()[key] == value


def test_preset_characteristics() -> None:
    careful = Settings().with_updates(PRESETS["careful"])
    fast = Settings().with_updates(PRESETS["fast"])
    instant = Settings().with_updates(PRESETS["instant"])
    assert careful.typing.wpm < Settings().typing.wpm < fast.typing.wpm
    assert careful.typing.typo_rate < Settings().typing.typo_rate
    assert careful.mouse.overshoot_probability < Settings().mouse.overshoot_probability
    assert instant.humanize.enabled is False


def test_file_layer_and_sources(tmp_path: Path) -> None:
    write(tmp_path / "humanlike.toml", 'preset = "fast"\n[typing]\nwpm = 85\n[click]\nhold_ms = [70, 90]\n')
    m = ConfigManager(env={}, cwd=tmp_path)
    assert m.preset == "fast"
    assert m.settings.typing.wpm == 85
    assert m.settings.click.hold_ms == (70, 90)
    assert m.settings.scroll.tick_gap_ms == PRESETS["fast"]["scroll.tick_gap_ms"]
    d = m.describe()
    assert d["settings"]["typing"]["wpm"]["source"].startswith("file:")
    assert d["settings"]["scroll"]["tick_gap_ms"]["source"] == "preset:fast"
    assert d["settings"]["mouse"]["gravity"]["source"] == "default"
    assert d["settings"]["click"]["hold_ms"]["value"] == [70, 90]


def test_config_path_from_env(tmp_path: Path) -> None:
    cfg = write(tmp_path / "custom.toml", "[mouse]\nwind = 5\n")
    m = ConfigManager(env={"HUMANLIKE_CONFIG": str(cfg)}, cwd=tmp_path / "elsewhere")
    assert m.settings.mouse.wind == 5
    assert m.config_path == cfg


def test_missing_explicit_config_file_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        ConfigManager(env={"HUMANLIKE_CONFIG": str(tmp_path / "nope.toml")}, cwd=tmp_path)


def test_bad_toml_errors(tmp_path: Path) -> None:
    write(tmp_path / "humanlike.toml", "[typing\nwpm = 3")
    with pytest.raises(ConfigError, match="Invalid TOML"):
        ConfigManager(env={}, cwd=tmp_path)
    write(tmp_path / "humanlike.toml", "[typing]\nwords_per_minute = 3")
    with pytest.raises(ConfigError, match="unknown setting"):
        ConfigManager(env={}, cwd=tmp_path)
    write(tmp_path / "humanlike.toml", "wpm = 3")
    with pytest.raises(ConfigError, match="must be a table"):
        ConfigManager(env={}, cwd=tmp_path)


def test_full_layer_order(tmp_path: Path) -> None:
    write(tmp_path / "humanlike.toml", 'preset = "careful"\n[typing]\nwpm = 50\ntypo_rate = 0.01\n[mouse]\nwind = 4\n')
    env = {"HUMANLIKE_TYPING__WPM": "60", "HUMANLIKE_MOUSE__SPEED": "1.2"}
    m = ConfigManager(env=env, cwd=tmp_path)
    assert m.settings.mouse.overshoot_probability == 0.15  # preset
    assert m.settings.mouse.wind == 4  # file beats preset (careful sets 2)
    assert m.settings.typing.wpm == 60  # env beats file
    m.set({"typing.wpm": 65})
    assert m.settings.typing.wpm == 65  # runtime beats env
    call = m.effective({"typing.wpm": 90, "mouse.speed": None})
    assert call.typing.wpm == 90 and call.mouse.speed == 1.2  # per-call beats runtime; None ignored
    assert m.settings.typing.wpm == 65  # per-call is not persisted
    m.reset()
    assert m.settings.typing.wpm == 60
    assert m.describe()["settings"]["typing"]["wpm"]["source"] == "env"


def test_env_preset_beats_file_preset(tmp_path: Path) -> None:
    write(tmp_path / "humanlike.toml", 'preset = "careful"\n')
    m = ConfigManager(env={"HUMANLIKE_PRESET": "fast"}, cwd=tmp_path)
    assert m.preset == "fast"


def test_env_parsing_types() -> None:
    env = {
        "HUMANLIKE_SAFETY__FAILSAFE": "0",
        "HUMANLIKE_HUMANIZE__ENABLED": "yes",
        "HUMANLIKE_CLICK__HOLD_MS": "70,120",
        "HUMANLIKE_TYPING__KEY_HOLD_MS": "[20, 40]",
        "HUMANLIKE_SCROLL__TICK_GAP_MS": "50",
        "HUMANLIKE_SAFETY__MAX_TEXT_LENGTH": "500",
        "HUMANLIKE_TYPING__LAYOUT": "qwerty",
        "HUMANLIKE_LIVE_TESTS": "1",
        "PATH": "x",
    }
    values = parse_env(env)
    assert values == {
        "safety.failsafe": False,
        "humanize.enabled": True,
        "click.hold_ms": (70.0, 120.0),
        "typing.key_hold_ms": (20.0, 40.0),
        "scroll.tick_gap_ms": (50.0, 50.0),
        "safety.max_text_length": 500,
        "typing.layout": "qwerty",
    }
    s = Settings().with_updates(values)
    assert s.safety.max_text_length == 500 and isinstance(s.safety.max_text_length, int)


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"HUMANLIKE_TYPING__SPEED": "3"}, "Unknown setting"),
        ({"HUMANLIKE_SAFETY__FAILSAFE": "maybe"}, "boolean"),
        ({"HUMANLIKE_TYPING__WPM": "fast"}, "number"),
        ({"HUMANLIKE_TYPING__WPM": "1000"}, "maximum"),
        ({"HUMANLIKE_PRESET": "turbo"}, "Unknown preset"),
    ],
)
def test_env_errors(tmp_path: Path, env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        ConfigManager(env=env, cwd=tmp_path)


def test_env_var_names() -> None:
    assert env_var_name("typing.wpm") == "HUMANLIKE_TYPING__WPM"
    assert env_var_name("mouse.overshoot_max_px") == "HUMANLIKE_MOUSE__OVERSHOOT_MAX_PX"


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"typing.wpm": -5}, "minimum"),
        ({"typing.wpm": True}, "finite number"),
        ({"click.hold_ms": [200, 100]}, "greater than max"),
        ({"click.hold_ms": [1, 2, 3]}, r"\[min, max\]"),
        ({"mouse.overshoot_probability": 1.5}, "maximum"),
        ({"typing.layout": "dvorak"}, "not one of"),
        ({"safety.max_text_length": 2.5}, "integer"),
        ({"nope.key": 1}, "Unknown setting"),
        ({"typing.wpm": float("nan")}, "finite"),
    ],
)
def test_validation_errors(config: ConfigManager, updates: dict, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        config.set(updates)


def test_direct_dataclass_validation() -> None:
    with pytest.raises(ConfigError):
        TypingSettings(wpm=0)
    with pytest.raises(ConfigError):
        ScrollSettings(ramp="yes")  # type: ignore[arg-type]


def test_set_config_is_atomic_and_reports(config: ConfigManager) -> None:
    with pytest.raises(ConfigError):
        config.set({"typing.wpm": 55, "typing.typo_rate": 9})
    assert config.settings.typing.wpm == 70
    out = config.set({"Typing.WPM": "55", "click.hold_ms": 90})
    assert out == {"typing.wpm": 55.0, "click.hold_ms": [90.0, 90.0]}
    assert config.describe()["settings"]["typing"]["wpm"]["source"] == "runtime"
    with pytest.raises(ConfigError):
        config.set({})


def test_use_preset_and_reset(config: ConfigManager) -> None:
    config.set({"typing.wpm": 33})
    config.use_preset("FAST")
    assert config.preset == "fast"
    assert config.settings.typing.wpm == 100  # runtime overrides cleared by use_preset
    config.set({"mouse.wind": 6})
    config.reset()
    assert config.preset == "natural"
    assert config.settings == Settings()
    with pytest.raises(ConfigError):
        config.use_preset("nope")
    assert config.preset == "natural"


def test_settings_are_frozen() -> None:
    s = Settings()
    with pytest.raises(Exception):
        s.typing.wpm = 10  # type: ignore[misc]
