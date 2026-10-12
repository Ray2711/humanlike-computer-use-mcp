# Agent usage guide: humanlike-computer-use-mcp

How an AI agent should **use** this server's tools effectively and safely. For installation, see [agent-setup.md](agent-setup.md). For every parameter and setting, see the [README](../README.md#tool-reference).

> These tools drive the user's **real** mouse and keyboard. Get the user's consent before the first input action of a task. Read-only tools (`screenshot`, `get_screen_size`, `get_cursor_position`, `get_config`, `list_presets`) are always fine.

---

## 1. The core loop: look, then act, then look again

1. `screenshot`: see the current state.
2. Pick a target in **image** pixels and pass it to the mouse tool unchanged (section 2).
3. Act: `click`, `type_text`, `hotkey`, `scroll`, …
4. `wait` briefly if the UI needs time (page loads, animations), then `screenshot` again to confirm the result before the next step.

**Mandatory: confirm every step with a screenshot before continuing.** After each input action (`click`, `type_text`, `press_key`, `hotkey`, `scroll`, `drag`, `mouse_move`, `mouse_down`/`mouse_up`), take a `screenshot` and check that the step actually did what you expected before you start the next one. Never chain input actions blindly: windows move, dialogs pop up, and focus changes.

- Call `screenshot` only **after** the action has returned. Never issue it in parallel with an action call; it may capture the screen before the action happened.
- Check the details, not just "something changed". For typed text, read the field and compare it with what you sent. The first keystrokes after a focus change (e.g. clicking a taskbar icon) can be dropped.
- If the step didn't succeed, fix it (e.g. clear the field and retype), then screenshot again. Only move on once the screenshot confirms the step.

---

## 2. Coordinates: click what you see

`screenshot` defaults to the **primary monitor** (`monitor=1`), downscaled to **1280 px wide** (the `screenshot.max_width` setting). It returns two content items: first a **text** block, then the **image**. The text starts with a plain line that states the sizes and the coordinates the mouse tools expect, then a line about missing images, then JSON:

```text
Screenshot 1280x720 of a 1920x1080 screen (monitor 1, top-left at screen 0,0). Click coordinates (coordinate_space="screenshot"): pixels in this image, x 0..1279, y 0..719.
If no image follows this text, or it was replaced by an error such as "this model does not support image input", ...
{"screen_resolution": "1920x1080", "image_size": "1280x720", "image_width": 1280, "image_height": 720,
 "format": "png", "region": {"left": 0, "top": 0, "width": 1920, "height": 1080},
 "scale": 1.5, "scale_x": 1.5, "scale_y": 1.5, "to_screen": "...",
 "coordinate_space": "screenshot", "valid_range": {"x": [0, 1279], "y": [0, 719]},
 "cursor_screen": [961, 540], "cursor_screenshot": [640, 360], "cursor_normalized": [501, 500]}
```

**Follow the first line.** It names the active coordinate space and the valid range. Mouse tools (`click`, `mouse_move`, `mouse_down`, `drag`, `scroll`) accept four spaces; omit `coordinate_space` to use the configured default:

| `coordinate_space` | Pass | Range |
| --- | --- | --- |
| `"screenshot"` (usual default) | Pixels in the latest screenshot, exactly as you see them. | `0..image_width-1`, `0..image_height-1` |
| `"normalized"` | Position on a 0–1000 grid over the latest screenshot (500,500 = centre), whatever the image size. Natural for models trained on 0–1000 coordinates (Qwen3-VL, GLM-V, Gemini, Gemma). | `0..1000` |
| `"screen"` | Physical screen pixels. Use only when you already have screen pixels, e.g. from `get_cursor_position`. | Anything (clamped to the desktop) |
| `"calibrated"` | Your own convention, exactly as you reported the targets to `calibrate_submit`. | Named in the screenshot's first line |

The server converts screenshot, normalized and calibrated coordinates to real screen pixels using the **most recent** screenshot, so you never scale anything yourself.

