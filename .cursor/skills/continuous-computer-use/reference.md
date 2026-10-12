# Continuous computer use: reference

Detailed patterns for [SKILL.md](SKILL.md). General tool usage (coordinate scaling, presets, typos, hotkeys, scroll) lives in [docs/agent-usage.md](../../../docs/agent-usage.md); this file does not repeat it.

## Contents

- Consent conversation
- Tool cheat sheet (real names and params)
- Error handling
- Stuck detection
- Waiting (settle polling, timeouts, pacing, chunking, cross-tick waits, idle)
- Journal compaction
- Scheduling with /loop
- Stop, pause and resume
- Scheduled, recurring and conditional tasks
- Multi-session continuity checklist

---

## Consent conversation

Ask once, before the first input action. If `state.md` or `tasks.md` doesn't exist yet, first copy it from `state.template.md` / `tasks.template.md` (see "First run" in [SKILL.md](SKILL.md)). Record the answers in `state.md` under `## Consent`.

1. "I'll control your real mouse and keyboard unattended, on a schedule. OK?"
2. "Which apps and sites may I use?" These go into `allowed_apps`.
3. "Which preset: `natural` (default), `careful`, `fast` or `instant`?"
4. "Any limits?" For example working hours, things never to touch, or budget changes.
5. Explain the two ways to stop:
   - **Fail-safe:** move the mouse into any corner of the primary screen. All input aborts at once, and the loop pauses until the user says to resume.
   - **STOP file:** create `.cursor/skills/continuous-computer-use/STOP`, e.g. with `New-Item .cursor/skills/continuous-computer-use/STOP` or `touch .cursor/skills/continuous-computer-use/STOP`. The next tick halts. Deleting the file allows resuming.

Then set `consent: yes`, `consent_given_at`, `status: running` and `session_started_at`.

---

## Tool cheat sheet (server `humanlike-computer-use`)

| Tool | Params (defaults) | Loop usage |
| --- | --- | --- |
| `screenshot` | `monitor=1`, `max_width=1280`, `format="png"`, `quality=80` | Every observe and verify step. Metadata comes first: `screen_resolution`, `image_size`, `scale`, `cursor_screen` and `cursor_screenshot`. It becomes the reference for screenshot-space coordinates. Use `max_width: null` only to read tiny text, then take a 1280 px shot again before clicking. |
| `get_screen_size` | none | Once per session; record `resolution` and the monitor layout in `last_screenshot_summary` if multi-monitor. |
| `get_cursor_position` | none | Rarely; the screenshot metadata and every mouse result already include the cursor in both spaces. |
| `mouse_move` | `x`, `y`, `speed?`, `humanize?`, `coordinate_space="screenshot"`, `overrides?` | Hover menus and tooltips. All mouse tools take latest-screenshot pixels by default. |
| `click` | `x?`, `y?`, `button="left"`, `clicks=1..3`, `speed?`, `humanize?`, `overrides?` | Main pointing action. For tiny targets add `overrides: {"mouse.landing_jitter_px": 0, "mouse.overshoot_enabled": false}`. |
| `mouse_down` / `mouse_up` | `button`, (`x?`, `y?` on down) | Only for multi-step gestures. Always release. |
| `drag` | `start_x`, `start_y`, `end_x`, `end_y`, `button`, `speed?`, `humanize?`, `overrides?` | Sliders, selection, drag-and-drop. |
| `scroll` | `amount` (notches; + = up, or right with `horizontal`), `x?`, `y?`, `horizontal=false` | Pass `x`/`y` over the scrollable pane. At most about 10 notches per iteration, then re-observe. |
| `type_text` | `text`, `wpm?`, `typo_rate?`, `humanize?`, `overrides?` | `typo_rate: 0` for anything exact. Keep each call short (one field or one line). |
| `press_key` | `key`, `presses=1` | `enter`, `tab`, `esc`, arrows, `pagedown`, … |
| `hotkey` | `keys` (`"ctrl+s"` or a list) | Save, copy, switch tabs. `"ctrl++"` means Ctrl and Plus. |
| `wait` | `seconds` (0–300) | Settle polling, pacing and chunked waits. ±5% jitter when humanization is on (exact under `instant`). Polls the corner fail-safe every 0.2 s and aborts with `Fail-safe triggered` if it fires. Returns `{"waited_s": ...}`. |
| `get_config` / `use_preset` / `set_config` / `reset_config` / `list_presets` | — | Precondition checks and preset changes. **Never** set `safety.failsafe` to false. |

