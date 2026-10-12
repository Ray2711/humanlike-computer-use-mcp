"""Typed settings, presets and layered configuration loading.

Layer order (later wins): built-in defaults < preset < TOML file < environment
variables < runtime ``set_config`` < per-call overrides.
"""

from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, ClassVar, Mapping

Range = tuple[float, float]

ENV_PREFIX = "HUMANLIKE_"
ENV_PRESET = "HUMANLIKE_PRESET"
ENV_CONFIG = "HUMANLIKE_CONFIG"
DEFAULT_CONFIG_NAME = "humanlike.toml"

_TRUE = {"1", "true", "yes", "on", "y", "t"}
_FALSE = {"0", "false", "no", "off", "n", "f"}


class ConfigError(ValueError):
    """Raised for unknown keys, unparsable values and out-of-range settings."""


def _opt(default: Any, lo: float | None = None, hi: float | None = None, *, choices: tuple[str, ...] = ()) -> Any:
    return field(default=default, metadata={"lo": lo, "hi": hi, "choices": choices})


def _kind(default: Any) -> str:
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, int):
        return "int"
    if isinstance(default, float):
        return "float"
    if isinstance(default, tuple):
        return "range"
    return "str"


@dataclass(frozen=True)
class _Section:
    section_name: ClassVar[str] = ""

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            normalized = _validate(f"{self.section_name}.{f.name}", _kind(f.default), value, f.metadata)
            object.__setattr__(self, f.name, normalized)


def _check_bounds(key: str, value: float, meta: Mapping[str, Any]) -> None:
    lo, hi = meta.get("lo"), meta.get("hi")
    if lo is not None and value < lo:
        raise ConfigError(f"{key}: {value} is below the minimum {lo}")
    if hi is not None and value > hi:
        raise ConfigError(f"{key}: {value} is above the maximum {hi}")


def _validate(key: str, kind: str, value: Any, meta: Mapping[str, Any]) -> Any:
    if kind == "bool":
        if not isinstance(value, bool):
            raise ConfigError(f"{key}: expected a boolean, got {value!r}")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{key}: expected an integer, got {value!r}")
        _check_bounds(key, value, meta)
        return value
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ConfigError(f"{key}: expected a finite number, got {value!r}")
        _check_bounds(key, float(value), meta)
        return float(value)
    if kind == "range":
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            raise ConfigError(f"{key}: expected a [min, max] pair, got {value!r}")
        lo, hi = (_validate(key, "float", v, meta) for v in value)
        if lo > hi:
            raise ConfigError(f"{key}: min {lo} is greater than max {hi}")
        return (lo, hi)
    if not isinstance(value, str):
        raise ConfigError(f"{key}: expected a string, got {value!r}")
    choices = meta.get("choices") or ()
    if choices and value not in choices:
        raise ConfigError(f"{key}: {value!r} is not one of {list(choices)}")
    return value


@dataclass(frozen=True)
class MouseSettings(_Section):
    section_name: ClassVar[str] = "mouse"
    gravity: float = _opt(9.0, 0.1, 100.0)
    wind: float = _opt(3.0, 0.0, 100.0)
    max_step: float = _opt(15.0, 1.0, 500.0)
    damped_distance: float = _opt(12.0, 1.0, 1000.0)
    tick_delay: float = _opt(0.012, 0.0, 0.2)
    speed: float = _opt(1.0, 0.05, 20.0)
    ease: bool = _opt(True)
    arc_strength: float = _opt(0.04, 0.0, 0.5)
    overshoot_enabled: bool = _opt(True)
    overshoot_min_distance: float = _opt(300.0, 0.0, 100000.0)
    overshoot_probability: float = _opt(0.3, 0.0, 1.0)
    overshoot_max_px: float = _opt(25.0, 0.0, 1000.0)
    landing_jitter_px: float = _opt(2.0, 0.0, 100.0)


@dataclass(frozen=True)
class ClickSettings(_Section):
    section_name: ClassVar[str] = "click"
    hold_ms: Range = _opt((60.0, 140.0), 0.0, 10000.0)
    pre_click_pause_ms: Range = _opt((50.0, 250.0), 0.0, 10000.0)
    double_click_gap_ms: Range = _opt((80.0, 180.0), 0.0, 2000.0)


@dataclass(frozen=True)
class DragSettings(_Section):
    section_name: ClassVar[str] = "drag"
    pre_move_hold_ms: Range = _opt((80.0, 200.0), 0.0, 10000.0)
    release_dwell_ms: Range = _opt((60.0, 180.0), 0.0, 10000.0)
    speed_factor: float = _opt(0.7, 0.05, 10.0)


