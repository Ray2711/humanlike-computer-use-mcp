# Agent usage guide: humanlike-computer-use-mcp

How an AI agent should **use** this server's tools effectively and safely. For installation, see [agent-setup.md](agent-setup.md). For every parameter and setting, see the [README](../README.md#tool-reference).

> These tools drive the user's **real** mouse and keyboard. Get the user's consent before the first input action of a task. Read-only tools (`screenshot`, `get_screen_size`, `get_cursor_position`, `get_config`, `list_presets`) are always fine.

---

## 1. The core loop: look, then act, then look again

1. `screenshot`: see the current state.
2. Pick a target in **image** pixels and convert it to **screen** pixels (section 2).
3. Act: `click`, `type_text`, `hotkey`, `scroll`, …
4. `wait` briefly if the UI needs time (page loads, animations), then `screenshot` again to confirm the result before the next step.

Don't chain many input actions blindly. Windows move, dialogs pop up, and focus changes.

---

## 2. Screenshot to screen coordinates

`screenshot` returns two content items: the **image**, and a **JSON text** block like this:

```json
{
  "image_width": 1280,
  "image_height": 720,
  "format": "png",
  "region": {"left": 0, "top": 0, "width": 1920, "height": 1080},
  "scale": 1.5,
  "to_screen": "screen_x = region.left + image_x * scale; screen_y = region.top + image_y * scale",
  "cursor": [961, 540]
}
```

**Always convert before clicking:**

```
screen_x = region.left + image_x * scale
screen_y = region.top  + image_y * scale
```

Round the results to integers.

- **`scale`** is `1.0` when the image isn't downscaled. With `max_width`, the image is shrunk proportionally and `scale = original_width / max_width`.
- **`region`** is the captured area in screen pixels.
  - With `monitor=0` (the default, all monitors), `region.left` / `region.top` can be **negative**, for example when a second monitor sits left of the primary.
  - With `monitor=1`, it's the primary monitor, whose origin is (0, 0).
- **`cursor`** is the current mouse position in *screen* pixels, which is handy for sanity checks.
- Coordinates are **physical pixels**. The server is DPI aware, so don't apply Windows display scaling (125% / 150%) yourself.

**Choosing `max_width`:**

- 1280–1600 is a good balance of legibility and payload size for full-HD and larger screens.
- Go smaller (e.g. 1024) for quick "did it work?" checks.
- Use `format: "jpeg"` (with `quality` around 70–80) to shrink payloads further. Use PNG when you need to read small text.
- To read a specific monitor at full detail, use `monitor: 2` (etc.) without `max_width`. Call `get_screen_size` to list monitors and their bounds.

**Worked example.** The screenshot was taken with `max_width: 1280` on a 1920×1080 primary monitor (`scale` 1.5, `region` origin (0, 0)). A button appears at image (400, 300), so you click screen (0 + 400×1.5, 0 + 300×1.5) = **(600, 450)**.

---

## 3. Choosing a preset

| Preset | Use when |
| --- | --- |
| `natural` (default) | Normal work. Human pace, curved mouse paths, ~70 WPM, occasional corrected typos. |
| `careful` | Precise or fiddly UIs (small targets, sliders), or when the user wants calm, slow behaviour. ~45 WPM, fewer overshoots, 1 px jitter. |
| `fast` | Long, routine sessions where speed matters but human-like input is still wanted. ~100 WPM, faster mouse. |
| `instant` | Debugging, or when human-likeness doesn't matter. No delays, no curves, no typos. |

- Switch for the whole session with `use_preset {"name": "careful"}`. This clears earlier `set_config` changes. Return to defaults with `reset_config`.
- Inspect the active values with `get_config`. Each setting reports its `source`.
- Fine-tune for the session with `set_config {"updates": {"typing.wpm": 55}}`. The call is all-or-nothing and validated; an invalid value returns an error and nothing changes.

Prefer **per-call overrides** (next section) for one-off needs, rather than changing session config and forgetting to restore it.

---

## 4. Per-call overrides

Every action tool accepts:

- `humanize: false`: no human timing for this call (instant move, no pauses, no typos).
- `speed` on `mouse_move`, `click` and `drag`: a mouse speed multiplier for this call.
- `overrides`: any setting, by dotted key, for this call only.

None of these persist. Examples:

```json
{"tool": "click", "arguments": {"x": 812, "y": 455,
  "overrides": {"mouse.overshoot_enabled": false, "mouse.landing_jitter_px": 0}}}
```

```json
{"tool": "mouse_move", "arguments": {"x": 1500, "y": 300, "speed": 1.5}}
```

```json
{"tool": "type_text", "arguments": {"text": "Thanks!", "wpm": 50}}
```

**Tiny targets** (checkboxes, close buttons, splitter handles): set `mouse.landing_jitter_px: 0` and `mouse.overshoot_enabled: false`. The default jitter of ±2 px is usually fine for normal buttons.

---

## 5. Typing: when to turn typos off

By default, `type_text` makes occasional **neighbour-key typos (about 2% per key)** and fixes them with Backspace. The final text is always exactly what you asked for, but the intermediate keystrokes contain wrong characters. **Pass `typo_rate: 0`** whenever intermediate wrong keystrokes could cause harm:

| Situation | Why |
| --- | --- |
| **Passwords, PINs, secrets** | Wrong characters may trigger lockouts or validation; some fields don't handle Backspace well. Also never echo the secret back in chat. |
| **Code, shell commands, terminals, REPLs** | Autocomplete, auto-closing brackets, or key bindings can react to the wrong character before Backspace arrives. |
| **Search boxes with live results, or forms with autocomplete** | The typo can select a suggestion. |
| **Exact identifiers** (URLs, emails, IDs, serial numbers) and **single-key shortcuts in apps** (vim, games) | Every keystroke matters. |
| **Fields with a character limit** | The extra characters may be rejected. |

```json
{"tool": "type_text", "arguments": {"text": "git status\n", "typo_rate": 0}}
```

More typing notes:

- **Newlines** (`\n`) press **Enter** and tabs (`\t`) press **Tab**. To type a multi-line text into a chat box where Enter sends the message, type line by line and use `hotkey "shift+enter"` between lines.
- Text goes to the **focused** window. Click into the field first.
- Characters that aren't on the active keyboard layout (emoji, CJK, accented letters on a US layout) are sent as Unicode. A few apps (games, remote desktops, some terminals) ignore those.
- `type_text` is limited to `safety.max_text_length` (10,000 characters). For long content, pass `humanize: false` or split the text into chunks.
- **Typing time** is about `len(text) / 5 / wpm` minutes, so 500 characters at 70 WPM take about 85 s. For long text where human pacing isn't important, use `humanize: false`.

---

## 6. Hotkeys and key presses

`hotkey` takes a `+`-joined string or a list:

```json
{"tool": "hotkey", "arguments": {"keys": "ctrl+shift+t"}}
{"tool": "hotkey", "arguments": {"keys": ["alt", "tab"]}}
{"tool": "hotkey", "arguments": {"keys": "ctrl++"}}
```

- Names are case-insensitive, and spaces, `-` and `_` are ignored.
- Common names: `ctrl`, `shift`, `alt`, `win` (also `cmd`/`super`/`meta`; it is ⌘ on macOS), `enter`, `esc`, `tab`, `space`, `backspace`, `delete`, `home`, `end`, `pageup`, `pagedown`, `up`/`down`/`left`/`right`, `f1`–`f24`, `printscreen`, `menu`, and media keys such as `volumeup` or `playpause`.
- Modifiers are pressed first, whatever order you write them in, then the main key. Keys are released in reverse order.
- Use `ctrl++` for Ctrl and Plus.
- Duplicate keys and unknown names are rejected with an error.
- Letter keys in hotkeys are sent as physical keys, so `ctrl+c` works on any layout.

`press_key` taps one key, optionally repeated:

```json
{"tool": "press_key", "arguments": {"key": "down", "presses": 5}}
{"tool": "press_key", "arguments": {"key": "enter"}}
```

`press_key` with a single shifted character (e.g. `"!"` or `"A"`) presses Shift for you.

---

## 7. Scrolling

- `amount` is in **wheel notches**. One notch is one standard mouse-wheel click, typically about 3 lines in most apps (this depends on the system setting).
- **Vertical:** positive = **up**, negative = **down**. **Horizontal** (`horizontal: true`): positive = **right**.
- The range is −500…500.
- Pass `x`, `y` to move over the scrollable area first. Scroll events go to the window **under the cursor**, not necessarily the focused one.
- The server scrolls in human "flicks" of 3–6 notches with short pauses between them, so large amounts take a little time.