---

## Error handling

The server reports expected problems as tool errors with readable text. Match on the **start** of the message.

| Error text starts with | Meaning | Loop response |
| --- | --- | --- |
| `Fail-safe triggered:` | The user pulled the cursor into a corner, during an action **or during a `wait`**. | **Halt.** `status: paused`, `blocked_reason: failsafe`, stop the schedule, clear the `## Waiting` block, tell the user. Never retry and never auto-resume. A partial `type_text` may have been typed; record that. |
| `Unknown setting`, `… is below the minimum`, `… is above the maximum`, `expected a …`, `min … is greater than max`, `… is not one of` | Bad `overrides` or `set_config` value | Fix the argument and retry once. Don't count this as a step retry. |
| `Unknown preset` | Bad preset name in state.md | Escalate with `blocked_reason: question`. |
| `Unknown key name`, `Invalid hotkey`, `Duplicate key in hotkey`, `Key '…' is not supported on this platform` | Bad key input | Fix it using the names in docs/agent-usage.md, then retry once. |
| `Text is N characters; the limit is …` | `type_text` over `safety.max_text_length` | Split the text into chunks (one per iteration). |
| `monitor must be between 0 and N` | Monitor layout changed | Call `get_screen_size`, update the notes, retry. |
| `button must be one of`, `clicks must be 1, 2 or 3`, `presses must be between 1 and 100` | Argument out of range | Fix and retry. |
| An input validation error naming a parameter | Schema violation (e.g. `scroll` amount over 500, `wait` over 300) | Fix and retry. |
| `Error executing tool <name>` | Unexpected server crash | Retry once after `wait 2`. If it repeats, escalate (`blocked_reason: error`) and suggest the user check the MCP server log. |
| Tool missing, or server disconnected | The MCP server is down or was reloaded | Don't act. Escalate and point to docs/agent-setup.md. |

Clamping is silent: off-screen coordinates are clamped, not rejected. If the returned `to` or `position` differs from your target by more than about 5 px, your coordinate mapping is wrong. Re-observe before clicking again.

**Interrupted gestures:** if any error happens between `mouse_down` and `mouse_up`, call `mouse_up {"button": ...}` before anything else, even after a fail-safe. `mouse_up` is always allowed.

---

## Stuck detection

A **screen signature** is a short, stable text: `<app> - <window title> | <1–3 key visible strings>`. Leave out clocks, cursors, spinners and timestamps.

- After each verify step, compare the new signature with `last_signature`.
  - Same signature, and the action was expected to change something: increment `same_signature_count`.
  - Otherwise, reset it to 0.
- Stuck when `same_signature_count >= stuck_threshold` (default 3), **or** when the same action failed `max_retries_per_step` times.
- Before escalating, try one recovery that fits the situation, and count it as an action:
  - `press_key esc` to close a stray menu;
  - click on the target window's title bar to refocus it;
  - scroll the target into view;
  - take a full-resolution screenshot (`monitor: 1`) to re-read small text.
- Still stuck: escalate with `blocked_reason: stuck`, a description of what was expected versus what is seen, and the last 3 journal lines.

**Loops across tasks:** if the journal shows the same task started and abandoned 3 times, mark it `@blocked` and move on.

**Waiting is not stuck.** Polls during a settle or conditional wait don't increment `same_signature_count`; only actions do.

---

## Waiting

Four kinds of waiting, all built on `wait` and recorded with `wait_kind` in `state.md` when they outlive a single call:

| Kind | Purpose | Typical length | Mouse moves? |
| --- | --- | --- | --- |
| `settle` | Let the UI react after an action | 0.5 s – 2 min | No |
| (pacing) | Human-like reading and thinking pause | 0.2–3 s | No |
| `until` / `after` | Conditional or scheduled task (`@wait-until`, `@after`) | minutes to hours | No |
| `idle` | Nothing to do | backoff up to 30 min | No |

**What `wait` guarantees:**
- `seconds` is 0–300. Anything larger is a schema error, so long waits must be split into chunks.
- With humanization on, the real sleep is `seconds × U(0.95, 1.05)`. The result's `waited_s` is the true value; use it for wall-clock bookkeeping.
- When `safety.failsafe` is on (always, in this skill), `wait` checks the corner fail-safe every 0.2 s and stops early with `Fail-safe triggered: …`. A user can therefore halt the agent mid-wait, not only mid-action.
- `wait` doesn't move the mouse or press anything.

