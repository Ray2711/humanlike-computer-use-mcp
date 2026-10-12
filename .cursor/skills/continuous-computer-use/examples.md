# Continuous computer use: example sessions

Tool calls are shown as `tool {json args}`, with results abbreviated after `->`.

## 1. First run: consent, then a scheduled loop

User: `/loop 5m /continuous-computer-use`

```text
state.md and tasks.md missing (fresh clone) ->
  Copy-Item .cursor/skills/continuous-computer-use/state.template.md .cursor/skills/continuous-computer-use/state.md
  Copy-Item .cursor/skills/continuous-computer-use/tasks.template.md .cursor/skills/continuous-computer-use/tasks.md
  (POSIX: cp ... the same two paths); strip the TEMPLATE comment line; ask the user to add tasks to tasks.md
read state.md -> consent: no
Agent asks: unattended control OK? allowed apps? preset? limits? and explains the corner fail-safe and the STOP file.
User: "Yes. Notepad and Chrome only. careful preset. Max 100 actions."
edit state.md -> consent: yes, consent_given_at: 2026-10-12T17:40, allowed_apps: [Notepad, Chrome],
                 preset: careful, max_actions_per_session: 100, status: running
get_config                       -> preset: natural
use_preset {"name": "careful"}   -> preset: careful
arm the loop: PowerShell sleeper every 300 s, sentinel AGENT_LOOP_TICK_ccu; loop_handle: <pid>
run the first burst now (session 2 below)
```

## 2. One burst: append a line in Notepad

Task: `- [ ] In Notepad, type today's date on a new line at the end of the open file and save with Ctrl+S @app Notepad`

```text
#1 screenshot {"max_width": 1280}
     -> scale 1.5, region (0,0); Notepad "todo.txt" visible, not focused
     sig: "Notepad - todo.txt | 3 lines"
   decide: click inside the Notepad text area (image 640,400 -> screen 960,600)
   click {"x": 960, "y": 600}                      -> ok
   settle: wait {"seconds": 0.5}; screenshot {"max_width": 640}
     -> Notepad focused, caret visible (expected element seen; settled)   journal: #1 ... | click text area | ok
#2 hotkey {"keys": "ctrl+end"}                     -> caret at the end    journal ok
#3 press_key {"key": "enter"}                      -> new empty line      journal ok
#4 type_text {"text": "2026-10-12", "typo_rate": 0}
     verify: last line reads 2026-10-12; title shows "*todo.txt"   journal ok
#5 hotkey {"keys": "ctrl+s"}
     verify: title back to "todo.txt" (no asterisk)                journal ok
tick tasks.md: - [x] ... -> appended 2026-10-12, saved (2026-10-12T17:42)
next task: none -> idle; log "idle" once; status stays running; next wake via backoff
```

## 3. Confirmation gate

Task: `- [ ] Reply "Thanks, received" to the latest email from Alex @app Chrome`

```text
... navigate, open the email, click Reply, type_text {"text": "Thanks, received", "typo_rate": 0} ...
decide: click "Send". This sends a message (irreversible) and the task line has no @confirmed "click Send".
edit state.md -> status: paused, blocked_reason: confirm,
                 question_for_user: "Draft reply to Alex is ready. OK to click Send?"
stop the schedule (kill loop_handle)
tell the user, then wait
User: "send it"
status: running; screenshot (verify the draft is still there) -> click Send -> verify "Message sent" -> mark done; re-arm /loop
```

## 4. Fail-safe during typing

```text
type_text {"text": "Quarterly summary: revenue grew ...", "typo_rate": 0}
  -> ToolError: "Fail-safe triggered: the mouse cursor is in a corner of the primary screen ..."
mouse_up not needed (no button held)
edit state.md -> status: paused, blocked_reason: failsafe,
                 next_action: "text may be partially typed; re-check before continuing"
kill loop_handle; clear it
Agent: "You triggered the fail-safe, so I've stopped and paused the loop. Some of the text may already be typed. Say 'resume' when you want me to continue."
(no retries, no further tool calls)
```

## 5. Stuck, then escalate

```text
#7 click {"x": 1210, "y": 88}  (the "Export" button)  -> verify: same screen, sig unchanged (count 1)
#8 re-locate on a full-res screenshot {"monitor": 1}; click {"x": 1214, "y": 90, "overrides": {"mouse.landing_jitter_px": 0}} -> unchanged (count 2)
#9 recovery: press_key {"key": "esc"}; click again -> unchanged (count 3 = stuck_threshold)
edit state.md -> status: paused, blocked_reason: stuck,
                 question_for_user: "The Export button doesn't respond (greyed out?). Should I skip this task?"
mark the task @blocked; stop the schedule; report the last 3 journal lines
```