@dataclass(frozen=True)
class ScrollSettings(_Section):
    section_name: ClassVar[str] = "scroll"
    tick_gap_ms: Range = _opt((20.0, 90.0), 0.0, 5000.0)
    ramp: bool = _opt(True)
    flick_notches: Range = _opt((3.0, 6.0), 1.0, 100.0)
    regrip_pause_ms: Range = _opt((150.0, 400.0), 0.0, 10000.0)


@dataclass(frozen=True)
class TypingSettings(_Section):
    section_name: ClassVar[str] = "typing"
    wpm: float = _opt(70.0, 5.0, 300.0)
    wpm_variance: float = _opt(0.25, 0.0, 2.0)
    key_hold_ms: Range = _opt((30.0, 90.0), 0.0, 2000.0)
    bigram_speedup: float = _opt(0.8, 0.1, 1.0)
    shift_extra_ms: Range = _opt((40.0, 90.0), 0.0, 2000.0)
    space_pause_ms: Range = _opt((80.0, 250.0), 0.0, 5000.0)
    punctuation_pause_ms: Range = _opt((150.0, 450.0), 0.0, 5000.0)
    newline_pause_ms: Range = _opt((300.0, 800.0), 0.0, 10000.0)
    think_pause_probability: float = _opt(0.03, 0.0, 1.0)
    think_pause_ms: Range = _opt((400.0, 1500.0), 0.0, 30000.0)
    typo_rate: float = _opt(0.02, 0.0, 0.5)
    typo_notice_ms: Range = _opt((200.0, 600.0), 0.0, 10000.0)
    layout: str = _opt("qwerty", choices=("qwerty",))


@dataclass(frozen=True)
class KeysSettings(_Section):
    section_name: ClassVar[str] = "keys"
    modifier_gap_ms: Range = _opt((20.0, 60.0), 0.0, 2000.0)
    key_hold_ms: Range = _opt((40.0, 100.0), 0.0, 5000.0)
    repeat_gap_ms: Range = _opt((60.0, 160.0), 0.0, 5000.0)


@dataclass(frozen=True)
class SafetySettings(_Section):
    section_name: ClassVar[str] = "safety"
    failsafe: bool = _opt(True)
    max_text_length: int = _opt(10000, 1, 1_000_000)


@dataclass(frozen=True)
class HumanizeSettings(_Section):
    section_name: ClassVar[str] = "humanize"
    enabled: bool = _opt(True)


SECTIONS: dict[str, type[_Section]] = {
    cls.section_name: cls
    for cls in (
        MouseSettings,
        ClickSettings,
        DragSettings,
        ScrollSettings,
        TypingSettings,
        KeysSettings,
        SafetySettings,
        HumanizeSettings,
    )
}


@dataclass(frozen=True)
class Settings:
    mouse: MouseSettings = field(default_factory=MouseSettings)
    click: ClickSettings = field(default_factory=ClickSettings)
    drag: DragSettings = field(default_factory=DragSettings)
    scroll: ScrollSettings = field(default_factory=ScrollSettings)
    typing: TypingSettings = field(default_factory=TypingSettings)
    keys: KeysSettings = field(default_factory=KeysSettings)
    safety: SafetySettings = field(default_factory=SafetySettings)
    humanize: HumanizeSettings = field(default_factory=HumanizeSettings)

    def flat(self) -> dict[str, Any]:
        return {
            f"{name}.{f.name}": getattr(getattr(self, name), f.name)
            for name, cls in SECTIONS.items()
            for f in fields(cls)
        }

    def with_updates(self, updates: Mapping[str, Any]) -> Settings:
        """Return a validated copy with dotted-key updates applied."""
        if not updates:
            return self
        grouped: dict[str, dict[str, Any]] = {}
        for key, raw in updates.items():
            section, name = split_key(key)
            grouped.setdefault(section, {})[name] = coerce(key, raw)
        changes = {section: replace(getattr(self, section), **vals) for section, vals in grouped.items()}
        return replace(self, **changes)


def _field_map() -> dict[str, Any]:
    return {f"{name}.{f.name}": f for name, cls in SECTIONS.items() for f in fields(cls)}


FIELDS = _field_map()


def split_key(key: str) -> tuple[str, str]:
    normalized = key.strip().lower()
    if normalized not in FIELDS:
        raise ConfigError(f"Unknown setting {key!r}. Valid keys: {', '.join(sorted(FIELDS))}")
    section, _, name = normalized.partition(".")
    return section, name


def _parse_number(key: str, text: str) -> float:
    try:
        return float(text.strip())
    except ValueError:
        raise ConfigError(f"{key}: cannot parse {text!r} as a number") from None