### Settle polling (after every action)

```
deadline = now + timeout (table below)
delays   = 0.5, 1, 2, 4, 4, 4 …      (seconds, each capped by the time left)
prev     = none
loop:
  wait {"seconds": delay}
  screenshot {"max_width": poll_max_width}      # 640: cheap, enough to spot change
  if expected element/text is visible        -> settled (success)
  if this screenshot matches prev            -> settled (check the expectation once more
                                                at screenshot_max_width before deciding)
  if now >= deadline                         -> timeout = mismatch (normal retry rules)
  prev = this screenshot
```

- **"Matches"** means the same screen signature *and* no visible progress indicators (spinners, progress bars, "Loading…", greyed-out panes). A page still painting images or a moving progress bar is not settled, even if the signature is unchanged.
- Ignore clocks, blinking carets and cursor position when comparing.
- Use `screenshot_max_width` (1280) or a full-resolution screenshot for the final verify step if you need to read small text. Polls stay small.
- Every screenshot, including a poll, becomes the reference for screenshot-space mouse coordinates. Never click using coordinates read from an earlier, differently sized screenshot. Take a fresh `screenshot_max_width` screenshot before the next action.
- Polls are not actions: they don't increment `actions_this_session` or `same_signature_count`, and they get one journal line in total (e.g. `settled after 3 polls / 3.5 s`), not one per poll.

**Recommended settle timeouts:**

| After | First poll | Timeout | On timeout |
| --- | --- | --- | --- |
| Click, keypress or typing in a local UI | 0.5 s | 3 s | Mismatch; retry or re-locate |
| Opening a menu, dropdown or dialog | 0.5 s | 5 s | Mismatch |
| Page navigation or form submit in a browser | 1 s | 20 s | One reload attempt (F5) counts as a retry; then escalate |
| App launch or window open | 2 s | 45 s | Check the taskbar; escalate if not visible |
| File save, export | 0.5 s | 15 s | Look for a "Save as" or error dialog; escalate |
| File download, upload, install, build | 2 s | 2 min in-tick | Convert to a cross-tick `until` wait with `@timeout` (default 20 min) |

If the task line has `@timeout`, it overrides the table for that task's long waits.

### Human pacing

Short pauses make a session look like a person reading the screen. They are cosmetic, so keep them small and skip them entirely under `instant`:

| Preset | Before acting on a newly observed screen | Extra after a new page or dialog |
| --- | --- | --- |
| `natural` | 0.4–1.2 s | +1–2 s |
| `careful` | 0.8–2 s | +1.5–3 s |
| `fast` | 0.2–0.5 s | +0.5–1 s |
| `instant` | none | none |

- Pick a random value in the range, rounded to 0.1 s, and make one `wait` call; never stack several.
- Fold pacing into the first settle poll when you can (e.g. under `natural` the first poll can be 1 s instead of 0.5 s), rather than adding a separate call.
- Pacing never exceeds 3 s and doesn't count toward `max_actions_*`.

### Chunked long waits (safety between chunks)

Any wait longer than `max_wait_chunk_s` (default 30 s, never more than 60 s) runs as repeated chunks:

```
until the wait condition is met or wait_deadline passes:
  wait {"seconds": min(max_wait_chunk_s, time left)}
     -> "Fail-safe triggered": halt (status paused, failsafe), clear ## Waiting, stop the schedule
  STOP file exists?                          -> halt (stop_file)
  re-read state.md: status still running?    -> otherwise end the tick
  now - session_started_at > max_session_minutes?   -> pause (session_time) and ask
  if waiting on a condition: screenshot {"max_width": poll_max_width}; condition visible? -> done
  update last_poll_at, polls (in memory; write state.md every ~5 chunks or on exit)
```

- Long waits always respect `max_wait_per_tick_s` (120 s). Past that, convert to a cross-tick wait, so that one tick never blocks for many minutes and the user can still talk to the agent.
- Never move the mouse, "jiggle", or press keys to keep the session awake while waiting. If the screen locks during a long wait, escalate with `blocked_reason: lock_screen` at the next poll.

### Cross-tick waits (persisted)

When a wait outlives the tick, write the `## Waiting` block in `state.md`:

```yaml
waiting_for: "report.csv  Done"         # visible text/condition; empty for a pure @after wait
wait_kind: until                        # until | after | idle
wait_task: "When the download bar shows ... @wait-until \"report.csv  Done\" @timeout 20m"
wait_started_at: 2026-10-12T18:02:10
wait_deadline: 2026-10-12T18:22:10      # started + @timeout (default 10m)
next_check_at: 2026-10-12T18:04:10      # next poll
last_poll_at: 2026-10-12T18:02:10
polls: 1
```

Each later tick:

1. Do the usual precondition checks (STOP, status, consent, `max_session_minutes`).
2. If `now < next_check_at`: end the tick at once. No screenshot, no journal line. (This keeps a fixed `/loop 1m` cheap.)
3. Otherwise poll with one screenshot at `poll_max_width`:
   - Condition met: journal `wait done after <n> polls / <minutes>`, clear the block, continue the task in the same tick.
   - Not met and `now >= wait_deadline`: escalate with `blocked_reason: wait_timeout` and a question such as "Download still not finished after 20 min. Keep waiting, retry, or skip?"
   - Not met: set `next_check_at = now + poll interval` and end the tick.
4. **Poll interval** for cross-tick waits: start at 1 min, double per poll, cap at 10 min, and never go past `wait_deadline`. Self-paced loops arm the next wake for exactly `next_check_at`; fixed loops just let early ticks exit.

