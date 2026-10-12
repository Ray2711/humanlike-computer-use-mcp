---
name: continuous-computer-use
description: >-
  Runs long-running, unattended ("24/7") computer use through the
  humanlike-computer-use MCP server as a recursive observe -> decide -> act ->
  verify loop. It works through a task queue (tasks.md), persists progress to
  state.md so it can resume across loop ticks and new sessions, and enforces
  safety guardrails: fail-safe, STOP file, budgets, allowlist, confirmations.
  Use when the user asks for continuous, recurring, scheduled or 24/7 computer
  use, or invokes /continuous-computer-use, often via
  /loop <interval> /continuous-computer-use.
disable-model-invocation: true
---

# Continuous computer use

Drive the user's real desktop through the `humanlike-computer-use` MCP server in small, verified steps. Persist everything to files in this skill folder so any loop tick or new session can pick up where the last one stopped.

Files, all in this folder (`.cursor/skills/continuous-computer-use/`):

| File | Role |
| --- | --- |
| `tasks.md` | The queue. The user writes it; the agent ticks items off. Git-ignored working copy. |
| `state.md` | Consent, settings, budgets, current step, and the journal. **The source of truth.** Git-ignored working copy. |
| `tasks.template.md`, `state.template.md` | Tracked templates. Never edit them during a run. |
| `STOP` | Kill switch. If this file exists, do nothing. It does not exist by default. |
| [reference.md](reference.md) | Detailed patterns: waiting, stuck detection, error table, journal compaction, scheduling. |
| [examples.md](examples.md) | Example sessions. |

Tool usage basics (coordinate scaling, typos, hotkeys, scroll) are covered in [docs/agent-usage.md](../../../docs/agent-usage.md). Installation is covered in [docs/agent-setup.md](../../../docs/agent-setup.md).

## First run: create the working copies

If `state.md` or `tasks.md` is missing (e.g. after a fresh clone), copy it from its template. Only copy a file that is missing; never overwrite an existing working copy. Run from the repo root:

```powershell
$d = ".cursor/skills/continuous-computer-use"
if (-not (Test-Path "$d/state.md")) { Copy-Item "$d/state.template.md" "$d/state.md" }
if (-not (Test-Path "$d/tasks.md")) { Copy-Item "$d/tasks.template.md" "$d/tasks.md" }
```

```bash
d=.cursor/skills/continuous-computer-use
[ -f "$d/state.md" ] || cp "$d/state.template.md" "$d/state.md"
[ -f "$d/tasks.md" ] || cp "$d/tasks.template.md" "$d/tasks.md"
```

Then remove the `<!-- TEMPLATE ... -->` comment line from the copies, and ask the user to put their tasks in `tasks.md` (not the template).

## Preconditions (check every tick; all must hold)

0. **The working copies exist.** Otherwise create them as above.
1. **The server is registered.** The `humanlike-computer-use` tools are available, and `get_config` succeeds. If not, stop and point the user to `docs/agent-setup.md`.
2. **Consent is recorded.** `state.md` has `consent: yes`, the date of the consent, the `allowed_apps` list and the `preset`. If any of these is missing, **ask the user**. Explain that the agent will move their real mouse and type unattended, how the corner fail-safe and the STOP file work, and which apps it may touch. Record their answers. Never assume consent.
3. **The preset is applied.** If `get_config().preset` differs from `state.md`, call `use_preset {"name": <preset>}`.
4. **The STOP file is absent**, `state.md` has `status: running`, and the budgets are not exhausted.

If a precondition fails, end the tick and change nothing on screen.

## The loop (one iteration = one small action)

```
Iteration:
- [ ] 1. Read state.md and tasks.md; check STOP, status and budgets
- [ ] 2. Observe: screenshot {"max_width": 1280}; note scale and region
- [ ] 3. Decide ONE small action toward the current step
- [ ] 4. Act with one humanlike tool call
- [ ] 5. Verify: settle-wait (poll wait + small screenshot), then compare with the expectation
- [ ] 6. Append a journal line to state.md and update counters
- [ ] 7. Continue / retry / mark done / escalate
```