def coerce(key: str, value: Any) -> Any:
    """Convert loosely typed input (env strings, JSON numbers, lists) to the field's type."""
    section, name = split_key(key)
    f = FIELDS[f"{section}.{name}"]
    kind = _kind(f.default)
    if kind == "bool":
        if isinstance(value, str):
            low = value.strip().lower()
            if low in _TRUE:
                return True
            if low in _FALSE:
                return False
            raise ConfigError(f"{key}: cannot parse {value!r} as a boolean")
        if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
            return bool(value)
        return value
    if kind == "int":
        if isinstance(value, str):
            value = _parse_number(key, value)
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
    if kind == "float":
        if isinstance(value, str):
            return _parse_number(key, value)
        return value
    if kind == "range":
        if isinstance(value, str):
            parts = [p for p in value.strip().strip("[]()").split(",") if p.strip()]
            nums = [_parse_number(key, p) for p in parts]
            value = nums
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (float(value), float(value))
        if isinstance(value, (list, tuple)) and len(value) == 1:
            return (value[0], value[0])
        if isinstance(value, list):
            return tuple(value)
        return value
    return value.strip() if isinstance(value, str) else value


PRESETS: dict[str, dict[str, Any]] = {
    "natural": {},
    "careful": {
        "mouse.wind": 2.0,
        "mouse.speed": 0.75,
        "mouse.overshoot_probability": 0.15,
        "mouse.landing_jitter_px": 1.0,
        "click.pre_click_pause_ms": (120.0, 350.0),
        "click.hold_ms": (70.0, 150.0),
        "drag.speed_factor": 0.55,
        "scroll.tick_gap_ms": (40.0, 130.0),
        "typing.wpm": 45.0,
        "typing.typo_rate": 0.008,
        "typing.punctuation_pause_ms": (200.0, 550.0),
        "typing.think_pause_probability": 0.05,
    },
    "fast": {
        "mouse.speed": 1.6,
        "mouse.overshoot_probability": 0.35,
        "click.pre_click_pause_ms": (20.0, 120.0),
        "click.hold_ms": (45.0, 100.0),
        "click.double_click_gap_ms": (60.0, 140.0),
        "drag.pre_move_hold_ms": (50.0, 120.0),
        "drag.release_dwell_ms": (40.0, 110.0),
        "drag.speed_factor": 0.85,
        "scroll.tick_gap_ms": (10.0, 50.0),
        "typing.wpm": 100.0,
        "typing.key_hold_ms": (25.0, 70.0),
        "typing.space_pause_ms": (40.0, 150.0),
        "typing.punctuation_pause_ms": (80.0, 250.0),
        "typing.newline_pause_ms": (150.0, 450.0),
        "typing.think_pause_probability": 0.01,
        "typing.typo_rate": 0.015,
        "keys.modifier_gap_ms": (10.0, 40.0),
        "keys.repeat_gap_ms": (40.0, 110.0),
    },
    "instant": {
        "humanize.enabled": False,
        "typing.typo_rate": 0.0,
    },
}

PRESET_DESCRIPTIONS: dict[str, str] = {
    "natural": "Default. Ordinary human behaviour: ~70 WPM with occasional typos, curved mouse paths with some overshoot.",
    "careful": "Slower and more precise: ~45 WPM, fewer typos, calmer mouse with fewer overshoots.",
    "fast": "Skilled power user: ~100 WPM, faster mouse, shorter pauses.",
    "instant": "Humanization off: instant moves, no delays, no typos. For debugging or speed.",
}


def parse_env(env: Mapping[str, str]) -> dict[str, Any]:
    """Collect ``HUMANLIKE_SECTION__KEY`` variables as dotted, coerced settings."""
    values: dict[str, Any] = {}
    for var, raw in env.items():
        upper = var.upper()
        if not upper.startswith(ENV_PREFIX) or "__" not in upper:
            continue
        section, _, name = upper[len(ENV_PREFIX):].partition("__")
        key = f"{section.lower()}.{name.lower()}"
        if key not in FIELDS:
            raise ConfigError(f"Unknown setting {key!r} from environment variable {var}")
        values[key] = coerce(key, raw)
    return values


def flatten_toml(data: Mapping[str, Any], origin: str = "config file") -> dict[str, Any]:
    values: dict[str, Any] = {}
    for section, table in data.items():
        if section == "preset":
            continue
        if not isinstance(table, Mapping):
            raise ConfigError(f"{origin}: top-level key {section!r} must be a table like [{section}]")
        for name, raw in table.items():
            key = f"{section}.{name}".lower()
            if key not in FIELDS:
                raise ConfigError(f"{origin}: unknown setting {key!r}")
            values[key] = coerce(key, raw)
    return values