Other tasks may run while a cross-tick wait is pending if they're eligible and use a **different** app, so they don't disturb the waited-on screen. Before polling, bring the waited-on window back to the front if needed (one click on its title bar or taskbar button; that's an action).

### Idle waiting

- Idle = no eligible task and no pending `## Waiting` block.
- Self-paced delay: `min(1 min × 2^idle_backoff_step, idle_backoff_cap)`, i.e. 1, 2, 4, 8, 16, 30, 30 … min. Increment `idle_backoff_step` each idle tick.
- Cap the delay at the earliest upcoming `@after` or `next_check_at`, so scheduled work starts on time.
- Reset `idle_backoff_step: 0` when a new eligible task appears or an `@after` time arrives.
- Idle ticks take no screenshot and log `idle` only once per idle stretch. Idle time still counts toward `max_session_minutes`. When that limit is reached while idle, pause and say so, rather than idling forever.
- With a fixed `/loop`, the backoff doesn't apply. Each idle tick reads the two files and exits.

---

## Journal compaction

When `## Journal` has more than about 30 lines, or at the start of a new session:

1. For each **finished or blocked** task in the journal, write one line under `## History summary`: `- <ISO date> <task> -> <outcome> (<n> actions)`.
2. Remove those tasks' journal lines. Keep the current task's lines, at most the last 10. (This is editing the file's contents, not deleting files.)
3. Keep `last_screenshot_summary` to 1–2 lines.
4. If `## History summary` grows beyond about 50 lines, merge it into per-day lines.

The goal: a fresh agent can resume by reading only `state.md` and `tasks.md`, in under about 150 lines.

---

## Scheduling with /loop

Each tick runs one burst of iterations (at most `max_actions_per_tick`), then ends the turn. The loop skill re-invokes the prompt.

**Fixed interval:** `/loop 5m /continuous-computer-use`. In a local session the loop skill arms a monitored background shell. In PowerShell:

```powershell
while ($true) { Start-Sleep -Seconds 300; Write-Output 'AGENT_LOOP_TICK_ccu {"prompt":"/continuous-computer-use"}' }
```

- Notify on `^AGENT_LOOP_TICK_ccu`.
- Record the shell PID in `state.md` as `loop_handle`.
- Run one burst immediately after arming.

**Self-paced:** `/loop /continuous-computer-use`. At the end of each tick, arm a one-shot wake whose delay follows the backoff:

```powershell
Start-Sleep -Seconds 120; Write-Output 'AGENT_LOOP_WAKE_ccu {"prompt":"/continuous-computer-use"}'
```

| Situation at the end of a tick | Next delay |
| --- | --- |
| Task in progress, budget left | 30–60 s |
| Cross-tick wait pending (`## Waiting` set) | Exactly until `next_check_at` |
| Queue empty or only future `@after` tasks (idle) | `min(1m × 2^idle_backoff_step, 30m)`, capped at the earliest `@after` / `next_check_at`. Increment `idle_backoff_step`. |
| New eligible task appeared | Reset `idle_backoff_step: 0` |
| `status: paused` | **Arm nothing.** Stop the schedule. |

The one-shot sleeper takes the delay in seconds. Keep it as one `Start-Sleep` (the loop skill's shell does the waiting, not the MCP `wait` tool, so long idle periods cost no tool calls).

**Cloud agents** use the loop skill's subscription timer (`name: loop-continuous-computer-use`). This only makes sense if the MCP server runs on the machine the agent controls. A cloud VM can't drive the user's local desktop.

**Rules:** keep a single loop only; check `loop_handle` and existing terminals before arming. Never run a tick while a previous tick is still acting.

---

## Stop, pause and resume

**Halt the schedule** (on fail-safe, STOP file, exhausted budget or `max_session_minutes`, a wait timeout, a confirmation request, or a user request):

1. Kill the shell PID in `loop_handle`, or unsubscribe the timer, then await the shell task so it doesn't wake you later.
2. Set `status: paused`, `blocked_reason`, `question_for_user`, and clear `loop_handle`.
3. Tell the user in one or two sentences what happened and what you need.

**Resume** only when the user explicitly says so:

- Fail-safe: the user must say "resume". Then take a fresh screenshot before anything else; the screen may have changed.
- STOP file: the user removes the file. The agent never deletes it.
- Budget: the user raises the limit or resets `actions_this_session`.
- Session time: the user raises `max_session_minutes`, or says to start a new session (reset `session_started_at` and `actions_this_session`).
- Wait timeout: follow the user's answer. "Keep waiting" means a new `wait_deadline`; "skip" means `@blocked` or moving the task to Done with the outcome "skipped".

Then set `status: running`, re-arm `/loop` and continue from `current_step`.

---

## Scheduled, recurring and conditional tasks

Tag syntax is defined in [tasks.template.md](tasks.template.md) (copied into `tasks.md`). Times are local to the machine running the MCP server; get "now" from the shell (`Get-Date -Format s`) rather than guessing.

| Tag | Within a tick | Across ticks |
| --- | --- | --- |
| `@after T` | Not eligible while `now < T`. If `T` is within `max_wait_per_tick_s` and nothing else is eligible, chunk-wait for it. | Idle backoff and self-paced wakes are capped at the earliest `T`. Fixed loops pick it up on the first tick after `T`. |
| `@every N` | Run normally. On success, move the item to Done and append a fresh unchecked copy with `@after <start time + N>` (if that's already past, `now + N`). Keep the other tags. | The new copy is an ordinary `@after` task. A missed run (e.g. the PC was asleep) runs once on the next tick; don't try to catch up on several. |
| `@wait-until "X"` | At the point the task needs it: settle-poll for X up to `max_wait_per_tick_s`. | Then persist `wait_kind: until`, `waiting_for: X`, `wait_deadline = wait_started_at + @timeout`, and poll on later ticks (see Cross-tick waits). |
| `@timeout D` | Bounds `@wait-until` (default 10m, max 24h). | Reaching `wait_deadline` escalates with `blocked_reason: wait_timeout`. |

- `@wait-until` without an `@app` still requires the foreground window to be in `allowed_apps` before acting on it. The wait itself needs no permission: it only takes screenshots.
- Match `X` loosely: case-insensitive and ignoring extra spaces, but **not** a synonym. If the text could appear for the wrong reason (e.g. an old "Done" from a previous download), require it in the expected place and note that in `waiting_for`.
- A `@wait-until` on the first line of the task means "before starting". Elsewhere in the goal text ("…then wait until X, then…"), it's a step condition. Both use the same machinery.
- A failed `@every` run doesn't re-queue automatically. It escalates like any other task, and the user decides.

---

## Multi-session continuity checklist

At the start of a fresh chat or after a context reset:

```
- [ ] state.md / tasks.md missing (fresh clone)? Copy them from the *.template.md files; never overwrite existing copies
- [ ] Read state.md and tasks.md fully
- [ ] Check the STOP file; check status, consent and budgets
- [ ] get_config: confirm the preset matches state.md
- [ ] Check loop_handle: is the schedule still alive? Never start a second loop.
- [ ] Check ## Waiting: if set, resume that wait (poll now if next_check_at has passed)
- [ ] screenshot: confirm the screen matches last_screenshot_summary; if not, re-plan current_step before acting
- [ ] Compact the journal if it is over about 30 lines
```
