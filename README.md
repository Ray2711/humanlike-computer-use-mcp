# humanlike-computer-use-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI agent use your computer the way a person does. It takes screenshots, moves the mouse along curved paths with natural speed, clicks with realistic hesitation and press durations, scrolls in finger flicks, and types with a real rhythm. That includes occasional typos, which it notices and fixes with Backspace.

It runs locally over **stdio** and is written in Python on top of the official `mcp` SDK.

- **Mouse paths:** [`windmouse`](https://pypi.org/project/windmouse/) (the WindMouse algorithm) generates the paths, and [`pyautogui`](https://pyautogui.readthedocs.io/) replays them.
- **Keyboard:** [`pynput`](https://pynput.readthedocs.io/) sends real key-down and key-up events, with real Shift presses and Unicode support.
- **Screenshots:** [`mss`](https://python-mss.readthedocs.io/) and [Pillow](https://python-pillow.org/).
- **Configuration:** every timing and randomness value is a setting, layered from built-in defaults, presets, a TOML file, environment variables, runtime changes, and per-call arguments.

> **Warning:** this server controls your **real** mouse and keyboard. Only connect it to agents you trust. To abort at any time, slam the mouse into a **corner of the primary screen** (the fail-safe).

## For AI agents

If you are an AI coding agent (Cursor, Claude Code, Codex, …):

- [`AGENTS.md`](AGENTS.md): repo layout, venv setup, safe test commands, and code conventions.
- [`docs/agent-setup.md`](docs/agent-setup.md): a step-by-step runbook for installing this server and registering it with Cursor, Claude Desktop, Claude Code, Codex or any stdio client, including verification and troubleshooting.
- [`docs/agent-usage.md`](docs/agent-usage.md): how to use the tools well. It covers screenshot coordinate scaling, presets, per-call overrides, when to set `typo_rate` to 0, hotkeys, scrolling, the fail-safe, and example sequences.
- [`.cursor/skills/continuous-computer-use/`](.cursor/skills/continuous-computer-use/SKILL.md): a Cursor skill for long-running ("24/7") computer use.
  - It runs a recursive observe → decide → act → verify loop over a task queue (`tasks.md`) and persists its state (`state.md`), so it resumes across ticks and sessions.
  - Its guardrails include consent, the fail-safe, a `STOP` file, budgets, an app allowlist, confirmation before irreversible actions, and stuck detection.
  - Run it with `/continuous-computer-use`, or schedule it with `/loop 5m /continuous-computer-use`.
  - `tasks.md` and `state.md` are git-ignored working copies of the tracked `tasks.template.md` and `state.template.md`. The first run creates them if they're missing; you can also create them yourself, then edit `tasks.md` (not the template):
    - PowerShell: `Copy-Item .cursor/skills/continuous-computer-use/tasks.template.md .cursor/skills/continuous-computer-use/tasks.md` (likewise for `state`)
    - POSIX: `cp .cursor/skills/continuous-computer-use/tasks.template.md .cursor/skills/continuous-computer-use/tasks.md` (likewise for `state`)

---

## Contents

1. [Features](#features)
2. [How the humanization works](#how-the-humanization-works)
3. [Installation](#installation)
4. [MCP client configuration](#mcp-client-configuration)
5. [Tool reference](#tool-reference)
6. [Configuration reference](#configuration-reference)
7. [Safety and the fail-safe](#safety-and-the-fail-safe)
8. [DPI scaling and multiple monitors](#dpi-scaling-and-multiple-monitors)
9. [Running the tests](#running-the-tests)
10. [Measured realism](#measured-realism)
11. [Troubleshooting](#troubleshooting)
12. [Limitations](#limitations)
13. [Project layout](#project-layout)
14. [Credits](#credits)

---

## Features

- **18 tools.**
  - Screen: `screenshot`, `get_screen_size`, `get_cursor_position`.
  - Mouse: `mouse_move`, `click` (single, double, or triple), `mouse_down`, `mouse_up`, `drag`, `scroll` (vertical or horizontal).
  - Keyboard: `type_text`, `press_key`, `hotkey`.
  - Utility: `wait`.
  - Configuration: `get_config`, `set_config`, `reset_config`, `list_presets`, `use_preset`.
- **Human-like mouse.**
  - Curved WindMouse paths with a gentle arc.
  - A bell-shaped speed profile.
  - Duration grows with distance roughly as Fitts' law predicts.
  - Random overshoot-and-correct on long moves.
  - Landing jitter of a few pixels.
- **Human-like clicks and drags.** A Gaussian hesitation before pressing, Gaussian press durations, a realistic double-click gap, and a hold-then-carry-then-settle pattern for drags.
- **Human-like scrolling.** Real wheel notches, grouped into 3–6-notch flicks with ramped gaps and "regrip" pauses between flicks.
- **Human-like typing.**
  - Log-normal key intervals, calibrated so the real words-per-minute rate matches the setting (pauses and typos included).
  - Faster common letter pairs and alternating-hand pairs.
  - Real Shift presses, held across runs of capitals.
  - Pauses after words, punctuation, and newlines, plus occasional "thinking" pauses.
  - QWERTY neighbour-key typos, sometimes noticed a key or two late, then fixed with Backspace.
- **Natural hotkeys.** Modifiers go down first with staggered gaps, and keys are released in reverse order. Keys are always released, even when something fails.
- **Four presets:** `natural` (default), `careful`, `fast`, and `instant` (humanization off).
- **Safety.**
  - Corner fail-safe, which also aborts long typing jobs between keystrokes.
  - Coordinates clamped to the desktop.
  - A maximum text length.
  - Mouse buttons are force-released if a drag or click is interrupted.
- **DPI-aware on Windows**, so screenshots, the mouse, and the keyboard all use the same physical-pixel coordinates.

---

## How the humanization works

The timing and randomness models live in `src/humanlike_mcp/humanize.py`. They are pure functions with no input side effects, so they are unit-tested in isolation. `mouse.py` and `keyboard.py` execute them through a small backend interface (pyautogui and pynput in production, recording fakes in tests).

A range written like `[60, 140]` is sampled from a **Gaussian centred in the range**, with a standard deviation of a quarter of its width, truncated to the range. Values cluster in the middle, and the extremes are rare.

### Mouse movement

1. **Target.** The requested point is clamped to the virtual desktop and nudged off the exact fail-safe corner pixels. A **landing offset** within `mouse.landing_jitter_px` (default 2 px) is added, so repeated moves don't land on the identical pixel.
2. **Path geometry: WindMouse.** The path comes from `windmouse.core.wind_mouse`, a physics-style model:
   - **Gravity** pulls the cursor toward the target.
   - Random **wind** pushes it sideways.
   - The velocity is clipped to a random value between ½·`max_step` and `max_step`.
   - Within `damped_distance` of the target, the wind dies down and the steps shrink, so the cursor eases in.
3. **Fitts-like scaling.**
   - `max_step` is scaled by `(distance / 500 px)^0.7`, clamped to 0.45×–3×. Short moves use small, careful steps and long moves use big ones, so total duration grows roughly logarithmically with distance instead of linearly.
   - `damped_distance` is scaled by the same factor. Without this, large steps tend to orbit the target.
4. **Arc.** People move the mouse from the wrist and elbow, so paths bow slightly to one side. The path is bent sideways by `A·sin(π·t)`, where `t` is progress along the straight line. `A` is drawn from `N(0, arc_strength · distance)` and capped at 3 standard deviations. Both endpoints stay exactly where they were.
5. **Speed profile.**
   - Each path point gets a delay of `tick_delay × multiplier`. The multipliers follow a bell-shaped velocity curve that starts slow, peaks at about 42% of the path, and decelerates into the target. They average 1, so the total time stays the same.
   - Each delay also gets ±15% noise.
   - The `speed` setting divides `tick_delay` by √speed and multiplies `max_step` by √speed, so the overall pace scales by `speed`.
6. **Overshoot and correction.**
   - Only moves of at least `overshoot_min_distance` (300 px) can overshoot. With probability `overshoot_probability` (0.3), the cursor first heads for a point up to `overshoot_max_px` (25 px, at most 15% of the distance) past the target, with some sideways error.
   - After a 60–160 ms "noticing" pause, it makes a short, calm correction back to the target. The correction uses a quarter of the wind, 40% of the step size, and slightly slower ticks.

The plan was to use windmouse's `PyautoguiMouseController.move_to_target()`. Inspecting the installed source showed four problems with it:

- It calls `pyautogui.moveTo(duration=0.1)` for every step, which tweens each step slowly.
- It cannot be aborted mid-move.
- It presses and releases the drag button without a `try/finally`, so an exception leaves the button stuck down.
- It treats coordinate `0` as "not set".

So this server calls the library's `wind_mouse()` **path generator** directly and replays the points itself with `pyautogui.moveTo(x, y, _pause=False)`. That replay runs pyautogui's fail-safe check at every point.

### Clicks

- When coordinates are given, the server first does a human move to them.
- It then waits a `click.pre_click_pause_ms` hesitation (50–250 ms), the time a person takes to confirm they're on target.
- Each press is held for `click.hold_ms` (60–140 ms).
- Double and triple clicks separate presses by `click.double_click_gap_ms` (80–180 ms), comfortably inside Windows' default 500 ms double-click time.
- If anything fails while the button is down, the button is force-released.

### Drag

1. Human move to the start point.
2. Pre-click hesitation.
3. Press the button.
4. Hold still for `drag.pre_move_hold_ms` (80–200 ms). Many UIs need this to register a drag.
5. Carry the cursor along a WindMouse path at `mouse.speed × drag.speed_factor` (0.7, since people drag more carefully). This leg has no overshoot and no landing jitter, so it ends exactly on the target.
6. Settle for `drag.release_dwell_ms` (60–180 ms).
7. Release. The release is in a `finally` block, so the button is freed even if the fail-safe trips mid-drag.

### Scroll

- `amount` is in **wheel notches** (one notch = 120 wheel units on Windows). Positive means up, or right when `horizontal=true`.
- Notches are sent one at a time, grouped into **flicks** of `scroll.flick_notches` (3–6).
- Within a flick, the gaps (`scroll.tick_gap_ms`, 20–90 ms) follow a U-shape when `scroll.ramp` is on: long, then short, then long again.
- Between flicks there is a finger "regrip" pause of `scroll.regrip_pause_ms` (150–400 ms).
- When `x`/`y` are given, the server moves there first and waits a short settle pause before scrolling.
- Scroll events go through `pynput`, not pyautogui. On Windows, `pyautogui.scroll(1)` sends a raw wheel delta of 1, which is 1/120 of a notch, and `pyautogui.hscroll` actually scrolls vertically.

### Typing

`type_text` first **plans** every keystroke, including typos and corrections, and then executes the plan.

- **WPM calibration.**
  - The nominal time per character is `12 / wpm` seconds (one word = 5 characters).
  - The planner computes the expected extra time for this specific text: word, punctuation, and newline pauses, thinking pauses, Shift lead time, and the expected cost of typos.
  - It then shrinks the base interval so the **total** time matches the configured WPM. A 70 WPM setting really produces about 70 WPM end to end.
- **Per-key noise.** Each press-to-press interval is multiplied by log-normal noise with mean 1 and sigma `wpm_variance` (0.25). The result is right-skewed like real inter-key intervals: mostly quick, with occasional slow keys.
- **Letter pairs.**
  - About 50 common English bigrams (`th`, `he`, `in`, `er`, …) take `bigram_speedup` (0.8) of the interval.
  - Letter pairs typed by alternating hands take halfway between, 0.9×.
- **Context pauses.**
  - Starting a new word adds `space_pause_ms` (80–250 ms).
  - The key after `. , ; : ! ?` adds `punctuation_pause_ms` (150–450 ms).
  - The key after a newline adds `newline_pause_ms` (300–800 ms).
  - At word boundaries, there is a `think_pause_probability` (3%) chance of an extra `think_pause_ms` (0.4–1.5 s).
- **Shift.**
  - Capitals and shifted symbols really press Shift. It goes down `shift_extra_ms` (40–90 ms) before the key.
  - Shift stays down across runs of capitals (`HELLO` is one Shift press) and is released 10–40 ms after the last shifted key.
- **Key holds.** Each key is held for `key_hold_ms` (30–90 ms).
- **Layout-aware keys.** On Windows, each character is resolved with `VkKeyScanW` for the *active* keyboard layout, so a real virtual-key event is sent, with a real Shift if needed. Caps Lock is taken into account. Characters that aren't on the layout (`é` on a US layout, `✓`, `日本`, emoji) are injected as Unicode key events.
- **Newlines and tabs** are typed as the Enter and Tab keys.

#### Typos

- For each letter or digit, there is a `typo_rate` (2%) chance of a mistake.
- Most mistakes hit a **neighbouring QWERTY key** (for example `s` → `a d w e z x`), keeping the case.
- About 20% are **transpositions** (`teh`) when the next character is also a letter.
- About 35% of the time the typist doesn't notice right away and types 1–2 more correct characters first (never past a word boundary).
- After a `typo_notice_ms` (200–600 ms) pause, the typist presses Backspace once per wrong or extra character, then retypes. Backspaces after the first are quicker.
- The final text is always exactly what was requested. The tests check this over many seeds at a 25–30% typo rate.

### Hotkeys and key presses

- `hotkey("ctrl+shift+t")` normalizes key names and orders modifiers first. Modifiers go down with `keys.modifier_gap_ms` (20–60 ms) gaps, then the main key goes down.
- Keys are held for `keys.key_hold_ms` (40–100 ms) and released in **reverse order**, with slightly shorter gaps.
- Every key that was pressed is released in a `finally` block.
- In a hotkey, letter keys are sent as virtual keys, so `ctrl+c` is a real Ctrl+C on any layout.
- `press_key` holds for `keys.key_hold_ms` and spaces out repeats with `keys.repeat_gap_ms` (60–160 ms).
- A single shifted character, such as `press_key("!")`, is wrapped in a real Shift press.

---

## Installation

Requirements: Windows 10/11 (the main target; see [Limitations](#limitations) for macOS and Linux), and Python 3.10 or newer. It was developed and tested on Python 3.14.

> `C:\Users\alice\humanlike-computer-use-mcp` in the examples below is a placeholder; substitute the absolute path of your own clone.

```powershell
cd C:\Users\alice\humanlike-computer-use-mcp
python -m venv venv
venv\Scripts\python.exe -m pip install -e ".[dev]"
```

The `[dev]` extra only adds `pytest`; use `pip install -e .` for runtime dependencies only. If pip fails with `Permission denied` on its cache directory, add `--no-cache-dir`.

Check the install:

```powershell
venv\Scripts\python.exe -c "import humanlike_mcp.server as s; print('ok')"
```

You can start the server by hand, but it only speaks MCP over stdin/stdout, so normally your MCP client launches it:

```powershell
venv\Scripts\python.exe -m humanlike_mcp.server
# or the console script:
venv\Scripts\humanlike-computer-use-mcp.exe
```

---

## MCP client configuration

Always use the **absolute path to the venv's Python** (or the console script), so the client uses the right interpreter and packages. Replace the placeholder `C:\\Users\\alice\\humanlike-computer-use-mcp` with your own absolute path (JSON needs doubled backslashes).

### Cursor

Add the server to a project's `.cursor/mcp.json`, or to the global `%USERPROFILE%\.cursor\mcp.json`:

```json
{
  "mcpServers": {
    "humanlike-computer-use": {
      "command": "C:\\Users\\alice\\humanlike-computer-use-mcp\\venv\\Scripts\\python.exe",
      "args": ["-m", "humanlike_mcp.server"],
      "env": {
        "HUMANLIKE_PRESET": "natural"
      }
    }
  }
}
```

Alternatively, use the console script:

```json
{
  "mcpServers": {
    "humanlike-computer-use": {
      "command": "C:\\Users\\alice\\humanlike-computer-use-mcp\\venv\\Scripts\\humanlike-computer-use-mcp.exe",
      "args": []
    }
  }
}
```

### Claude Desktop

Edit `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "humanlike-computer-use": {
      "command": "C:\\Users\\alice\\humanlike-computer-use-mcp\\venv\\Scripts\\python.exe",
      "args": ["-m", "humanlike_mcp.server"],
      "env": {
        "HUMANLIKE_CONFIG": "C:\\Users\\alice\\humanlike-computer-use-mcp\\humanlike.toml"
      }
    }
  }
}
```

### Using a config file from the client

MCP clients start the server from a working directory you don't control, so `./humanlike.toml` may not be found. Point to the file explicitly with `HUMANLIKE_CONFIG` (as in the Claude example above). Any single setting can also be passed as an environment variable in the `env` block, for example `"HUMANLIKE_TYPING__WPM": "55"`.

Restart or reload the MCP server in your client after editing its config.

---

## Tool reference

All coordinates are **physical screen pixels**. (0, 0) is the top-left corner of the primary monitor.

**Shared optional arguments on action tools:**

| Argument | Meaning |
| --- | --- |
| `humanize` (bool) | `false` skips all human-like timing for this call (instant move, no pauses, no typos). |
| `speed` (number > 0, ≤ 20) | Mouse speed multiplier for this call (`mouse_move`, `click`, `drag`). |
| `overrides` (object) | Any setting for this call only, by dotted key, e.g. `{"mouse.wind": 6, "click.hold_ms": [90, 130]}`. |

Per-call values never persist. Invalid values return a clear error and nothing happens.

### Screen

| Tool | Parameters | Returns |
| --- | --- | --- |
| `screenshot` | `monitor=0` (0 = all monitors / virtual desktop, 1 = primary, 2… = others), `max_width=None` (downscale to at most this width, ≥ 64), `format="png"` (`png` or `jpeg`), `quality=80` (JPEG only, 1–95) | An image, plus JSON metadata with `image_width`, `image_height`, `region` (left/top/width/height of the captured area in screen pixels), `scale`, and `cursor`. Convert image pixels to screen pixels with `screen_x = region.left + image_x * scale`, and the same for y. |
| `get_screen_size` | none | `width`/`height` of the primary monitor, `virtual_desktop` bounds, and a `monitors` list with each monitor's bounds |
| `get_cursor_position` | none | `{"x", "y"}` |

### Mouse

| Tool | Parameters | Notes |
| --- | --- | --- |
| `mouse_move` | `x`, `y`, `speed?`, `humanize?`, `overrides?` | Returns `from`, `to` (actual landing point, including jitter), `overshoot`, `points`, `duration_ms`. |
| `click` | `x?`, `y?`, `button="left"` (`left`/`right`/`middle`), `clicks=1` (1–3), `speed?`, `humanize?`, `overrides?` | Without coordinates it clicks in place. With only one of x/y, the other axis stays where it is. |
| `mouse_down` | `button="left"`, `x?`, `y?`, `humanize?`, `overrides?` | Press and hold. Pair with `mouse_up`. |
| `mouse_up` | `button="left"` | Always releases, even when the fail-safe is active. |
| `drag` | `start_x`, `start_y`, `end_x`, `end_y`, `button="left"`, `speed?`, `humanize?`, `overrides?` | Move to start, press, hold, carry, settle, release. |
| `scroll` | `amount` (notches, −500…500; vertical: + = up, horizontal: + = right), `x?`, `y?`, `horizontal=false`, `humanize?`, `overrides?` | Optionally moves to (x, y) first. |

### Keyboard

| Tool | Parameters | Notes |
| --- | --- | --- |
| `type_text` | `text`, `wpm?` (5–300), `typo_rate?` (0–0.5), `humanize?`, `overrides?` | Types into the focused window. Supports Unicode; `\n` presses Enter and `\t` presses Tab. Returns `characters`, `keystrokes`, `typo_keystrokes`, `corrections`, `duration_ms`. |
| `press_key` | `key`, `presses=1` (1–100), `humanize?`, `overrides?` | `key` is a key name (see below) or a single character. |
| `hotkey` | `keys` (a string like `"ctrl+shift+t"`, or a list like `["ctrl", "c"]`), `humanize?`, `overrides?` | Use `"ctrl++"` for Ctrl and Plus. Duplicate keys are rejected. |
| `wait` | `seconds` (0–300) | Varies by ±5% when humanization is on. Watches the corner fail-safe while waiting and aborts early with the fail-safe error. |

**Key names** are case-insensitive, and spaces, `-` and `_` are ignored (`Page Down` = `page_down` = `pgdn`):

| Category | Names |
| --- | --- |
| Editing and navigation | `enter`/`return`, `esc`/`escape`, `tab`, `space`, `backspace`, `delete`/`del`, `insert`/`ins`, `home`, `end`, `pageup`/`pgup`, `pagedown`/`pgdn`, `up`, `down`, `left`, `right` (also `arrowup` and the other `arrow…` forms) |
| Modifiers | `ctrl`/`control` (`lctrl`, `rctrl`), `shift` (`lshift`, `rshift`), `alt`/`option` (`lalt`, `ralt`, `altgr`), `win`/`windows`/`cmd`/`super`/`meta` (`lwin`, `rwin`) |
| Function keys | `f1` to `f24` |
| Lock and system keys | `capslock`, `numlock`, `scrolllock`, `printscreen`/`prtsc`, `pause`, `menu`/`apps` |
| Media keys | `volumeup`, `volumedown`, `mute`, `playpause`, `nexttrack`, `prevtrack`, `stop` |

### Configuration tools

| Tool | Parameters | Notes |
| --- | --- | --- |
| `get_config` | none | Every setting with its current value and **source**: `default`, `preset:<name>`, `file:<path>`, `env`, or `runtime`. Also returns the active preset and config file. |
| `set_config` | `updates` (object of dotted keys to values) | Validated and all-or-nothing: if one value is invalid, nothing changes. Lasts for this server session only. Returns the normalized new values. |
| `reset_config` | none | Undoes all runtime changes (`set_config` **and** `use_preset`). |
| `list_presets` | none | Each preset's description and the settings it changes. |
| `use_preset` | `name` | Switches the preset layer for this session and clears runtime `set_config` changes. File and environment settings still apply on top. |

---

## Configuration reference

### Layering

Each layer overrides the one before it:

1. **Built-in defaults:** the `natural` preset.
2. **Preset:** chosen with `HUMANLIKE_PRESET`, otherwise `preset = "…"` in the TOML file, otherwise `natural`. (At runtime, `use_preset` takes priority over both.)
3. **TOML file:** the path in `HUMANLIKE_CONFIG`, otherwise `./humanlike.toml` in the server's working directory if it exists. A missing *explicit* path is an error.
4. **Environment variables:** `HUMANLIKE_<SECTION>__<KEY>` (note the **double** underscore), e.g. `HUMANLIKE_TYPING__WPM=55`.
5. **Runtime:** `set_config` changes for the current session.
6. **Per call:** tool arguments such as `wpm`, `typo_rate`, `speed`, `humanize`, and `overrides`.

Every setting is validated: its type, its allowed range, and `min ≤ max` for ranges. Bad values are **rejected with a clear message, never silently clamped**. A bad config file or environment variable stops the server at startup, with the error printed to stderr. An unknown key is an error too, so typos in setting names don't go unnoticed.

**Value formats:**

- **Booleans:** `true/false`, `1/0`, `yes/no`, `on/off`.
- **Ranges (`[min, max]`, in milliseconds unless noted):**
  - TOML: `[60, 140]`.
  - Environment variable: `"60,140"` or `"[60,140]"`.
  - JSON (`set_config`): `[60, 140]`.
  - A single number `n` means the fixed value `[n, n]`.

### All settings

| Key | Default (`natural`) | Allowed | Environment variable |
| --- | --- | --- | --- |
| `humanize.enabled` | `true` | bool | `HUMANLIKE_HUMANIZE__ENABLED` |
| `mouse.gravity` | `9.0` | 0.1–100 | `HUMANLIKE_MOUSE__GRAVITY` |
| `mouse.wind` | `3.0` | 0–100 | `HUMANLIKE_MOUSE__WIND` |
| `mouse.max_step` | `15.0` | 1–500 | `HUMANLIKE_MOUSE__MAX_STEP` |
| `mouse.damped_distance` | `12.0` | 1–1000 | `HUMANLIKE_MOUSE__DAMPED_DISTANCE` |
| `mouse.tick_delay` | `0.012` (s) | 0–0.2 | `HUMANLIKE_MOUSE__TICK_DELAY` |
| `mouse.speed` | `1.0` | 0.05–20 | `HUMANLIKE_MOUSE__SPEED` |
| `mouse.ease` | `true` | bool | `HUMANLIKE_MOUSE__EASE` |
| `mouse.arc_strength` | `0.04` | 0–0.5 | `HUMANLIKE_MOUSE__ARC_STRENGTH` |
| `mouse.overshoot_enabled` | `true` | bool | `HUMANLIKE_MOUSE__OVERSHOOT_ENABLED` |
| `mouse.overshoot_min_distance` | `300.0` (px) | 0–100000 | `HUMANLIKE_MOUSE__OVERSHOOT_MIN_DISTANCE` |
| `mouse.overshoot_probability` | `0.3` | 0–1 | `HUMANLIKE_MOUSE__OVERSHOOT_PROBABILITY` |
| `mouse.overshoot_max_px` | `25.0` | 0–1000 | `HUMANLIKE_MOUSE__OVERSHOOT_MAX_PX` |
| `mouse.landing_jitter_px` | `2.0` | 0–100 | `HUMANLIKE_MOUSE__LANDING_JITTER_PX` |
| `click.hold_ms` | `[60, 140]` | 0–10000 | `HUMANLIKE_CLICK__HOLD_MS` |
| `click.pre_click_pause_ms` | `[50, 250]` | 0–10000 | `HUMANLIKE_CLICK__PRE_CLICK_PAUSE_MS` |
| `click.double_click_gap_ms` | `[80, 180]` | 0–2000 | `HUMANLIKE_CLICK__DOUBLE_CLICK_GAP_MS` |
| `drag.pre_move_hold_ms` | `[80, 200]` | 0–10000 | `HUMANLIKE_DRAG__PRE_MOVE_HOLD_MS` |
| `drag.release_dwell_ms` | `[60, 180]` | 0–10000 | `HUMANLIKE_DRAG__RELEASE_DWELL_MS` |
| `drag.speed_factor` | `0.7` | 0.05–10 | `HUMANLIKE_DRAG__SPEED_FACTOR` |
| `scroll.tick_gap_ms` | `[20, 90]` | 0–5000 | `HUMANLIKE_SCROLL__TICK_GAP_MS` |
| `scroll.ramp` | `true` | bool | `HUMANLIKE_SCROLL__RAMP` |
| `scroll.flick_notches` | `[3, 6]` (notches) | 1–100 | `HUMANLIKE_SCROLL__FLICK_NOTCHES` |
| `scroll.regrip_pause_ms` | `[150, 400]` | 0–10000 | `HUMANLIKE_SCROLL__REGRIP_PAUSE_MS` |
| `typing.wpm` | `70.0` | 5–300 | `HUMANLIKE_TYPING__WPM` |
| `typing.wpm_variance` | `0.25` | 0–2 | `HUMANLIKE_TYPING__WPM_VARIANCE` |
| `typing.key_hold_ms` | `[30, 90]` | 0–2000 | `HUMANLIKE_TYPING__KEY_HOLD_MS` |
| `typing.bigram_speedup` | `0.8` | 0.1–1 | `HUMANLIKE_TYPING__BIGRAM_SPEEDUP` |
| `typing.shift_extra_ms` | `[40, 90]` | 0–2000 | `HUMANLIKE_TYPING__SHIFT_EXTRA_MS` |
| `typing.space_pause_ms` | `[80, 250]` | 0–5000 | `HUMANLIKE_TYPING__SPACE_PAUSE_MS` |
| `typing.punctuation_pause_ms` | `[150, 450]` | 0–5000 | `HUMANLIKE_TYPING__PUNCTUATION_PAUSE_MS` |
| `typing.newline_pause_ms` | `[300, 800]` | 0–10000 | `HUMANLIKE_TYPING__NEWLINE_PAUSE_MS` |
| `typing.think_pause_probability` | `0.03` | 0–1 | `HUMANLIKE_TYPING__THINK_PAUSE_PROBABILITY` |
| `typing.think_pause_ms` | `[400, 1500]` | 0–30000 | `HUMANLIKE_TYPING__THINK_PAUSE_MS` |
| `typing.typo_rate` | `0.02` | 0–0.5 | `HUMANLIKE_TYPING__TYPO_RATE` |
| `typing.typo_notice_ms` | `[200, 600]` | 0–10000 | `HUMANLIKE_TYPING__TYPO_NOTICE_MS` |
| `typing.layout` | `"qwerty"` | `qwerty` | `HUMANLIKE_TYPING__LAYOUT` |
| `keys.modifier_gap_ms` | `[20, 60]` | 0–2000 | `HUMANLIKE_KEYS__MODIFIER_GAP_MS` |
| `keys.key_hold_ms` | `[40, 100]` | 0–5000 | `HUMANLIKE_KEYS__KEY_HOLD_MS` |
| `keys.repeat_gap_ms` | `[60, 160]` | 0–5000 | `HUMANLIKE_KEYS__REPEAT_GAP_MS` |
| `safety.failsafe` | `true` | bool | `HUMANLIKE_SAFETY__FAILSAFE` |
| `safety.max_text_length` | `10000` (chars) | 1–1000000 | `HUMANLIKE_SAFETY__MAX_TEXT_LENGTH` |

`HUMANLIKE_PRESET` selects the preset and `HUMANLIKE_CONFIG` points to the TOML file. What each setting means is described in [How the humanization works](#how-the-humanization-works).

### TOML example

[`humanlike.example.toml`](humanlike.example.toml) lists every setting with its default and a comment. Copy it to `humanlike.toml`, or point `HUMANLIKE_CONFIG` at it, and keep only the lines you change:

```toml
preset = "natural"

[typing]
wpm = 55
typo_rate = 0.01

[mouse]
speed = 0.9
overshoot_probability = 0.2

[click]
pre_click_pause_ms = [100, 300]
```

### Presets

| Preset | Character | Changes from `natural` |
| --- | --- | --- |
| `natural` | Default; ordinary human behaviour | none |
| `careful` | Slower and more precise | ~45 WPM, typo rate 0.8%, more thinking pauses and longer punctuation pauses; mouse speed 0.75, wind 2, overshoot 15%, jitter 1 px; longer pre-click pauses and holds; slower drags and scrolling |
| `fast` | Skilled power user | ~100 WPM, typo rate 1.5%, shorter key holds and pauses, rare thinking pauses; mouse speed 1.6, overshoot 35%; quicker clicks, drags, scrolls, and hotkeys |
| `instant` | Humanization off (debugging or speed) | `humanize.enabled=false`, `typing.typo_rate=0`: instant jumps, no delays, no typos |

`list_presets` shows the exact values.

### Examples

Set a slower typist for the whole session:

```json
{"tool": "set_config", "arguments": {"updates": {"typing.wpm": 45, "typing.typo_rate": 0.01}}}
```

Do one precise click without overshoot or jitter:

```json
{"tool": "click", "arguments": {"x": 812, "y": 455, "overrides": {"mouse.overshoot_enabled": false, "mouse.landing_jitter_px": 0}}}
```

Paste-speed typing for one call:

```json
{"tool": "type_text", "arguments": {"text": "long log output...", "humanize": false}}
```

---

## Safety and the fail-safe

- **Corner fail-safe.** If the cursor sits in any corner pixel of the **primary** monitor, every mouse action and every keystroke aborts with:
  > `Fail-safe triggered: the mouse cursor is in a corner of the primary screen, which aborts all automated input. …`

  The check runs at every point of a mouse path (through pyautogui) and **between every keystroke** of `type_text`, so long typing jobs can be stopped too. Move the cursor away from the corner to continue.
- **Disabling it** is not recommended. You can set `safety.failsafe=false`, `HUMANLIKE_SAFETY__FAILSAFE=0`, or `set_config({"safety.failsafe": false})`.
- **No stuck keys or buttons.** Clicks and drags force-release the mouse button if interrupted. `type_text` always releases Shift, and `hotkey` always releases every key it pressed. `mouse_up` works even while the fail-safe is active.
- **Coordinate clamping.** Targets are clamped to the virtual desktop. While the fail-safe is on, targets are also nudged 1 px off the exact corner pixels, so the server never trips its own fail-safe.
- **Limits.**
  - `type_text` refuses text longer than `safety.max_text_length` (10,000).
  - `scroll` accepts up to ±500 notches.
  - `press_key` accepts up to 100 presses.
  - `wait` accepts up to 300 s.
- **Serialized input.** Action tools hold a lock, so two simultaneous tool calls never interleave their input events.

---

## DPI scaling and multiple monitors

- **DPI.** At startup, and before `pyautogui` is imported, the server calls `SetProcessDpiAwareness(2)` (per-monitor DPI aware). Screenshots, cursor positions, and mouse moves then all use **physical pixels**. On a 4K screen at 150% scaling, `get_screen_size` reports 3840×2160, and screenshot pixel (x, y) is screen pixel (x, y) when not downscaled.
- **Downscaled screenshots.** With `max_width`, multiply image coordinates by the returned `scale` and add `region.left` / `region.top`.
- **Multiple monitors.**
  - `screenshot(monitor=0)` captures the whole virtual desktop, and `region` tells you where it starts. On some layouts that is at negative coordinates.
  - `monitor=1` is the primary monitor and `2…` are the others. `get_screen_size` lists every monitor's bounds.
  - Mouse targets can be anywhere on the virtual desktop, including negative coordinates for monitors left of or above the primary.
  - The fail-safe corners are those of the **primary** monitor only.
- **Mixed DPI across monitors** works, because the process is per-monitor aware. Windows scales nothing behind the server's back.

---

## Running the tests

```powershell
venv\Scripts\python.exe -m pytest -q
```

The suite runs in about 7 seconds and **never sends real input**. The mouse and keyboard are replaced by recording fakes with a fake `sleep`, so the tests can check exact event order and timing.

| File | What it checks |
| --- | --- |
| `test_humanize.py` | Timing distributions, determinism with a seeded RNG, WindMouse path validity and curvature, the no-orbit guarantee, arc, speed profile, overshoot geometry, scroll ramp and regrips, the QWERTY neighbour map, that typos are always corrected (25 seeds), typo frequency, delay bounds, context pauses, effective WPM within 15% at 45, 70, and 100 WPM, and simulated move durations of 0.3–0.9 s |
| `test_config.py` | Defaults, the example TOML matching the defaults, presets, every layer and its reported source, environment parsing, validation errors, atomic `set_config`, `use_preset`/`reset_config` |
| `test_mouse.py` | Move, clamp, and corner nudging; overshoot and correction; speed scaling; click order and hold times; double-click gap; drag sequence; button release when the fail-safe trips mid-drag; scroll notches |
| `test_keyboard.py` | Typed output equal to the input despite typos; balanced press/release; Shift held across capitals; typo-then-Backspace sequences; Unicode; fail-safe abort that releases Shift; hotkey order and reverse release (also when a press fails); key-name normalization |
| `test_server.py` | All 18 tools listed; config tools; actions routed to backends; fail-safe and validation surfaced as tool errors; real screenshots in-process; and an **end-to-end stdio session** through the official MCP client (initialize, list tools, `get_config` showing an env-layer value, `get_screen_size`, `screenshot`, an invalid `set_config`) |
| `test_live.py` | **Opt-in** real-desktop smoke test. It moves the cursor about 60 px and back, takes a screenshot, and reads the cursor position. It never clicks or types. |

Run the live test:

```powershell
$env:HUMANLIKE_LIVE_TESTS = "1"; venv\Scripts\python.exe -m pytest tests\test_live.py -q -s; Remove-Item Env:HUMANLIKE_LIVE_TESTS
```

To print a realism report (simulation only; nothing moves), run the following. The `--overhead-ms` value is the real per-point cost of `moveTo` plus sleep overshoot on your machine, which was about 0.6 ms here:

```powershell
venv\Scripts\python.exe scripts\realism_report.py --overhead-ms 0.6
```

---

## Measured realism

These figures come from `scripts/realism_report.py` (200 runs per distance) and were confirmed on the real desktop: a live 67 px move took 236 ms.

Median mouse-move durations, `natural` preset (10th–90th percentile in brackets; the upper tail includes the ~30% of long moves that overshoot):

| Distance | 100 px | 250 px | 500 px | 800 px | 1200 px | 1800 px |
| --- | --- | --- | --- | --- | --- | --- |
| Median (ms) | 289 [270–315] | 495 [465–522] | 617 [582–864] | 698 [653–927] | 773 [736–1018] | 896 [838–1123] |

`careful` is about 25% slower and `fast` about 30% faster. Speed along the path is bell-shaped: about 760 px/s in the first 10%, peaking at about 1,730 px/s around 40–50%, and about 510 px/s in the last 10%.

Typing, with effective WPM including all pauses and typos (mean of 50 plans):

| Preset | Target WPM | Effective WPM |
| --- | --- | --- |
| `natural` | 70 | 71.5 (+2.1%) |
| `careful` | 45 | 44.8 (−0.3%) |
| `fast` | 100 | 100.7 (+0.7%) |

At 70 WPM, the median keystroke interval is about 117 ms (10th percentile 72 ms, 90th percentile 302 ms), which is a realistic right-skewed distribution.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Tool error "Fail-safe triggered" | The cursor is in a primary-screen corner. Move it away. To disable the fail-safe (not recommended), see [Safety](#safety-and-the-fail-safe). |
| Clicks land in the wrong place on a scaled display | Make sure you use the screenshot's `scale` and `region` when converting coordinates. The server is DPI aware, so `get_screen_size` should report the physical resolution. If another tool in the same process set DPI awareness first, restart the server. |
| Nothing happens in an elevated (Administrator) window | Windows blocks input from a normal-privilege process into elevated windows (UIPI). Run the MCP client as Administrator, or avoid elevated targets. |
| Typing goes to the wrong window | Input goes to the focused window. Click into the target field first. |
| Wrong characters typed | Characters are resolved for the **active** keyboard layout. Characters missing from it are sent as Unicode, which some apps (games, remote desktops, some terminals) ignore. Typos are simulated with a QWERTY neighbour map whatever the layout. |
| Server fails to start with "configuration error" | Read the message: it names the bad key, file, or environment variable. Remember the **double** underscore in `HUMANLIKE_SECTION__KEY`. |
| `HUMANLIKE_CONFIG` file not found | Use an absolute path. A missing explicit file is an error by design. |
| Client shows "Error executing tool …" with no detail | That's an unexpected crash. The full traceback is in the server's stderr log (in Cursor, the MCP output panel). |
| `pip install` fails with "Permission denied" in the pip cache | Use `pip install --no-cache-dir -e .`. |
| Screenshots are huge | Pass `max_width` (e.g. 1280) and/or `format="jpeg"`. |
| Moves feel too fast or slow | Change `mouse.speed`, or `mouse.tick_delay` for a fixed change, or use the `careful` or `fast` preset. |

---

## Limitations

- **Platform.** It is built and tested on Windows.
  - On macOS and Linux (X11), the code paths exist: pyautogui and pynput are cross-platform, and characters fall back to `KeyCode.from_char` with US-layout Shift mapping.
  - However, they are untested.
  - Wayland generally blocks synthetic input.
  - On macOS, `cmd` is the Command key and Accessibility permission is required.
- **Typos assume QWERTY.** `typing.layout` only accepts `qwerty` for now. Typing itself works on any layout; only the *choice of wrong key* assumes QWERTY.
- **Typing time is sequential.** Key presses don't overlap (no rollover), and very fast settings (well above 120 WPM) hit the 5 ms minimum gap between keys.
- **Not a detection-evasion guarantee.** The behaviour is statistically human-like, but it is still injected input (`SendInput`), which software can tell apart from hardware input.
- **No cancellation mid-call** other than the fail-safe. A long `type_text` runs to the end unless the cursor goes to a corner.
- **windmouse uses NumPy's global RNG**, so path randomness can't be seeded per instance. The tests seed `numpy.random` directly.
- **Fail-safe corners are on the primary monitor only** (pyautogui behaviour).
- **Requires `mcp>=2`.** The server uses the `mcp` 2.x API (`mcp.server.mcpserver.MCPServer`, the successor of 1.x's `FastMCP`).

---

## Project layout

```
humanlike-computer-use-mcp/
├── pyproject.toml              # deps + console script humanlike-computer-use-mcp
├── humanlike.example.toml      # every setting with its default
├── README.md
├── scripts/
│   └── realism_report.py       # simulated duration / speed-profile / WPM report
├── src/humanlike_mcp/
│   ├── server.py               # MCPServer app, the 18 tools, entry point
│   ├── config.py               # frozen dataclass settings, presets, layered loading
│   ├── humanize.py             # pure timing/randomness models (paths, typing plan, typos, scroll)
│   ├── mouse.py                # WindMouse paths replayed via pyautogui; clicks, drag, scroll
│   ├── keyboard.py             # pynput typing executor, press_key, hotkey, key names
│   ├── screen.py               # DPI awareness, mss screenshots, monitor geometry
│   └── errors.py               # FailSafeTriggered
└── tests/                      # pytest suite (fakes; optional live smoke test)
```

---

## Credits

- [**windmouse**](https://pypi.org/project/windmouse/): Python implementation of the WindMouse algorithm. The original WindMouse algorithm is by Benjamin J. Land.
- [**PyAutoGUI**](https://github.com/asweigart/pyautogui) by Al Sweigart: cursor movement, mouse buttons, and the corner fail-safe.
- [**pynput**](https://github.com/moses-palmer/pynput) by Moses Palmér: keyboard events, Unicode input, and wheel scrolling.
- [**python-mss**](https://github.com/BoboTiG/python-mss) and [**Pillow**](https://python-pillow.org/): fast screenshots and image encoding.
- [**Model Context Protocol Python SDK**](https://github.com/modelcontextprotocol/python-sdk): the server framework and stdio transport.