def _check_preset(name: str) -> str:
    normalized = name.strip().lower()
    if normalized not in PRESETS:
        raise ConfigError(f"Unknown preset {name!r}. Available: {', '.join(PRESETS)}")
    return normalized


def _jsonable(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


class ConfigManager:
    """Holds the layered configuration for one server session."""

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        config_path: str | Path | None = None,
        cwd: str | Path | None = None,
    ) -> None:
        self._env = dict(os.environ if env is None else env)
        self._explicit_path = Path(config_path) if config_path else None
        self._cwd = Path(cwd) if cwd else Path.cwd()
        self._runtime: dict[str, Any] = {}
        self._runtime_preset: str | None = None
        self.reload()

    def _resolve_path(self) -> Path | None:
        if self._explicit_path is not None:
            return self._explicit_path
        env_path = self._env.get(ENV_CONFIG)
        if env_path:
            return Path(env_path).expanduser()
        candidate = self._cwd / DEFAULT_CONFIG_NAME
        return candidate if candidate.is_file() else None

    def reload(self) -> None:
        """Re-read the config file and environment; keeps runtime changes."""
        path = self._resolve_path()
        data: dict[str, Any] = {}
        if path is not None:
            if not path.is_file():
                raise ConfigError(f"Config file not found: {path}")
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc
        file_preset = data.get("preset")
        if file_preset is not None and not isinstance(file_preset, str):
            raise ConfigError(f"{path}: 'preset' must be a string")
        self._config_path = path
        self._file_preset = _check_preset(file_preset) if file_preset else None
        self._file_values = flatten_toml(data, str(path))
        env_preset = self._env.get(ENV_PRESET)
        self._env_preset = _check_preset(env_preset) if env_preset else None
        self._env_values = parse_env(self._env)
        self._rebuild(self._runtime)

    @property
    def preset(self) -> str:
        return self._runtime_preset or self._env_preset or self._file_preset or "natural"

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    @property
    def settings(self) -> Settings:
        return self._settings

    def _layers(self, runtime: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
        return [
            (f"preset:{self.preset}", PRESETS[self.preset]),
            (f"file:{self._config_path}", self._file_values),
            ("env", self._env_values),
            ("runtime", runtime),
        ]

    def _rebuild(self, runtime: Mapping[str, Any]) -> None:
        sources = {key: "default" for key in FIELDS}
        merged: dict[str, Any] = {}
        for label, layer in self._layers(runtime):
            for key, value in layer.items():
                merged[key] = value
                sources[key] = label
        self._settings = Settings().with_updates(merged)
        self._sources = sources

    def set(self, updates: Mapping[str, Any]) -> dict[str, Any]:
        """Apply validated runtime changes; nothing is applied if any value is invalid."""
        if not isinstance(updates, Mapping) or not updates:
            raise ConfigError("set_config expects a non-empty object of {'section.key': value}")
        coerced = {".".join(split_key(k)): coerce(k, v) for k, v in updates.items()}
        candidate = {**self._runtime, **coerced}
        self._rebuild(candidate)
        self._runtime = candidate
        flat = self._settings.flat()
        return {key: _jsonable(flat[key]) for key in coerced}

    def reset(self) -> None:
        self._runtime = {}
        self._runtime_preset = None
        self._rebuild(self._runtime)

    def use_preset(self, name: str) -> None:
        """Switch the preset layer for this session; clears runtime overrides."""
        previous = self._runtime_preset
        self._runtime_preset = _check_preset(name)
        try:
            self._rebuild({})
        except ConfigError:
            self._runtime_preset = previous
            raise
        self._runtime = {}

    def effective(self, overrides: Mapping[str, Any] | None = None) -> Settings:
        """Settings for one call: current settings plus per-call overrides."""
        clean = {k: v for k, v in (overrides or {}).items() if v is not None}
        return self._settings.with_updates(clean)

    def describe(self) -> dict[str, Any]:
        flat = self._settings.flat()
        grouped: dict[str, dict[str, Any]] = {}
        for key, value in flat.items():
            section, _, name = key.partition(".")
            grouped.setdefault(section, {})[name] = {"value": _jsonable(value), "source": self._sources[key]}
        return {
            "preset": self.preset,
            "config_file": str(self._config_path) if self._config_path else None,
            "settings": grouped,
        }


def env_var_name(key: str) -> str:
    section, name = split_key(key)
    return f"{ENV_PREFIX}{section.upper()}__{name.upper()}"
