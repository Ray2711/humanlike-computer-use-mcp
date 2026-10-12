from __future__ import annotations

import random

import pytest

from humanlike_mcp.config import Settings
from humanlike_mcp.errors import FailSafeTriggered
from humanlike_mcp.keyboard import Keyboard, normalize_key, parse_hotkey

from .conftest import FakeKeyboardBackend, Recorder, typed_text


def keys(rec: Recorder) -> list[tuple[str, str]]:
    return [(e[0], e[1]) for e in rec.events if e[0] in ("press", "release")]


@pytest.mark.parametrize("seed", range(10))
def test_typed_text_matches_input_even_with_typos(seed: int, rec: Recorder, settings: Settings) -> None:
    kb = Keyboard(FakeKeyboardBackend(rec), rng=random.Random(seed), sleep=rec.sleep)
    text = "Hello, World! Typing (with typos) & SHIFTED symbols: 100% done?\nNext line\ttab."
    result = kb.type_text(text, settings, typo_rate=0.25)
    assert typed_text(rec.events) == text
    assert result["characters"] == len(text)
    presses = [k for k in keys(rec) if k[0] == "press" and k[1] != "shift"]
    assert result["keystrokes"] == len(presses)


def test_every_press_has_matching_release(keyboard: Keyboard, rec: Recorder, settings: Settings) -> None:
    keyboard.type_text("Mixed CASE text, with Punctuation!", settings, typo_rate=0.2)
    depth: dict[str, int] = {}
    for kind, key in keys(rec):
        depth[key] = depth.get(key, 0) + (1 if kind == "press" else -1)
        assert depth[key] in (0, 1)
    assert all(v == 0 for v in depth.values())


def test_shift_is_pressed_around_capitals_and_held_across_runs(
    keyboard: Keyboard, rec: Recorder, settings: Settings
) -> None:
    keyboard.type_text("aHELLOb", settings, typo_rate=0)
    seq = keys(rec)
    assert seq.count(("press", "shift")) == 1
    i_shift = seq.index(("press", "shift"))
    assert seq[i_shift + 1] == ("press", "h")
    i_release = seq.index(("release", "shift"))
    assert seq[i_release - 1] == ("release", "o")
    assert seq[i_release + 1] == ("press", "b")
    lead = next(e for e in rec.events[rec.events.index(("press", "shift")) + 1 :])
    assert lead[0] == "sleep" and 0.040 <= lead[1] <= 0.090


def test_typo_then_backspace_sequence(rec: Recorder, settings: Settings) -> None:
    kb = Keyboard(FakeKeyboardBackend(rec), rng=random.Random(4), sleep=rec.sleep)
    result = kb.type_text("the quick brown fox", settings, typo_rate=0.4)
    seq = [k for kind, k in keys(rec) if kind == "press"]
    assert "backspace" in seq
    assert result["corrections"] == seq.count("backspace")
    assert result["typo_keystrokes"] >= 1
    first_bs = seq.index("backspace")
    assert typed_text(rec.events) == "the quick brown fox"
    notice_wait = [
        e for e in rec.events[: rec.events.index(("press", "backspace"))] if e[0] in ("sleep", "release")
    ][-1]
    assert notice_wait[0] == "sleep" and notice_wait[1] > 0.1
    assert first_bs > 0


def test_unicode_characters_pass_through(keyboard: Keyboard, rec: Recorder, settings: Settings) -> None:
    keyboard.type_text("café ✓ 日本", settings, typo_rate=0)
    assert typed_text(rec.events) == "café ✓ 日本"


def test_instant_mode_has_no_sleeps(keyboard: Keyboard, rec: Recorder, settings: Settings) -> None:
    s = settings.with_updates({"humanize.enabled": False})
    keyboard.type_text("Fast TEXT!", s)
    assert rec.slept == 0
    assert typed_text(rec.events) == "Fast TEXT!"


def test_max_text_length_and_argument_validation(keyboard: Keyboard, settings: Settings) -> None:
    s = settings.with_updates({"safety.max_text_length": 5})
    with pytest.raises(ValueError, match="limit is 5"):
        keyboard.type_text("too long", s)
    with pytest.raises(ValueError):
        keyboard.type_text("hi", settings, wpm=1000)
    with pytest.raises(ValueError):
        keyboard.type_text("hi", settings, typo_rate=0.9)


