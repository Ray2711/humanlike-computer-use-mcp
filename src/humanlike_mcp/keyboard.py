"""Human-like keyboard input on pynput: real key-down/up events, Shift handling, typos, hotkeys."""

from __future__ import annotations

import random
import sys
import time
from typing import Any, Callable, Iterable, Protocol

from . import humanize
from .config import Settings

SHIFT_RELEASE_LAG_MS = (10.0, 40.0)

US_SHIFTED = dict(zip('~!@#$%^&*()_+{}|:"<>?', "`1234567890-=[]\\;',./"))

KEY_ALIASES: dict[str, str] = {
    "enter": "enter", "return": "enter", "ret": "enter",
    "esc": "esc", "escape": "esc",
    "tab": "tab", "space": "space", "spacebar": "space",
    "backspace": "backspace", "bksp": "backspace", "back": "backspace",
    "delete": "delete", "del": "delete",
    "insert": "insert", "ins": "insert",
    "home": "home", "end": "end",
    "pageup": "page_up", "pgup": "page_up", "prior": "page_up",
    "pagedown": "page_down", "pgdn": "page_down", "next": "page_down",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "ctrl": "ctrl", "control": "ctrl", "lctrl": "ctrl_l", "ctrll": "ctrl_l", "rctrl": "ctrl_r", "ctrlr": "ctrl_r",
    "shift": "shift", "lshift": "shift_l", "shiftl": "shift_l", "rshift": "shift_r", "shiftr": "shift_r",
    "alt": "alt", "option": "alt", "opt": "alt", "lalt": "alt_l", "altl": "alt_l",
    "ralt": "alt_r", "altr": "alt_r", "altgr": "alt_gr",
    "win": "cmd", "windows": "cmd", "super": "cmd", "meta": "cmd", "cmd": "cmd", "command": "cmd", "start": "cmd",
    "lwin": "cmd_l", "rwin": "cmd_r",
    "capslock": "caps_lock", "caps": "caps_lock", "numlock": "num_lock", "scrolllock": "scroll_lock",
    "printscreen": "print_screen", "prtsc": "print_screen", "prtscr": "print_screen", "print": "print_screen",
    "pause": "pause", "break": "pause",
    "menu": "menu", "apps": "menu", "contextmenu": "menu",
    "volumeup": "media_volume_up", "volumedown": "media_volume_down", "volumemute": "media_volume_mute",
    "mute": "media_volume_mute", "playpause": "media_play_pause", "mediaplaypause": "media_play_pause",
    "nexttrack": "media_next", "prevtrack": "media_previous", "previoustrack": "media_previous",
    "stop": "media_stop",
}
KEY_ALIASES.update({f"f{i}": f"f{i}" for i in range(1, 25)})

MODIFIERS = frozenset(
    {"ctrl", "ctrl_l", "ctrl_r", "shift", "shift_l", "shift_r", "alt", "alt_l", "alt_r", "alt_gr", "cmd", "cmd_l", "cmd_r"}
)


def normalize_key(name: str) -> str:
    """Canonical pynput key name for a named key, or the single character itself."""
    if len(name) == 1:
        return name
    cleaned = name.strip()
    if len(cleaned) == 1:
        return cleaned
    compact = cleaned.lower().replace(" ", "").replace("-", "").replace("_", "")
    if compact in KEY_ALIASES:
        return KEY_ALIASES[compact]
    raise ValueError(f"Unknown key name {name!r}")


def parse_hotkey(keys: str | Iterable[str]) -> list[str]:
    """``"ctrl+shift+t"`` or ``["ctrl", "shift", "t"]`` -> canonical names, modifiers first."""
    if isinstance(keys, str):
        text = keys.strip()
        parts: list[str] = []
        if text.endswith("++"):
            parts = [p for p in text[:-2].split("+") if p.strip()] + ["+"]
        elif text == "+":
            parts = ["+"]
        else:
            parts = text.split("+")
        raw = [p if p == "+" else p.strip() for p in parts]
    else:
        raw = list(keys)
    if not raw or any(not k for k in raw):
        raise ValueError(f"Invalid hotkey {keys!r}")
    names = [normalize_key(k) for k in raw]
    names = [n.lower() if len(n) == 1 else n for n in names]
    if len(set(names)) != len(names):
        raise ValueError(f"Duplicate key in hotkey {keys!r}")
    return [n for n in names if n in MODIFIERS] + [n for n in names if n not in MODIFIERS]