**1. Read.**
- Load `state.md` (status, current task and step, counters, last screen signature, `## Waiting`) and `tasks.md`.
- If `waiting_for` is set, handle the wait first (see Waiting below).
- If there is no current task, take the first unchecked `- [ ]` item that is eligible: not `@blocked`, and any `@after` time has passed.

**2. Observe.**
- Call `screenshot {"max_width": 1280}`. Map image points to the screen: `x = region.left + ix * scale`, `y = region.top + iy * scale`, then round.
- Write a one-line **screen signature**: the foreground app and window title, plus the key visible text, e.g. `Notepad - todo.txt | caret line 3`.
- **Allowlist check:** if the foreground app is not in `allowed_apps` and the step does not explicitly require switching to an allowed app, do not act. Escalate.

**3. Decide.**
- Pick exactly **one** action: one click, one `type_text`, one hotkey, one scroll, and so on.
- Write down what the screen should look like afterwards.
- **Confirmation gate:** if the action is destructive or irreversible, set `status: paused` and ask the user, unless the task line pre-authorizes that exact action with `@confirmed "..."`. This covers deleting, sending messages or emails, purchases and payments, submitting forms, publishing or posting, closing unsaved work, changing settings or accounts, and installing software.

**4. Act.**
- Use the real tool names: `click`, `type_text`, `press_key`, `hotkey`, `scroll`, `drag`, `mouse_move`, `mouse_down`/`mouse_up`, `wait`.
- **Always pass `typo_rate: 0`** for exact text: passwords, codes, URLs, emails, file paths, code, shell commands and form values.
- Natural typos are fine only for free prose in plain text editors.

**5. Verify.**
- **Wait for the screen to settle** (see Waiting), then compare the result with the expectation from step 3.
- After `mouse_down`, always end with `mouse_up`, even on failure.

**6. Journal.**
- Append to `## Journal` in `state.md`: `- <ISO time> #<n> <task>/<step> | <action> | <result: ok/mismatch/error> | sig: <signature>`.
- Increment `actions_this_session`.
- Update `last_signature`, `same_signature_count` and `retries_this_step`.

**7. Decide what's next.**

