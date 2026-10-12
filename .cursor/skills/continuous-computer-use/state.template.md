# Continuous computer-use state

<!-- TEMPLATE (tracked in git). Don't edit for a run: copy it to state.md (git-ignored) and use that. -->

The agent reads this file at the start of every tick and updates it after every action. Never store secrets here; write `<secret>` instead.

## Consent (filled in by the agent from the user's answers; the agent does nothing until consent is yes)

```yaml
consent: no                     # yes only after the user explicitly agrees to unattended input
consent_given_at:               # ISO time
allowed_apps: []                # e.g. [Notepad, Chrome (example.com only), Excel]
preset: natural                 # natural | careful | fast | instant
user_notes:                     # e.g. "never touch Outlook", "work only 09:00-18:00"
```

## Run settings and budgets

```yaml
status: paused                  # running | paused   (paused = take no screen actions)
blocked_reason:                 # failsafe | stop_file | budget | session_time | stuck | confirm | error | question | lock_screen | wait_timeout
question_for_user:
loop_handle:                    # shell PID or timer name of the /loop schedule
loop_interval: 5m               # base interval of a fixed /loop
idle_backoff_step: 0            # self-paced idle delay = min(1m * 2^step, idle_backoff_cap)
idle_backoff_cap: 30m
max_actions_per_tick: 20
max_actions_per_session: 300    # waits don't count as actions
max_session_minutes: 240        # wall clock since session_started_at, waits included; then pause and ask
max_retries_per_step: 3
stuck_threshold: 3              # same screen signature after this many actions -> escalate
screenshot_max_width: 1280
poll_max_width: 640             # smaller screenshots while polling during waits
image_limited: no               # yes if the provider caps images per request (abliteration.ai: 4); see SKILL.md "Image budget"
max_wait_chunk_s: 30            # longest single `wait` call (30-60); check STOP between chunks
max_wait_per_tick_s: 120        # longer waits end the tick and resume on a later tick
```

## Waiting

```yaml
waiting_for:                    # null, or "<visible text/condition>" being waited on
wait_kind:                      # settle | until | after | idle
wait_task:                      # the task line the wait belongs to
wait_started_at:                # ISO time
wait_deadline:                  # ISO time; reaching it -> escalate (wait_timeout)
next_check_at:                  # ISO time of the next poll; ticks before this exit immediately
last_poll_at:
polls: 0
```

## Progress

```yaml
session_started_at:
actions_this_session: 0
current_task:                   # copied from tasks.md
current_step:                   # the next concrete sub-goal, in plain words
retries_this_step: 0
last_signature:                 # "<app - window title> | <key visible text>"
same_signature_count: 0
last_screenshot_summary:        # 1-2 lines describing what was on screen
next_action:                    # what the next tick should try first
```

## History summary

<!-- One line per finished or abandoned task, written when the journal is compacted. -->

## Journal

<!-- - <ISO time> #<n> <task>/<step> | <action> | <ok|mismatch|error> | sig: <signature> -->