class KeyboardBackend(Protocol):
    def press(self, key: Any) -> None: ...
    def release(self, key: Any) -> None: ...
    def char_key(self, char: str) -> tuple[Any, bool]: ...
    def named_key(self, name: str) -> Any: ...
    def hotkey_char(self, char: str) -> Any: ...
    @property
    def shift(self) -> Any: ...


class PynputBackend:
    """Sends real virtual-key events where the active layout has the character, Unicode otherwise."""

    def __init__(self) -> None:
        from pynput.keyboard import Controller, Key, KeyCode

        self._ctl = Controller()
        self._Key = Key
        self._KeyCode = KeyCode
        self._user32: Any = None
        if sys.platform == "win32":
            import ctypes

            user32 = ctypes.windll.user32
            user32.VkKeyScanW.restype = ctypes.c_short
            user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
            user32.GetKeyState.restype = ctypes.c_short
            self._user32 = user32

    @property
    def shift(self) -> Any:
        return self._Key.shift

    def press(self, key: Any) -> None:
        self._ctl.press(key)

    def release(self, key: Any) -> None:
        self._ctl.release(key)

    def named_key(self, name: str) -> Any:
        try:
            return getattr(self._Key, name)
        except AttributeError:
            raise ValueError(f"Key {name!r} is not supported on this platform") from None

    def _vk_scan(self, char: str) -> tuple[int, int] | None:
        if self._user32 is None or len(char) != 1 or ord(char) > 0xFFFF:
            return None
        res = self._user32.VkKeyScanW(char)
        if res == -1:
            return None
        return res & 0xFF, (res >> 8) & 0xFF

    def char_key(self, char: str) -> tuple[Any, bool]:
        special = {"\n": "enter", "\r": "enter", "\t": "tab", " ": "space"}
        if char in special:
            return self.named_key(special[char]), False
        if self._user32 is not None:
            scan = self._vk_scan(char)
            if scan is None or scan[1] not in (0, 1):
                return char, False
            vk, mods = scan
            shift = mods == 1
            if char.isalpha() and self._user32.GetKeyState(0x14) & 1:
                shift = not shift
            return self._KeyCode.from_vk(vk), shift
        if char in US_SHIFTED:
            return self._KeyCode.from_char(US_SHIFTED[char]), True
        if char.isascii() and char.isupper():
            return self._KeyCode.from_char(char.lower()), True
        return self._KeyCode.from_char(char), False

    def hotkey_char(self, char: str) -> Any:
        scan = self._vk_scan(char)
        if scan is not None:
            return self._KeyCode.from_vk(scan[0])
        return self._KeyCode.from_char(char)