def test_failsafe_aborts_typing_and_releases_shift(rec: Recorder, settings: Settings) -> None:
    calls = {"n": 0}

    def check() -> None:
        calls["n"] += 1
        if calls["n"] > 3:
            raise FailSafeTriggered()

    kb = Keyboard(FakeKeyboardBackend(rec), rng=random.Random(1), sleep=rec.sleep, failsafe_check=check)
    with pytest.raises(FailSafeTriggered):
        kb.type_text("ABCDEFG", settings, typo_rate=0)
    seq = keys(rec)
    assert seq[-1] == ("release", "shift")
    assert len([k for k in seq if k[0] == "press" and k[1] != "shift"]) == 3
    calls["n"] = -1000
    kb.type_text("ok", settings.with_updates({"safety.failsafe": False}), typo_rate=0)


def test_hotkey_presses_modifiers_first_and_releases_in_reverse(
    keyboard: Keyboard, rec: Recorder, settings: Settings
) -> None:
    keyboard.hotkey("ctrl+shift+t", settings)
    assert keys(rec) == [
        ("press", "ctrl"),
        ("press", "shift"),
        ("press", "t"),
        ("release", "t"),
        ("release", "shift"),
        ("release", "ctrl"),
    ]
    first_gap = rec.events[1]
    assert first_gap[0] == "sleep" and 0.020 <= first_gap[1] <= 0.060


def test_hotkey_list_reorders_modifiers(keyboard: Keyboard, rec: Recorder, settings: Settings) -> None:
    keyboard.hotkey(["c", "Control"], settings)
    assert keys(rec) == [("press", "ctrl"), ("press", "c"), ("release", "c"), ("release", "ctrl")]


def test_hotkey_releases_pressed_keys_when_a_press_fails(rec: Recorder, settings: Settings) -> None:
    backend = FakeKeyboardBackend(rec)
    backend.fail_on_press = "t"
    kb = Keyboard(backend, rng=random.Random(1), sleep=rec.sleep)
    with pytest.raises(RuntimeError):
        kb.hotkey("ctrl+alt+t", settings)
    assert keys(rec) == [("press", "ctrl"), ("press", "alt"), ("release", "alt"), ("release", "ctrl")]


def test_press_key_repeats_with_hold_and_gap(keyboard: Keyboard, rec: Recorder, settings: Settings) -> None:
    keyboard.press_key("Page Down", settings, presses=3)
    assert keys(rec) == [("press", "page_down"), ("release", "page_down")] * 3
    holds = [rec.events[i + 1][1] for i, e in enumerate(rec.events) if e[0] == "press"]
    assert all(0.040 <= h <= 0.100 for h in holds)
    with pytest.raises(ValueError):
        keyboard.press_key("enter", settings, presses=0)


def test_press_key_shifted_character(keyboard: Keyboard, rec: Recorder, settings: Settings) -> None:
    keyboard.press_key("!", settings)
    assert keys(rec) == [("press", "shift"), ("press", "1"), ("release", "1"), ("release", "shift")]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Enter", "enter"),
        ("return", "enter"),
        ("ESC", "esc"),
        ("pgup", "page_up"),
        ("Page Down", "page_down"),
        ("win", "cmd"),
        ("Windows", "cmd"),
        ("ctrl", "ctrl"),
        ("Control", "ctrl"),
        ("F5", "f5"),
        ("f24", "f24"),
        ("del", "delete"),
        ("arrow-left", "left"),
        ("PrtSc", "print_screen"),
        ("a", "a"),
        ("A", "A"),
        (" ", " "),
    ],
)
def test_normalize_key(name: str, expected: str) -> None:
    assert normalize_key(name) == expected


def test_parse_hotkey_variants() -> None:
    assert parse_hotkey("Ctrl+Shift+T") == ["ctrl", "shift", "t"]
    assert parse_hotkey("ctrl++") == ["ctrl", "+"]
    assert parse_hotkey(["alt", "f4"]) == ["alt", "f4"]
    with pytest.raises(ValueError):
        parse_hotkey("ctrl+bogus")
    with pytest.raises(ValueError):
        parse_hotkey("ctrl+ctrl")
    with pytest.raises(ValueError):
        parse_hotkey("")