**Calibrate when clicks land off-target.** If the cursor keeps ending up somewhere other than where you aimed, or out-of-range errors repeat, measure your convention once instead of guessing:

1. Call `calibrate`. It returns a synthetic image (not the real screen; nothing is clicked) the size of a normal screenshot, with numbered targets.
2. Report the centre of each target **exactly as you would pass it to `click`**, in target order: `calibrate_submit {"points": [[x1, y1], [x2, y2], ...]}`. Point at the targets directly. Don't calculate, convert, or check the numbers against an image size, and use the same convention for x and y. If you have no natural convention, use a 0–1000 grid on both axes (1000 = right or bottom edge, even though the image is wider than tall).
3. On success the result names the convention (`matched`), its `quality` (`good`, or `usable` with a warning) and makes it the default for this session. Calibration is then done: don't call `calibrate` again. Keep giving coordinates the same way and omit `coordinate_space`. Show the user the `persist` values so they can make it permanent.
4. On an error, nothing changed. Read it and resubmit **corrected** points for the same image; never the same points again.
   - "Your x values match … but your y values do not": re-report y on the scale it names.
   - "you reported [y, x]": swap to `[x, y]`.
   - "inconsistent": check the order and the centres. If you estimated pixels, point on a 0–1000 grid instead.
   - "stop calibrating": stop, keep the current settings and tell the user calibration didn't converge.

- **If you cannot see the image** (no image after the text, or an error like "this model does not support image input"), **stop and tell the user.** Don't guess coordinates from `screen_resolution`; the client or model isn't passing images to you.
- **Out-of-range coordinates are rejected, not clamped.** The error states the valid range, says nothing was done, and suggests the space your numbers probably belong to. Re-read the first line of the latest screenshot and retry with the right numbers or `coordinate_space`. If it happens again, calibrate (above).
- **Coordinates always refer to the latest screenshot.** Every `screenshot` call, including small polling shots (e.g. `max_width: 640`), replaces the mapping. Read coordinates from the screenshot you just took, not an older one.
- **Every mouse result includes `cursor`** with `screen`, `screenshot` and `normalized` positions, so you can check where the pointer landed. The latter two are `null` when the cursor is outside the captured area (e.g. on another monitor).
- **Before any screenshot,** `"screenshot"` coordinates are treated as screen pixels, and the result carries a `note` saying so; `"normalized"` and `"calibrated"` coordinates are an error. Take a screenshot first.
- Coordinates are **physical pixels**. The server is DPI aware, so don't apply Windows display scaling (125% / 150%) yourself.
- **`grid`**: pass e.g. `grid: 100` to `screenshot` to overlay labeled lines every 100 units of the active space. The labels are valid coordinates. Use it when you're unsure of a position.
- **`get_cursor_position`** returns `x`/`y` (screen) plus `screen` and, after a screenshot, `screenshot` and `normalized` positions. **`get_screen_size`** returns `resolution` (e.g. `"2560x1440"`) and every monitor's bounds.

**Choosing screenshot options:**

- Keep the default 1280 px width. Some clients silently downscale larger images before the model sees them, which breaks coordinates. Avoid going above about 1500 px.
- Go smaller (e.g. 640–1024) for quick "did it work?" checks, but take a normal screenshot again before clicking anything.
- Use `format: "jpeg"` (with `quality` around 70–80) to shrink payloads further. Use PNG when you need to read small text.
- **`image: false`** returns only the metadata (resolution, cursor position in both spaces), with no image. Use it when you only need to know where the cursor is. It costs almost no context, and it does **not** replace the coordinate reference. The MCP server never re-sends old screenshots, but most clients keep every earlier image in the conversation, so each image you skip saves context for the rest of the session.
- Other monitors: `monitor: 2` (etc.), or `monitor: 0` for the whole virtual desktop. Clicks then map into that monitor automatically.