class Keyboard:
    def __init__(
        self,
        backend: KeyboardBackend | None = None,
        *,
        rng: random.Random | None = None,
        sleep: Callable[[float], None] = time.sleep,
        failsafe_check: Callable[[], None] | None = None,
    ) -> None:
        self._backend = backend
        self.rng = rng or random.Random()
        self._sleep = sleep
        self.failsafe_check = failsafe_check

    @property
    def backend(self) -> KeyboardBackend:
        if self._backend is None:
            self._backend = PynputBackend()
        return self._backend

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._sleep(seconds)

    def _check(self, s: Settings) -> None:
        if s.safety.failsafe and self.failsafe_check is not None:
            self.failsafe_check()

    def type_text(
        self, text: str, s: Settings, *, wpm: float | None = None, typo_rate: float | None = None
    ) -> dict[str, Any]:
        if len(text) > s.safety.max_text_length:
            raise ValueError(
                f"Text is {len(text)} characters; the limit is {s.safety.max_text_length} (safety.max_text_length)"
            )
        if wpm is not None and not 5 <= wpm <= 300:
            raise ValueError("wpm must be between 5 and 300")
        if typo_rate is not None and not 0 <= typo_rate <= 0.5:
            raise ValueError("typo_rate must be between 0 and 0.5")
        human = s.humanize.enabled
        steps = humanize.plan_typing(self.rng, text, s.typing, wpm=wpm, typo_rate=typo_rate, enabled=human)
        backend = self.backend
        backspace = backend.named_key("backspace")
        shift_down = False
        started = time.perf_counter()
        try:
            for step in steps:
                self._check(s)
                if step.char == humanize.BACKSPACE:
                    key, needs_shift = backspace, False
                else:
                    key, needs_shift = backend.char_key(step.char)
                wait = step.wait if needs_shift else step.wait + step.shift_lead
                if shift_down and not needs_shift:
                    lag = min(wait, humanize.sample_ms(self.rng, SHIFT_RELEASE_LAG_MS)) if human else 0.0
                    self.sleep(lag)
                    backend.release(backend.shift)
                    shift_down = False
                    wait -= lag
                self.sleep(wait)
                if needs_shift and not shift_down:
                    backend.press(backend.shift)
                    shift_down = True
                    self.sleep(step.shift_lead)
                backend.press(key)
                self.sleep(step.hold)
                backend.release(key)
        finally:
            if shift_down:
                if human:
                    self.sleep(humanize.sample_ms(self.rng, SHIFT_RELEASE_LAG_MS))
                backend.release(backend.shift)
        typos = sum(1 for st in steps if st.typo and st.char != humanize.BACKSPACE)
        return {
            "characters": len(text),
            "keystrokes": len(steps),
            "typo_keystrokes": typos,
            "corrections": sum(1 for st in steps if st.correction),
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }

    def _resolve(self, name: str) -> tuple[Any, bool]:
        canonical = normalize_key(name)
        if len(canonical) == 1:
            return self.backend.char_key(canonical)
        return self.backend.named_key(canonical), False

    def press_key(self, key: str, s: Settings, *, presses: int = 1) -> dict[str, Any]:
        if not 1 <= presses <= 100:
            raise ValueError("presses must be between 1 and 100")
        resolved, needs_shift = self._resolve(key)
        human = s.humanize.enabled
        backend = self.backend
        for i in range(presses):
            self._check(s)
            if needs_shift:
                backend.press(backend.shift)
                if human:
                    self.sleep(humanize.sample_ms(self.rng, s.keys.modifier_gap_ms))
            backend.press(resolved)
            try:
                if human:
                    self.sleep(humanize.sample_ms(self.rng, s.keys.key_hold_ms))
            finally:
                backend.release(resolved)
                if needs_shift:
                    backend.release(backend.shift)
            if i < presses - 1 and human:
                self.sleep(humanize.sample_ms(self.rng, s.keys.repeat_gap_ms))
        return {"key": normalize_key(key), "presses": presses}

    def hotkey(self, keys: str | list[str], s: Settings) -> dict[str, Any]:
        names = parse_hotkey(keys)
        backend = self.backend
        resolved = [backend.named_key(n) if len(n) > 1 else backend.hotkey_char(n) for n in names]
        human = s.humanize.enabled
        self._check(s)
        pressed: list[Any] = []
        try:
            for i, key in enumerate(resolved):
                if i and human:
                    self.sleep(humanize.sample_ms(self.rng, s.keys.modifier_gap_ms))
                backend.press(key)
                pressed.append(key)
            if human:
                self.sleep(humanize.sample_ms(self.rng, s.keys.key_hold_ms))
        finally:
            for i, key in enumerate(reversed(pressed)):
                if i and human:
                    self.sleep(humanize.sample_ms(self.rng, s.keys.modifier_gap_ms) * 0.6)
                backend.release(key)
        return {"keys": names}
