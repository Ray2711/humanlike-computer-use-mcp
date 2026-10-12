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
- [`docs/agent-setup.md`](docs/agent-setup.md): a step-by-step runbook for installing this server and registering it with Cursor, Claude Desktop, Claude Code, Codex, opencode or any stdio client, including verification and troubleshooting.
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
7. [Client/model compatibility](#clientmodel-compatibility)
8. [Safety and the fail-safe](#safety-and-the-fail-safe)
9. [DPI scaling and multiple monitors](#dpi-scaling-and-multiple-monitors)
10. [Running the tests](#running-the-tests)
11. [Measured realism](#measured-realism)
12. [Troubleshooting](#troubleshooting)
13. [Limitations](#limitations)
14. [Project layout](#project-layout)
15. [Credits](#credits)

---

## Features

- **21 tools.**
  - Screen: `screenshot`, `get_screen_size`, `get_cursor_position`, `reset_image_budget`.
  - Calibration: `calibrate`, `calibrate_submit` (measure the model's click-coordinate convention).
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
  - Screenshot and normalized coordinates outside the image are rejected; screen coordinates are clamped to the desktop.
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

### opencode

Add a `local` entry under `mcp` in `~/.config/opencode/opencode.json` or a project `opencode.json`. `command` is an array, and the environment block is called `environment`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "humanlike-computer-use": {
      "type": "local",
      "command": ["C:\\Users\\alice\\humanlike-computer-use-mcp\\venv\\Scripts\\python.exe", "-m", "humanlike_mcp.server"],
      "enabled": true,
      "timeout": 30000,
      "environment": {
        "HUMANLIKE_PRESET": "natural"
      }
    }
  }
}
```

The model must be able to see images. See [opencode + abliteration.ai](#opencode--abliterationai) for the `modalities` setting and the coordinate settings that suit open-weight vision models.

### Using a config file from the client

MCP clients start the server from a working directory you don't control, so `./humanlike.toml` may not be found. Point to the file explicitly with `HUMANLIKE_CONFIG` (as in the Claude example above). Any single setting can also be passed as an environment variable in the `env` block, for example `"HUMANLIKE_TYPING__WPM": "55"`.

Restart or reload the MCP server in your client after editing its config.

---

## Tool reference

**Coordinates.** Mouse tools read `x`/`y` in one of four coordinate spaces, chosen per call with `coordinate_space` or, when omitted, by the `screenshot.coordinate_space` setting (default `"screenshot"`):

| Space | x/y mean | Valid range |
| --- | --- | --- |
| `"screenshot"` (default) | Pixels in the most recent screenshot image, exactly as the model sees them. The server converts them to the screen. | `0..image_width-1`, `0..image_height-1` |
| `"normalized"` | A 0–1000 grid over the most recent screenshot (0,0 top-left, 1000,1000 bottom-right, 500,500 centre), independent of the image size. This is the native output of Qwen3-VL-style models, GLM-4.xV, Gemini and Gemma 4. | `0..1000` on both axes |
| `"screen"` | Physical screen pixels; (0, 0) is the top-left corner of the primary monitor, other monitors may be negative. | Anything; clamped to the desktop |
| `"calibrated"` | The model's own convention, measured with [`calibrate` / `calibrate_submit`](#calibration) and stored as a linear map in `screenshot.calibration`. | Whatever maps onto the latest screenshot (named in its first line) |

- **Out-of-range `"screenshot"` / `"normalized"` / `"calibrated"` coordinates are rejected**, never clamped. The error names the valid range and the image and screen sizes, says nothing was done, and suggests the space the numbers probably belong to (for example `coordinate_space="screen"` when they fit the screen but not the image). It also suggests running `calibrate`; from the second out-of-range call in a session that suggestion becomes explicit.
- Every mouse tool result includes `coordinate_space` (the one actually used) and `cursor` (`{"screen": [x, y], "screenshot": [x, y], "normalized": [x, y]}`, plus `"calibrated"` when that is the default space). Coordinates may be fractional.
- Before the first screenshot, `"screenshot"` coordinates are treated as screen pixels and the result includes a `note` saying so; `"normalized"` and `"calibrated"` coordinates are an error until a screenshot exists.
- The server instructions and the `screenshot` tool description name this machine's primary monitor size, the default screenshot size and the default coordinate space, so the model knows them before its first call.

**Shared optional arguments on action tools:**

| Argument | Meaning |
| --- | --- |
| `humanize` (bool) | `false` skips all human-like timing for this call (instant move, no pauses, no typos). |
| `speed` (number > 0, ≤ 20) | Mouse speed multiplier for this call (`mouse_move`, `click`, `drag`). |
| `overrides` (object) | Any setting for this call only, by dotted key, e.g. `{"mouse.wind": 6, "click.hold_ms": [90, 130]}`. |
| `coordinate_space` (`"screenshot"` / `"normalized"` / `"screen"` / `"calibrated"`) | Mouse tools only. Omit to use the `screenshot.coordinate_space` setting. See the table above. |

Per-call values never persist. Invalid values return a clear error and nothing happens.

### Screen

| Tool | Parameters | Returns |
| --- | --- | --- |
| `screenshot` | `monitor=1` (1 = primary, 2… = others, 0 = all monitors / virtual desktop), `max_width` (omit = `screenshot.max_width`, default 1280; `0` = full resolution; otherwise ≥ 64), `format="png"` (`png` or `jpeg`), `quality=80` (JPEG only, 1–95), `image=true` (`false` = metadata only, no image), `grid` (omit = `screenshot.grid`; labeled grid lines every N units of the active coordinate space, `0` = off, otherwise ≥ 10) | One text block, then the image. The text starts with a **plain-text line** such as `Screenshot 1280x720 of a 2560x1440 screen (monitor 1, top-left at screen 0,0). Click coordinates (coordinate_space="screenshot"): pixels in this image, x 0..1279, y 0..719.` (worded for the active space), then a line telling the model not to guess coordinates if it cannot see the image, then JSON metadata: `screen_resolution`, `image_size`, `image_width`, `image_height`, `region` (the captured area in screen pixels), `scale`/`scale_x`/`scale_y`, `coordinate_space`, `valid_range`, `grid` (when on), `cursor_screen`, `cursor_screenshot` and `cursor_normalized` (`null` if the cursor is outside the capture). This screenshot becomes the reference for `"screenshot"` and `"normalized"` coordinates. With `image=false`, only the text comes back (header plus `screen_resolution`, `region`, `coordinate_space`, cursor positions, `reference_image_size`), and the coordinate reference stays on the last screenshot that had an image. The result never carries `structuredContent`, because some clients (opencode ≥ 1.17.5) then drop the image. With `screenshot.max_session_images` set, the text also reports the remaining image budget, and once it is used up the call returns the metadata-only result with an "Image budget reached" note instead of an image. |
| `get_screen_size` | none | `resolution` (`"WxH"`) and `width`/`height` of the primary monitor, `virtual_desktop` bounds, and a `monitors` list with each monitor's bounds |
| `get_cursor_position` | none | `x`, `y` and `screen` in screen pixels, plus `screenshot` (image pixels in the latest screenshot) and `normalized` (its 0–1000 grid), each `null` if the cursor is outside it |

### Mouse

| Tool | Parameters | Notes |
| --- | --- | --- |
| `mouse_move` | `x`, `y`, `speed?`, `humanize?`, `coordinate_space?`, `overrides?` | Returns `from`, `to` (actual landing point in screen pixels, including jitter), `overshoot`, `points`, `duration_ms`. |
| `click` | `x?`, `y?`, `button="left"` (`left`/`right`/`middle`), `clicks=1` (1–3), `speed?`, `humanize?`, `coordinate_space?`, `overrides?` | Without coordinates it clicks in place. With only one of x/y, the other axis stays where it is. |
| `mouse_down` | `button="left"`, `x?`, `y?`, `humanize?`, `coordinate_space?`, `overrides?` | Press and hold. Pair with `mouse_up`. |
| `mouse_up` | `button="left"` | Always releases, even when the fail-safe is active. |
| `drag` | `start_x`, `start_y`, `end_x`, `end_y`, `button="left"`, `speed?`, `humanize?`, `coordinate_space?`, `overrides?` | Move to start, press, hold, carry, settle, release. Both points are validated before anything moves. |
| `scroll` | `amount` (notches, −500…500; vertical: + = up, horizontal: + = right), `x?`, `y?`, `horizontal=false`, `humanize?`, `coordinate_space?`, `overrides?` | Optionally moves to (x, y) first. |

### Calibration

| Tool | Parameters | Notes |
| --- | --- | --- |
| `calibrate` | `monitor=1`, `format="png"` | Returns instructions plus a **synthetic** image (it never captures or touches the real screen) with the same size as a normal screenshot under the current settings. It shows 6 numbered, high-contrast crosshair targets at known, asymmetric positions and asks the model to report each centre as `[x, y]`, "the exact numbers you would pass to click", answering directly from the image. The text deliberately states **no image size and no pixel ranges**: in tests, naming the size made a 0–1000 model squeeze its y values under the image height. It names a 0–1000 grid only as the fallback for a model with no natural convention. |
| `calibrate_submit` | `points` (`[[x1,y1],...]`, targets in order), `apply=true` | Fits the points per axis (scale and offset, least squares) and compares named conventions: image pixels, 0–1000, 0–1, physical screen pixels, and image pixels after a Qwen-style resize (multiples of 28/32 under common pixel budgets, e.g. a 1280x720 image seen as 1176x672). Returns `matched`, `quality`, `max_error_px` / `rms_error_px` / `per_target_error_px` in screenshot pixels, `max_error_screen_px`, the `fit`, the ranked `hypotheses`, `persist` (`toml` snippet and `env` variables) and `next`. See the acceptance rules below. Nothing changes on an error. |

How `calibrate_submit` decides (tolerance = max(12 px, 1.5% of the longer image side), 19 px at 1280 wide; usable = max(24 px, 3%), 38 px):

1. **Built-in convention first.** If one (`screenshot`, `normalized`, `screen`, …) fits within tolerance, or hits all but at most one target almost exactly (median error ≤ ¼ tolerance), it is used: `quality: "good"`, and the one miss is reported as `ignored_target`. A convention with no free parameters generalises better to real clicks than a free fit through six noisy points.
2. **Free fit** within tolerance, possibly after ignoring one clear outlier (a target whose y was copied from a neighbour, say): `"good"`, stored as `"calibrated"`.
3. **Mixed axes get one targeted retry.** If one axis matches a built-in convention exactly and the other doesn't (for example x on 0–1000, y squeezed toward pixels), the first such submission is rejected with a message naming the wrong axis and its range. Later submissions are judged normally.
4. **Usable** (free fit within the usable bound): applied with `quality: "usable"` and a warning about the expected screen-pixel error.
5. **Rejected** otherwise. The message asks for target order, crosshair centres and one convention for both axes, says not to resubmit the same points, and suggests pointing on a 0–1000 grid if the model estimated pixels. Points that fit only as `[y, x]` get their own message. From the third failed submission (counted until a success, not per image), the error tells the model to stop calibrating and report that it didn't converge, which ends retry loops.

A successful result's `next` tells the model calibration is done and not to call `calibrate` again.

The fitted map is stored relative to the image size (`calibration_units = "image"`) for pixel-like conventions, so it keeps working when `max_width` changes; fixed grids such as 0–1 use `"absolute"`.

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
| `reset_config` | none | Undoes all runtime changes (`set_config` **and** `use_preset`) and resets the image budget counter. |
| `reset_image_budget` | none | Resets the count of images returned in this server session (see `screenshot.max_session_images`) without touching other settings. Returns `previous`, `used`, `limit`, `remaining`. Meant to be called after the user compacted the conversation or started a new session. |
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
| `screenshot.coordinate_space` | `"screenshot"` | `screenshot`, `normalized`, `screen`, `calibrated` | `HUMANLIKE_SCREENSHOT__COORDINATE_SPACE` |
| `screenshot.max_width` | `1280` (px) | 0 (full resolution) or 64–16384 | `HUMANLIKE_SCREENSHOT__MAX_WIDTH` |
| `screenshot.snap_multiple` | `0` (off) | 0–256 | `HUMANLIKE_SCREENSHOT__SNAP_MULTIPLE` |
| `screenshot.grid` | `0` (off) | 0 (off) or 10–1000 | `HUMANLIKE_SCREENSHOT__GRID` |
| `screenshot.calibration` | `[1.0, 0.0, 1.0, 0.0]` | 4 numbers, each −1000–1000; scales non-zero | `HUMANLIKE_SCREENSHOT__CALIBRATION` (`"sx,ox,sy,oy"`) |
| `screenshot.calibration_units` | `"image"` | `image`, `absolute` | `HUMANLIKE_SCREENSHOT__CALIBRATION_UNITS` |
| `screenshot.max_session_images` | `0` (unlimited) | 0–10000 | `HUMANLIKE_SCREENSHOT__MAX_SESSION_IMAGES` |
| `debug.log_path` | `""` (off) | file path | `HUMANLIKE_DEBUG__LOG_PATH` |
| `debug.log_text` | `false` | bool | `HUMANLIKE_DEBUG__LOG_TEXT` |

`HUMANLIKE_PRESET` selects the preset and `HUMANLIKE_CONFIG` points to the TOML file. What each humanization setting means is described in [How the humanization works](#how-the-humanization-works). The `screenshot` settings:

- `coordinate_space`: how mouse tools read x/y when a call omits `coordinate_space` (see [Tool reference](#tool-reference)). The screenshot's first line, the server instructions and the tool descriptions follow it.
- `max_width`: the default screenshot width when the `screenshot` call omits `max_width`. Smaller images cost fewer tokens and are less likely to be shrunk again by the client or provider.
- `snap_multiple`: rounds the image width and height **down** to a multiple of this value (28 for Qwen2.5-VL and UI-TARS-1.5, whose image processors resize to multiples of 28; 32 for Qwen3-VL). The model then works on exactly the image the server sent, so pixel coordinates need no correction. Width and height may then scale slightly differently; the server tracks both.
- `grid`: draws faint magenta lines with yellow-on-black labels every N units of the active coordinate space (image pixels, 0–1000 grid units, or screen pixels). The labels are valid coordinates, which helps weaker models read positions. It covers some pixels, so leave it off for models that ground well.
- `max_session_images`: how many images (`screenshot` with an image, and `calibrate`) this server process returns before it stops attaching them. While budget remains, each image result says how many are left. Once it is used up, `screenshot` returns its metadata-only result with a note telling the model to ask you to compact or start a new session and then call `reset_image_budget`, and `calibrate` refuses. `image=false` screenshots are always free. Use it when the provider caps images per request and you can't install the [opencode plugin](#image-limit-more-images-than-the-limit-of-4); with the plugin installed, leave it at `0`.
- `calibration` / `calibration_units`: the map used by `coordinate_space = "calibrated"`, written by `calibrate_submit`. The fraction of the image width is `sx * X + ox` (height: `sy * Y + oy`), where `X = (x + 0.5) / image_width` with `"image"` units and `X = x` with `"absolute"` units.

The `debug` settings:

- `log_path`: when set, the server appends one JSON line per event to this file (parent folders are created). Events: `screenshot` (image size, region, scale, active space), `mouse` (tool, raw x/y, coordinate space, mapped screen x/y, whether they were in bounds, or the error), `type_text` (`text_length` only), `calibrate` (image size, true target centres) and `calibrate_submit` (points, match, residual). Write errors are reported once on stderr and never fail a tool.
- `log_text`: also log the typed text. Off by default; leave it off when the agent types passwords.

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

## Client/model compatibility

When a model describes the screen correctly but its clicks land in the wrong place, it is answering in a different coordinate convention than the server expects: 0–1000 instead of pixels, pixels of an image its provider resized, and so on. Vision models rarely know which one they use, and providers rarely document it, so **measure it**:

1. Ask the model to call `calibrate`. It gets a synthetic image the size of a normal screenshot with six numbered targets.
2. It reports the target centres to `calibrate_submit` exactly as it would click them.
3. The server fits the points, names the matching convention (or a custom linear map), makes it the session default and prints the TOML / env values to make it permanent.

From then on the model keeps answering the way it naturally does, and the server converts. Out-of-range errors suggest calibrating, and the server instructions tell the model to calibrate once when clicks land off-target. Set `HUMANLIKE_DEBUG__LOG_PATH` to see exactly what the model sent and where each click went.

If a model can't see images at all (some clients replace MCP images for models they consider text-only), it can't calibrate either: the screenshot's second line tells it to say so instead of guessing.

If you already know the model family, you can skip calibration and pick the space directly:

| Model family | Native click/box coordinates | Suggested settings |
| --- | --- | --- |
| Claude, GPT-4o/5, most general models | Pixels in the image as received | `coordinate_space="screenshot"` (default) |
| Qwen2.5-VL and fine-tunes (UI-TARS-1.5, many abliterated "VL" models) | Absolute pixels of the image **after** the processor resizes it to multiples of 28 ([Qwen blog](https://qwenlm.github.io/blog/qwen2.5-vl/), [UI-TARS coordinates](https://github.com/bytedance/UI-TARS/blob/main/README_coordinates.md)) | `"screenshot"` with `snap_multiple = 28` |
| Qwen2-VL, Qwen3-VL, Qwen3.5 (multimodal) | 0–1000 relative grid ([Qwen3-VL #1521](https://github.com/QwenLM/Qwen3-VL/issues/1521)) | `"normalized"` |
| GLM-4.1V / 4.5V / 4.6V | 0–1000 (GUI agent prompts use 0–999) ([GLM-V](https://github.com/zai-org/GLM-V)) | `"normalized"` |
| Gemini, Gemma 4 | `box_2d` on a 0–1000 grid ([Gemma 4 blog](https://huggingface.co/blog/gemma4)) | `"normalized"` |
| UI-TARS v1 (7B-DPO etc.) | 0–1000 relative ([UI-TARS #173](https://github.com/bytedance/UI-TARS/issues/173)) | `"normalized"` |
| abliteration.ai `abliterated-model` | 0–1000 (measured, [below](#measured-abliterated-model)) | `"normalized"` |

Whatever the setting, the model can always pass `coordinate_space` per call, and an out-of-range value returns an error that suggests the right space. If a weaker model misreads positions, turn on `screenshot.grid` (e.g. `100` in `"normalized"` space, or `200` in `"screenshot"` space) so it can read coordinates off labeled lines.

### opencode + abliteration.ai

[abliteration.ai](https://docs.abliteration.ai/models) (often called "abliterated.ai") serves three models through an OpenAI-compatible API at `https://api.abliteration.ai/v1`. It is a built-in opencode provider via models.dev.

| Model | Images | Notes |
| --- | --- | --- |
| `abliterated-model` | **Yes** | Multimodal base model, 256K context. The only one that can see screenshots. Its base model isn't named. **Measured: it points on a 0–1000 grid** (10 calibration runs through opencode 2.0.27; see [Measured: abliterated-model](#measured-abliterated-model)). Use `coordinate_space = "normalized"`. |
| `abliterated-model-large-v2` | **No** (text-only, GLM-5.3) | The model abliteration.ai's [opencode guide](https://docs.abliteration.ai/integrations/opencode) recommends picking. It rejects image input with HTTP 400. |
| `abliterated-model-large` | **No** (text-only, GLM-5.2) | Same. |

What opencode does with MCP screenshots:

- **It strips images for models that aren't declared image-capable.** An image is replaced with the text `ERROR: Cannot read image (this model does not support image input). Inform the user.` before the request is sent ([opencode #20802](https://github.com/anomalyco/opencode/issues/20802), [#32832](https://github.com/anomalyco/opencode/issues/32832)). This matters for the large models; `abliterated-model` receives the images.
- **For OpenAI-compatible providers, MCP images arrive as a separate synthetic user message** after the tool result, because Chat Completions tool messages can't carry images (maintainer comment in [#20802](https://github.com/anomalyco/opencode/issues/20802)). The model still sees the image, but the text header and the image are in different messages. That's why every size and coordinate fact is repeated in plain text.
- **Since 1.17.5, a tool result that has `structuredContent` loses its image** ([#32832](https://github.com/anomalyco/opencode/issues/32832)). This server's `screenshot` never returns `structuredContent`, so it isn't affected.

Recommended setup:

1. **Use `abliterated-model`** (`/models` → abliteration.ai → *Abliterated Model*), not the large models, for any computer-use session.
2. **Declare image input and register this server.** Check your version with `opencode --version` and the provider ID with `opencode models` (lines read `provider/model`; on opencode 2.0.27 it is `abliteration-ai/abliterated-model`). Merge into `~/.config/opencode/opencode.json` (Windows: `%USERPROFILE%\.config\opencode\opencode.json`), using the absolute venv Python. The `environment` block already holds the measured setting for `abliterated-model` (step 4).

   **opencode 2.x** (the shape the calibration harness uses and opencode 2.0.27 accepts):

   ```json
   {
     "$schema": "https://opencode.ai/config.json",
     "providers": {
       "abliteration-ai": {
         "models": { "abliterated-model": { "capabilities": { "input": ["text", "image"] } } }
       }
     },
     "mcp": {
       "servers": {
         "humanlike-computer-use": {
           "type": "local",
           "command": ["C:\\Users\\alice\\humanlike-computer-use-mcp\\venv\\Scripts\\python.exe", "-m", "humanlike_mcp.server"],
           "environment": {
             "HUMANLIKE_SCREENSHOT__MAX_WIDTH": "1280",
             "HUMANLIKE_SCREENSHOT__COORDINATE_SPACE": "normalized"
           }
         }
       }
     }
   }
   ```

   **opencode 1.x:** the same values under `"provider"` → `"abliteration"` → `"models"` → `"abliterated-model"` → `"modalities": {"input": ["text","image"], "output": ["text"]}`, and the server directly under `"mcp"` → `"humanlike-computer-use"` with `"type": "local"`, `"command"`, `"enabled": true`, `"timeout": 30000` (opencode's 5 s default for listing tools can be too short for a cold Python start) and the same `"environment"`.

   Restart opencode after editing the config.
3. **Calibrate** (for another model, or to re-check). In a new opencode session, send:

   > Call the calibrate tool, then call calibrate_submit with the centre of each numbered target exactly as you would pass it to click. Report the tool's result (matched convention and max_error_px).

   A good result has `quality: "good"` and names the convention in `matched`, for example `0-1000 normalized grid` or `pixels of the image resized to …`. It applies for the rest of the session. `"usable"` is applied too, but `max_error_screen_px` says how far clicks may land off. If the server answers with an axis message ("Your x values match … but your y values do not"), the model gets one retry with the corrected axis. If calibration doesn't converge after three submissions, the server tells the model to stop; start a new session and try once more.
4. **Make it permanent.** Copy the `persist.env` values from the result into the `environment` block (or `persist.toml` into a `humanlike.toml` that `HUMANLIKE_CONFIG` points to). For a built-in match that is only `HUMANLIKE_SCREENSHOT__COORDINATE_SPACE`. For **`abliterated-model` it is `"normalized"`**, already in the example above. A free fit adds the map, for example:

   ```json
   "HUMANLIKE_SCREENSHOT__COORDINATE_SPACE": "calibrated",
   "HUMANLIKE_SCREENSHOT__CALIBRATION": "1.172161,0.0,1.168831,0.0",
   "HUMANLIKE_SCREENSHOT__CALIBRATION_UNITS": "image"
   ```

   Calibrate again after switching models. Don't leave a model that points on 0–1000 on the default `"screenshot"` space: its header says "pixels in this image, y 0..719", and the measured hit rate was 0%.
5. **Check the debug log** if clicks are still off: add `"HUMANLIKE_DEBUG__LOG_PATH": "C:\\Users\\alice\\humanlike-debug.jsonl"` to `environment`. Each `mouse` line in the `HUMANLIKE_DEBUG__LOG_PATH` file shows the raw x/y the model sent, the coordinate space used, the mapped screen position and whether it was in bounds; each `screenshot` line shows the image size, region and scale. Typed text is logged only as its length. Remove the variable when you're done.

#### Measured: abliterated-model

Ten calibration runs through opencode 2.0.27 against an input-free copy of this server (harness in [`integrations/opencode/calibration-test/`](integrations/opencode/calibration-test/)). Monitor 1920x1080, calibration image 1280x720.

- **It points on a 0–1000 grid, very precisely, when it answers directly.** x matched 0–1000 within about 1 unit in every run.
- **It sometimes squeezes y toward pixels.** In 4 of the 10 runs, y values for targets in the lower half came out under the image height (600 for a target at 860 on the 0–1000 scale). This happened most when the text named the image size ("1280x720"), and the model then "corrected" values above 720. The calibration prompt therefore names no size, and the server answers this pattern with an axis-specific retry.
- **Pixel guessing is imprecise.** In one run it estimated image pixels instead, 40–100 px off. That is rejected with a suggestion to point on a 0–1000 grid.

Click accuracy, measured offline by mapping every point the model reported to screen pixels and comparing with the true target centres (`accuracy.py` in the harness; screen pixels on 1920x1080):

| When the model points natively (0–1000) | Median error | 32 px icon hit | 100x32 button hit |
|---|---|---|---|
| Server on the default `"screenshot"` space | 324 px | 0% | 0% |
| `coordinate_space = "normalized"` | 3 px | 83% | 93% |

To repeat the measurement (it uses your stored opencode credentials and API credits, and never sends input): `run.py serve` starts a private `opencode serve` on port 4917 with a generated config whose only tools are `calibrate`, `calibrate_submit`, `get_config`, `get_screen_size` and a text-only `screenshot`; opencode's shell, edit and write tools are denied. `run.py audit` dumps the tools the model would get and aborts before the provider is called; run it until it lists `humanlike-calib (5 tools)`, because the first request after a start can race the MCP connection. Then `run.py 1` (2, 3, …) runs one calibration prompt, `accuracy.py` reports click accuracy, and `run.py stop` stops the private server only. Config, transcripts and logs go to `.calib-runs/`, which is git-ignored.

**Setting for `abliterated-model`:** `HUMANLIKE_SCREENSHOT__COORDINATE_SPACE=normalized` (no calibration vector needed). With it, the screenshot header says "a 0–1000 grid over this image, not pixels; y 0..1000", which matches what the model does natively.

#### Image limit: "more images than the limit of 4"

`Error: This request has more images than the limit of 4. Send fewer images and try again.` comes from the provider, not from this server. opencode re-sends every earlier screenshot in the conversation with each request, so the fifth image in a session breaks every later turn. abliteration.ai doesn't publish a hard number; its [vision docs](https://abliteration.ai/docs/vision) say to keep it "≤4" per request, and its [multimodal page](https://abliteration.ai/multimodal-llm-api) says "the effective image count depends on your plan and project settings". Its other models are text-only ([images docs](https://docs.abliteration.ai/capabilities/images)), so switching models doesn't help. An MCP server can't edit the client's history, so the fix belongs in opencode:

1. **Install the bundled plugin** [`integrations/opencode/plugins/keep-last-images.ts`](integrations/opencode/plugins/keep-last-images.ts). It keeps only the newest 3 images in each request. Older screenshot results keep their text, with `[older screenshot removed to stay under the provider's image limit]` instead of the image. It edits only the per-request copy of the messages, so the stored session is unchanged, and it also unblocks a session that already hit the error. Copy it into opencode's plugin folder and restart opencode:

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\opencode\plugins" | Out-Null
   Copy-Item integrations\opencode\plugins\keep-last-images.ts "$env:USERPROFILE\.config\opencode\plugins\"
   ```

   On macOS/Linux: `mkdir -p ~/.config/opencode/plugins && cp integrations/opencode/plugins/keep-last-images.ts ~/.config/opencode/plugins/`. Files in `~/.config/opencode/plugins/` (global) or `<project>/.opencode/plugins/` load automatically at startup ([plugin docs](https://opencode.ai/docs/plugins/)); no `opencode.json` entry is needed. To keep a different number of images, set `OPENCODE_KEEP_LAST_IMAGES` (e.g. `2`) in the shell that starts opencode.

   **opencode version.** Check it with `opencode --version`. The plugin API changed between major versions, and each version needs its own file:

   | opencode | File to install | Plugin API |
   |---|---|---|
   | 2.x (checked against 2.0.27) | `integrations/opencode/plugins/keep-last-images.ts` | default export `{ id, setup }`; `setup(api)` registers `api.session.hook("context" \| "compaction" \| "generate", fn)`, and `fn` rewrites `event.messages` (`LLM.Message` objects with `content` parts: `media` for user images, `tool-result` → `result.value[]` `file` parts for tool images) |
   | 1.x | `integrations/opencode/legacy-1.x/keep-last-images.ts` | named plugin function returning the `experimental.chat.messages.transform` hook ([hook type in `@opencode-ai/plugin`](https://cdn.jsdelivr.net/npm/@opencode-ai/plugin/dist/index.d.ts), [PR #5207](https://github.com/anomalyco/opencode/pull/5207)) |

   Installing the 1.x file on opencode 2.x fails at startup with `Plugin must export a default definition with an id and an effect or setup function.` The 2.x loader accepts only that default export. The 1.x loader calls every export as a plugin function, so one file can't serve both versions. Install exactly one of them, under the name `keep-last-images.ts`. The 2.x API was read from the code bundled in the installed `@opencode/cli` 2.0.27; it has no published type package, so a later 2.x release could change it. Both files are unit-tested against those message shapes, but not against a running opencode.
2. **Optional server-side budget** for setups without the plugin: `"HUMANLIKE_SCREENSHOT__MAX_SESSION_IMAGES": "3"` in `environment`. After 3 images the server stops attaching images and tells the model to ask you to `/compact` or `/new`. It counts per server process, not per opencode session, so the model calls `reset_image_budget` after you've compacted or started over.
3. **opencode's built-in options don't target image counts.** `compaction.auto` (default `true`) only compacts when the context window is full, and `compaction.prune` (default `false`) clears old tool outputs, including their images, only beyond a token threshold ([config docs](https://opencode.ai/docs/config/#compaction)). Turning `prune` on doesn't hurt, but don't rely on it for a 4-image cap. There is no setting to keep only the last N images.
4. **A session that is already stuck** recovers as soon as the plugin is installed and opencode restarted: reopen it with `/sessions` and continue. Without the plugin, start a fresh session with `/new` (alias `/clear`). `/compact` (alias `/summarize`) replaces the history with a summary, but on some opencode versions the summarizing request itself still carries the old images and fails the same way. `/undo` removes the last turn and its screenshot.

---

## Safety and the fail-safe

- **Corner fail-safe.** If the cursor sits in any corner pixel of the **primary** monitor, every mouse action and every keystroke aborts with:
  > `Fail-safe triggered: the mouse cursor is in a corner of the primary screen, which aborts all automated input. …`

  The check runs at every point of a mouse path (through pyautogui) and **between every keystroke** of `type_text`, so long typing jobs can be stopped too. Move the cursor away from the corner to continue.
- **Disabling it** is not recommended. You can set `safety.failsafe=false`, `HUMANLIKE_SAFETY__FAILSAFE=0`, or `set_config({"safety.failsafe": false})`.
- **No stuck keys or buttons.** Clicks and drags force-release the mouse button if interrupted. `type_text` always releases Shift, and `hotkey` always releases every key it pressed. `mouse_up` works even while the fail-safe is active.
- **Coordinate checks.** Screenshot-space and normalized coordinates outside the latest screenshot are rejected with an error before anything moves. Targets are clamped to the virtual desktop. While the fail-safe is on, targets are also nudged 1 px off the exact corner pixels, so the server never trips its own fail-safe.
- **Limits.**
  - `type_text` refuses text longer than `safety.max_text_length` (10,000).
  - `scroll` accepts up to ±500 notches.
  - `press_key` accepts up to 100 presses.
  - `wait` accepts up to 300 s.
- **Serialized input.** Action tools hold a lock, so two simultaneous tool calls never interleave their input events.

---

## DPI scaling and multiple monitors

- **DPI.** At startup, and before `pyautogui` is imported, the server calls `SetProcessDpiAwareness(2)` (per-monitor DPI aware). Screenshots, cursor positions, and mouse moves then all use **physical pixels**. On a 4K screen at 150% scaling, `get_screen_size` reports 3840×2160, and screenshot pixel (x, y) is screen pixel (x, y) when not downscaled.
- **Downscaled screenshots.** Screenshots default to 1280 px wide (`screenshot.max_width`), because some MCP clients silently shrink larger images, which would make the model's coordinates wrong. With `coordinate_space="screenshot"` or `"normalized"`, the server maps coordinates back to the screen itself. With `coordinate_space="screen"`, use `screen_x = region.left + image_x * scale_x` and `screen_y = region.top + image_y * scale_y`.
- **Multiple monitors.**
  - `screenshot(monitor=0)` captures the whole virtual desktop, and `region` tells you where it starts. On some layouts that is at negative coordinates. Screenshot-space clicks are mapped into whichever monitor or area the latest screenshot captured.
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
| `test_calibration.py` | The calibration image (right size and format, target centres, no screen capture or input); fits for image pixels, 0–1000, 0–1, smart-resize and arbitrary resized pixels, and noisy points; real abliterated-model submissions (copied y ignored as an outlier, built-in convention preferred over an overfitted free fit, mixed-axis retry hint, imprecise pixel guesses rejected); `good` / `usable` tiers, the failure cap and stop message, no image size in the prompt; rejection of inconsistent and swapped points; applying, persisting (TOML and env round trip) and reusing the calibration after a `max_width` change; the calibrated space in click, drag and scroll; the repeated-error hint; debug log records and `type_text` redaction |
| `test_image_budget.py` | `screenshot.max_session_images`: countdown lines, metadata-only results once used up, free `image=false` shots, `calibrate` counting and refusing, `reset_image_budget` and `reset_config`, runtime changes to the limit |
| `test_safe_server.py` | The input-free calibration launcher in `integrations/opencode/calibration-test/`: its mouse and keyboard backends refuse every call, and a real stdio session lists only read-only tools, returns text-only screenshots and rejects `click` |
| `test_opencode_plugin.py` | Runs `integrations/opencode/keep-last-images.test.mjs` under Node (skipped without Node ≥ 22.18): opencode 2.x plugin (default `{ id, setup }` export, `context`/`compaction`/`generate` hooks, newest-N pruning of `media` and `tool-result` images, immutable message replacement, `keep` option and env var) and the 1.x legacy plugin (exports only functions, in-place transform) |
| `test_server.py` | All 21 tools listed; config tools; actions routed to backends; fail-safe and validation surfaced as tool errors; real screenshots in-process (header line, snapping, grid, config-driven defaults, no `structuredContent`); screenshot/normalized/screen coordinate mapping and out-of-range rejection; and an **end-to-end stdio session** through the official MCP client (initialize with the display hint in the instructions, list tools, `get_config` showing an env-layer value, `get_screen_size`, `screenshot`, an invalid `set_config`) |
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
| Clicks land in the wrong place on a scaled display | Pass pixel coordinates from the **latest** screenshot with the default `coordinate_space="screenshot"`, and don't scale them yourself. Check `cursor.screenshot` in the result. Keep `max_width` at 1280 or lower so the client doesn't shrink the image further. The server is DPI aware, so `get_screen_size` should report the physical resolution. If another tool in the same process set DPI awareness first, restart the server. |
| Error "… is outside the valid range for coordinate_space=…" | The model used a different convention than the active space (often screen pixels or a 0–1000 grid). Have it run `calibrate` and `calibrate_submit`, or set `screenshot.coordinate_space` to the model's native convention; see [Client/model compatibility](#clientmodel-compatibility). |
| The model describes the screen correctly but clicks land off-target (by a fixed factor, or drifting toward one corner) | Coordinate convention mismatch. Run [calibration](#calibration) and persist the values it prints. Set `HUMANLIKE_DEBUG__LOG_PATH` to see the raw coordinates and where they were mapped. |
| `This request has more images than the limit of 4` (opencode + abliteration.ai, or another provider with an image cap) | The client re-sends every earlier screenshot. Install the [keep-last-images opencode plugin](#image-limit-more-images-than-the-limit-of-4), restart opencode and continue the session; or start a new session with `/new`. Without the plugin, set `HUMANLIKE_SCREENSHOT__MAX_SESSION_IMAGES=3`. |
| A screenshot says "Image budget reached" | `screenshot.max_session_images` is used up. Run `/compact` or `/new` in the client, then let the model call `reset_image_budget` (or raise the limit). |
| `calibrate_submit` says the points are inconsistent | The model misread the targets, reported them out of order, or estimated pixels. Follow the error: one convention for both axes, or point on a 0–1000 grid. After three failed submissions the server tells the model to stop; start a new session and calibrate once more. If the model's family is known, set `coordinate_space` from the [table](#clientmodel-compatibility) instead. |
| The model can't describe what's on the screen | It isn't receiving the image. In opencode, use an image-capable model and declare `modalities.input: ["text", "image"]`; see [opencode + abliteration.ai](#opencode--abliterationai). |
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
├── integrations/opencode/
│   ├── plugins/keep-last-images.ts  # opencode 2.x plugin: keep only the newest N images per request
│   ├── legacy-1.x/keep-last-images.ts  # same plugin for opencode 1.x
│   ├── keep-last-images.test.mjs    # node --test suite for both
│   └── calibration-test/            # isolated, input-free opencode harness for calibration runs
├── scripts/
│   └── realism_report.py       # simulated duration / speed-profile / WPM report
├── src/humanlike_mcp/
│   ├── server.py               # MCPServer app, the 21 tools, entry point
│   ├── calibration.py          # calibration image, convention fitting (pure)
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