```json
{"tool": "scroll", "arguments": {"amount": -10, "x": 960, "y": 600}}
```

Scroll, then take a `screenshot` before scrolling more. Don't overshoot long pages blindly.

---

## 8. Drag and drop, and holding buttons

- `drag` covers start-to-end moves: it moves to the start, presses, pauses, carries the cursor slower and exactly to the end, settles, and releases. It's suitable for sliders, file drag-and-drop, window resizing, and selecting text.
- For multi-step gestures (e.g. hold, then scroll, then release), use `mouse_down`, then other actions, then `mouse_up`. **Always call `mouse_up` afterwards**, even when an intermediate step fails. `mouse_up` works even while the fail-safe is active.

---

## 9. Errors you'll see

| Error text (starts with) | Meaning | What to do |
| --- | --- | --- |
| `Fail-safe triggered: the mouse cursor is in a corner of the primary screen…` | The **user** (or a stray move) put the cursor in a primary-monitor corner, which aborts all automated input. Long `type_text` calls stop mid-way. | **Stop.** Treat it as the user saying "halt". Tell the user and wait for their go-ahead before doing anything else. Don't retry automatically, and don't disable `safety.failsafe`. If you resume typing, check with a screenshot what was already typed. |
| `Unknown setting '…'` / `… is above the maximum …` / `min … is greater than max …` | Invalid `overrides` or `set_config` value | Fix the key or value; see `get_config` for valid keys |
| `Unknown key name '…'` / `Duplicate key in hotkey …` | Bad `press_key` / `hotkey` input | Use the names in section 6 |
| `Text is N characters; the limit is …` | `type_text` above `safety.max_text_length` | Split the text, or ask the user before raising the limit |
| `monitor must be between 0 and N` | Bad `screenshot` monitor index | Call `get_screen_size` |

Off-screen coordinates are **clamped** to the desktop, not rejected. If `mouse_move` returns a `to` far from what you asked for, your coordinate conversion is probably wrong.

---

## 10. Example sequences

### Open a URL in the browser's address bar

```text
screenshot {"max_width": 1280}                   -> confirm the browser is focused/visible
hotkey     {"keys": "ctrl+l"}                    -> focus the address bar (cmd+l on macOS)
type_text  {"text": "https://example.com", "typo_rate": 0}
press_key  {"key": "enter"}
wait       {"seconds": 2}
screenshot {"max_width": 1280}                   -> verify the page loaded
```

### Click a button seen in a downscaled screenshot

```text
screenshot {"max_width": 1280}
  -> metadata: scale 1.5, region {left: 0, top: 0}; button at image (842, 611)
click      {"x": 1263, "y": 917}                 -> 842*1.5, 611*1.5 (rounded)
screenshot {"max_width": 1280}                   -> verify
```

### Fill a form field and submit

```text
click      {"x": 700, "y": 380}                  -> focus "Name"
hotkey     {"keys": "ctrl+a"}                    -> select existing content
type_text  {"text": "Jane Doe"}                  -> natural typing is fine here
press_key  {"key": "tab"}
type_text  {"text": "jane.doe@example.com", "typo_rate": 0}
click      {"x": 820, "y": 640}                  -> Submit
wait       {"seconds": 1}
screenshot {"max_width": 1280}
```

### Run a terminal command

```text
click      {"x": 900, "y": 700}                  -> focus the terminal
type_text  {"text": "python --version\n", "typo_rate": 0}
wait       {"seconds": 1}
screenshot {"monitor": 1}                        -> read the output at full resolution
```

### Read further down a long page

```text
scroll     {"amount": -8, "x": 960, "y": 600}
screenshot {"max_width": 1280}
scroll     {"amount": -8}
screenshot {"max_width": 1280}
```

### Precise slider drag

```text
drag       {"start_x": 410, "start_y": 520, "end_x": 655, "end_y": 520,
            "overrides": {"mouse.landing_jitter_px": 0}}
screenshot {"max_width": 1280}                   -> confirm the new value
```

### Quick, non-human batch work (user agreed)

```text
use_preset {"name": "instant"}
... actions ...
reset_config                                      -> back to natural
```