## 6. STOP file and idle backoff

```text
tick: STOP file exists -> do nothing on screen; status: paused, blocked_reason: stop_file;
      kill loop_handle; "STOP file found; halted. Remove it and tell me to resume."

(self-paced loop, queue empty)
tick 1: no tasks -> journal "idle"; arm wake in 1m (idle_backoff_step 1)
tick 2: no tasks -> arm wake in 2m      (no journal line, no screenshot)
tick 3: no tasks -> 4m ... then 8m, 16m, then 30m (cap)
        (a task tagged @after 09:00 exists -> the wake is capped so it fires at 09:00)
tick n: user added a task -> idle_backoff_step 0; work; next wake in 30-60 s
```

## 7. Resuming in a fresh chat

User (new chat): `/continuous-computer-use`

```text
read state.md -> status: running, current_task "Export report", current_step "open File menu",
                 loop_handle: 18244 (is the process still alive? no -> clear it, offer to re-arm /loop)
journal has 41 lines -> compact: finished tasks summarized into "History summary", keep the last 10 lines
get_config -> preset matches
screenshot -> matches last_screenshot_summary -> continue with the "open File menu" step
```

## 8. Waiting: settle, pacing, a long download across ticks, and a scheduled task

Preset `natural`, self-paced loop. Queue:

```text
- [ ] Download the monthly report from the portal, then open it @app Chrome @wait-until "report.csv  Done" @timeout 20m
- [ ] Check the build dashboard tab and note the status in state.md @app Chrome @every 30m @after 09:00
```

```text
-- tick 1 (08:41) --
#1 click {"x": 812, "y": 455}  ("Download report" button)
   settle: wait 0.5 -> screenshot {"max_width": 640} -> spinner on the button (not settled)
           wait 1   -> screenshot -> download bar "report.csv  3.1/48 MB" (expected element; settled)
   journal: #1 ... | click Download | ok (settled after 2 polls / 1.5 s)
   the download needs "report.csv  Done" -> chunked in-tick wait, up to max_wait_per_tick_s (120 s):
   wait {"seconds": 30} -> STOP? no; status running; session 41/240 min; poll screenshot -> 19/48 MB
   wait {"seconds": 30} -> ... -> 31/48 MB
   wait {"seconds": 30} -> ... -> 40/48 MB
   wait {"seconds": 30} -> ... -> 44/48 MB        (120 s used; not done)
   persist in state.md:
     waiting_for: "report.csv  Done"   wait_kind: until
     wait_started_at: 08:41:20         wait_deadline: 09:01:20   (@timeout 20m)
     next_check_at: 08:44:30           polls: 5
   the 09:00 task isn't eligible yet -> end tick; arm the wake for next_check_at (~60 s)

-- tick 2 (08:44:30) --
preconditions ok; now >= next_check_at -> one poll: screenshot {"max_width": 640}
   -> "report.csv  Done" visible -> journal "wait done after 6 polls / 3.2 min"; clear ## Waiting
pacing: new state on screen -> wait {"seconds": 0.8}
#2 click {"x": 1650, "y": 1012} ("report.csv" in the download bar)
   settle (app launch, timeout 45 s): wait 2 -> screenshot -> Excel splash; wait 1 -> screenshot -> sheet open;
           wait 2 -> screenshot -> same as previous (settled)
   verify at screenshot_max_width 1280: report.csv is open -> mark the task done
queue: only "@after 09:00" left -> idle; wake capped at 09:00 (15.5 min, not the 1m backoff)

-- tick 3 (09:00) --
@after passed -> task eligible; idle_backoff_step 0
click the dashboard tab -> settle (page navigation, timeout 20 s) -> read the status "passing"
move the item to Done; append "- [ ] Check the build dashboard ... @app Chrome @every 30m @after 09:30"
idle; wake at 09:30

-- variant: the user slams the mouse into a corner during a 30 s chunk --
wait {"seconds": 30} -> ToolError "Fail-safe triggered: ..." after 7.4 s
status: paused, blocked_reason: failsafe; clear ## Waiting; kill loop_handle; tell the user; no retries

-- variant: the download never finishes --
tick at 09:01:30: poll -> "report.csv  Failed - Network error"; now >= wait_deadline
status: paused, blocked_reason: wait_timeout,
question_for_user: "report.csv didn't finish within 20 min (Chrome shows 'Failed - Network error'). Retry, keep waiting, or skip?"
```