| Outcome | Next |
| --- | --- |
| Expectation met, step not finished | Next iteration. |
| Expectation met, task finished | Tick `- [x]` in `tasks.md` and add a one-line outcome; clear `current_task`; next iteration. |
| Mismatch | Retry with a different approach (re-locate the target, scroll, refocus) while `retries_this_step < max_retries_per_step`. Then escalate. |
| Stuck | The signature is unchanged for `stuck_threshold` iterations despite actions. Escalate. |
| Tool error | See the error table in [reference.md](reference.md#error-handling). |

**Escalate** means:
1. Set `status: paused` and write `blocked_reason` plus a concrete question in `state.md`.
2. Mark the task `@blocked` if it can't continue.
3. Tell the user.
4. Take no further screen actions until the user replies and you set `status: running` again.

## Waiting

All waiting uses the server's `wait` tool (0–300 s, ±5% jitter when humanization is on). `wait` watches the corner fail-safe while it sleeps and aborts with `Fail-safe triggered` if the user slams the cursor into a corner. Treat that exactly like any other fail-safe. Details and tables: [reference.md](reference.md#waiting).

1. **Settle after every action.** Poll `wait` then `screenshot {"max_width": 640}` on a backoff of **0.5, 1, 2, 4, 4, … s**. Stop as soon as the expected element appears, or two consecutive screenshots look the same, or the timeout passes. Typical timeouts: UI click 3 s, menu/dialog 5 s, page navigation 20 s, app launch 45 s, file save 15 s, download 2 min in-tick (longer goes to a cross-tick wait). A timeout is a **mismatch**, not a crash.
2. **Human pacing.** Before acting on a newly seen screen, add a short reading pause scaled by preset: `natural` 0.4–1.2 s, `careful` 0.8–2 s, `fast` 0.2–0.5 s, `instant` none. Add 1–3 s (scaled the same way) after a whole new page or dialog. Pacing waits are optional flavour and never exceed 3 s.
3. **Chunk long waits.** No single `wait` call longer than `max_wait_chunk_s` (30 s, at most 60 s). Between chunks, check the STOP file, `status` and `max_session_minutes`, and take a poll screenshot if waiting on a condition. Don't move the mouse while waiting.
4. **Within a tick vs across ticks.** If the remaining wait fits in `max_wait_per_tick_s` (120 s), wait in chunks inside the tick. Otherwise record the wait in `state.md` (`waiting_for`, `wait_kind`, `wait_started_at`, `wait_deadline`, `next_check_at`), end the tick, and let `/loop` wake you. A tick that starts before `next_check_at` ends immediately, with no screenshot and no journal line.
5. **Scheduled and conditional tasks.** `@after` holds a task until a time, `@every` re-queues it with the next `@after`, `@wait-until "<text>"` waits for visible text, and `@timeout` bounds it (default 10m). On timeout: escalate with `blocked_reason: wait_timeout`.

## Mandatory guardrails

- **Fail-safe** (`ToolError` starting `Fail-safe triggered`): the user has deliberately pulled the cursor into a corner.
  - **Stop immediately.** Set `status: paused`, `blocked_reason: failsafe`.
  - **Stop the loop schedule** (see Scheduling below).
  - Ask the user what to do. **Never auto-resume, and never retry the call.**
- **Never** set `safety.failsafe` to false, and never pass it in `overrides`.
- **STOP file:** check for `.cursor/skills/continuous-computer-use/STOP` at the start of every iteration. If it exists, end the tick, stop the schedule and report. Do not delete it; the user removes it to allow resuming.
- **Budgets** (from `state.md`):
  - At most `max_actions_per_tick` actions per tick.
  - At most `max_actions_per_session` actions in total. When reached, pause and ask.
  - At most `max_retries_per_step` retries on a step, then escalate.
  - At most `max_session_minutes` of wall-clock time since `session_started_at`. Waits don't count as actions but **do** count toward this. When reached, pause with `blocked_reason: session_time` and ask.
- **Every wait is bounded.** No wait without a deadline; a deadline that passes escalates to the user (`blocked_reason: wait_timeout`). Never wait silently forever.
- **Secrets:** only type secrets the user gave in this conversation, and only when needed. Never write them to `state.md`, `tasks.md` or the journal; write `<secret>` instead. Never read credentials from files or the screen to reuse elsewhere.
- **Scope:** only touch apps and sites in `allowed_apps`, and only do what the queue asks. Never open new apps or sites beyond what a task needs.
- **Locked screen, UAC prompt, login wall or CAPTCHA:** escalate. Don't try to get past them.
- **Unexpected dialogs** (crash reports, "Save changes?", permission prompts): escalate unless the current task covers them.

## Scheduling, continuity and idling

Run one **burst** of iterations per invocation, up to `max_actions_per_tick`, then end the turn. A schedule re-invokes the skill.

- **Fixed:** `/loop 5m /continuous-computer-use`. This uses the loop skill's monitored-shell mechanism locally, or a subscription timer in cloud agents (which have no local desktop, so use this only where the MCP server runs).
- **Self-paced:** `/loop /continuous-computer-use`. The agent picks the next delay, with idle backoff.
- **One-off:** `/continuous-computer-use` runs a single burst.

Record the loop's PID or name in `state.md` (`loop_handle`), so pausing or stopping can terminate it.

**Idle:** when no eligible tasks remain, do **not** move the mouse, click, or "keep the screen awake".
- Log `idle` once (don't log every idle tick).
- Back off the self-paced delay exponentially: 1m, 2m, 4m, 8m, 16m, then 30m (`idle_backoff_cap`). Never sleep past the earliest `next_check_at` or `@after` time.
- With a fixed `/loop 5m`, idle ticks just exit early; the backoff only applies to self-paced loops.
- Reset `idle_backoff_step` to 0 when `tasks.md` gains a new eligible item.

**Context hygiene:**
- `state.md` is the memory. Don't rely on chat history.
- Keep at most about 30 journal lines. Compact older lines into `## History summary`, with one line per finished task.
- Don't keep old screenshots in mind; only the latest signature matters.
- When a fresh session starts, re-read the files and continue from `current_step`.

## Additional resources

- Detailed patterns, the error table, stuck detection, compaction and the stop procedure: [reference.md](reference.md)
- Example sessions: [examples.md](examples.md)
- Templates: [tasks.template.md](tasks.template.md) and [state.template.md](state.template.md), copied to `tasks.md` and `state.md` on the first run.