**Worked example.** The primary monitor is 1920×1080, and the default screenshot is 1280×720. A button appears at image (400, 300), so you call `click {"x": 400, "y": 300}`. The server clicks screen (600, 450), and the result reports `cursor.screenshot ≈ [400, 300]`. If the first line says `coordinate_space="normalized"` instead, the same button is at `click {"x": 313, "y": 417}` (400/1280·1000, 300/720·1000).

**Image budget.** Most clients re-send every earlier image with each request, and some providers reject requests above a fixed number of images (abliteration.ai: 4, giving "This request has more images than the limit of 4"). Even without a hard cap, every image costs context. Spend images only where you need to *see* something:

- **Verify without a picture when you can.** A mouse result already reports where the cursor landed (`cursor` in every space). `screenshot {"image": false}` costs no image and still gives the cursor position and screen size. That is enough to confirm a move, a hover position or a scroll target. Take a real screenshot when the outcome is only visible on screen: a dialog opened, text was typed, a page loaded.
- **Don't take "just in case" screenshots.** Avoid a small polling shot followed by a full one; a 640 px image counts as one image just like a 1280 px one. Batch your observation: read everything you need from one screenshot before acting.
- **If the text says `Image budget: N of M images used`,** the server has a limit set (`screenshot.max_session_images`). Plan the remaining images for the steps that need them.
- **If a screenshot says `Image budget reached`,** no image was attached. Stop taking image screenshots and tell the user: ask them to compact the conversation (`/compact` in opencode) or start a new session. Once they confirm, call `reset_image_budget` and continue. `calibrate` is refused while the budget is used up.
- **For long runs,** keep progress in files (the continuous-computer-use skill keeps it in `tasks.md` / `state.md`) so a fresh session can pick up where the old one stopped, instead of relying on a long conversation full of screenshots.
- If the user reports the provider's image-limit error, point them to the README section [Image limit](../README.md#image-limit-more-images-than-the-limit-of-4). An opencode plugin there trims old images on the client side.

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
| `x=…, y=… is outside the valid range for coordinate_space="…"` | Your x/y don't fit the latest screenshot in the active space; usually screen pixels or a 0–1000 grid passed as image pixels. Nothing was clicked. | Re-read the screenshot's first line, then retry with the space the error suggests or corrected numbers. On a repeat, calibrate |
| `coordinate_space="normalized" needs a screenshot first` (or `"calibrated"`) | No screenshot has been taken yet | Call `screenshot`, then retry |
| `Image budget reached (N of M images …)` (in a screenshot's text, or as a `calibrate` error) | `screenshot.max_session_images` is used up; no image was sent | Tell the user to `/compact` or start a new session; after they confirm, call `reset_image_budget`. Meanwhile use `image: false` and mouse results |
| `Call calibrate first` | `calibrate_submit` without a preceding `calibrate` | Call `calibrate`, then submit the points for that image |
| `The reported points are inconsistent…` / `…you reported [y, x]` / `Your x values match … but your y values do not` | The calibration points don't fit one linear map (misread, wrong order, swapped axes, mixed conventions, or estimated pixels). Nothing changed. | Resubmit corrected points for the same image as the message says: `[x, y]` order, one convention for both axes, or a 0–1000 grid. Never resubmit identical points |
| `… stop calibrating …` | Third failed calibration submission | Stop calibrating and tell the user it didn't converge |

Screenshot, normalized and calibrated coordinates outside the image are **rejected**. Screen coordinates (`coordinate_space: "screen"`) are **clamped** to the desktop instead. If `mouse_move` returns a `to` far from what you asked for, your coordinates are probably in the wrong space.

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
screenshot {}
  -> first line: "Screenshot 1280x720 of a 1920x1080 screen ... pixels in this image, x 0..1279, y 0..719"
  -> button at image (842, 611)
click      {"x": 842, "y": 611}                  -> pass image pixels as-is; the server scales them
screenshot {}                                    -> verify
```

With `coordinate_space="normalized"` active, the same button is `click {"x": 658, "y": 849}` (842/1280·1000, 611/720·1000). To pass screen pixels explicitly: `click {"x": 1263, "y": 917, "coordinate_space": "screen"}`.

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
