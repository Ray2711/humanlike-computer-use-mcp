# Task queue

<!-- TEMPLATE (tracked in git). Don't edit for a run: copy it to tasks.md (git-ignored) and edit that. -->

The user edits this file and the agent ticks items off. The agent works top to bottom, taking the first unchecked item that is **eligible**: not `@blocked`, and not held back by `@after` or a pending `@wait-until`.

Format: `- [ ] <goal> <tags>`. Tags go at the end of the line, in any order:

| Tag | Meaning |
| --- | --- |
| `@app <name>` | The app the task must happen in. It must also be in `allowed_apps` in state.md. |
| `@confirmed "<exact action>"` | Pre-authorizes one specific irreversible action, e.g. `@confirmed "click Send"`. Without it, the agent asks first. |
| `@after HH:MM` or `@after YYYY-MM-DD HH:MM` | Don't start before this local time. A bare `HH:MM` means the next occurrence of that time after the line was added. |
| `@every 30m` (`m`, `h`, `d`) | Recurring. After finishing, the agent appends a fresh copy tagged `@after <next run time>`. |
| `@wait-until "<visible text or condition>"` | Before acting (or at the step that needs it), wait until this is visible on screen, e.g. `@wait-until "Download complete"`. |
| `@timeout 10m` | Maximum wait for `@wait-until` (default 10m, max 24h). On timeout the agent pauses and asks. |
| `@blocked` | Set by the agent when it escalates. Remove it to let the agent retry. |

Write secrets as `<ask me>`. Never put passwords here. Times are local to the machine running the MCP server.

## Queue

- [ ] Example: In Notepad, type today's date on a new line at the end of the open file and save with Ctrl+S @app Notepad
- [ ] Example: In the browser, open https://example.com and report the page heading in state.md @app Chrome
- [ ] Example: Check the build dashboard tab and note the status in state.md @app Chrome @every 30m @after 09:00
- [ ] Example: When the download bar shows "report.csv" finished, open it @app Chrome @wait-until "report.csv  Done" @timeout 20m

## Done

<!-- The agent moves finished items here as: - [x] <goal> -> <one-line outcome> (<ISO time>) -->
