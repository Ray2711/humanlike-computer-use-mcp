# AGENTS.md

Instructions for AI coding agents (Cursor, Claude Code, Codex, …) working in this repo.

## What this is

A local **stdio MCP server**, written in Python, that drives the **real** mouse and keyboard with human-like timing. It also provides screenshots, plus tools to read and change its own configuration.

- Framework: `mcp>=2` (`mcp.server.mcpserver.MCPServer`).
- Mouse paths: `windmouse` (its `wind_mouse` generator) replayed through `pyautogui`.
- Keyboard and scroll: `pynput`.
- Screenshots: `mss` + Pillow.
- Main target: Windows. macOS and Linux (X11) are untested.

To install the server for a user, follow **[docs/agent-setup.md](docs/agent-setup.md)** step by step.
To use its tools well, read **[docs/agent-usage.md](docs/agent-usage.md)**.

**When driving the desktop through this server, always confirm each step with a `screenshot` before continuing to the next one.** Take it after the input action has returned (never in parallel with it), check that the step really succeeded (e.g. the typed text is complete and correct), and fix and re-check before moving on.

For continuous or scheduled ("24/7") computer use, use the project skill **[.cursor/skills/continuous-computer-use](.cursor/skills/continuous-computer-use/SKILL.md)**. Invoke it with `/continuous-computer-use`, or `/loop 5m /continuous-computer-use`. Its state lives in that folder's `tasks.md` and `state.md`. These are git-ignored working copies; if either is missing, copy it from `tasks.template.md` / `state.template.md` (`Copy-Item <dir>/tasks.template.md <dir>/tasks.md` in PowerShell, `cp` on POSIX) and never overwrite an existing copy. Users edit `tasks.md`, not the template; only change a template to change the defaults for everyone.

## Layout

```
src/humanlike_mcp/
  server.py     MCPServer app, 18 tools, main() entry point (python -m humanlike_mcp.server)
  config.py     frozen-dataclass settings, presets, layered loading, validation
  humanize.py   pure timing/randomness models (no side effects; unit-tested)
  mouse.py      Mouse + PyAutoGuiBackend (injectable backend)
  keyboard.py   Keyboard + PynputBackend (injectable backend), key-name normalization
  screen.py     DPI awareness, mss capture, monitor geometry
  errors.py     FailSafeTriggered
tests/          pytest suite; conftest.py has recording fake backends
scripts/realism_report.py   simulated timing report (sends no input)
humanlike.example.toml      every setting with its default
```

## Setup

Always use the project venv. Never use the system Python for project commands.

Windows (PowerShell; chain commands with `;`, never `&&`):

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -e ".[dev]"
```

macOS/Linux:

```bash
python3 -m venv venv
venv/bin/python -m pip install -e ".[dev]"
```

If pip fails with `Permission denied` in its cache directory, add `--no-cache-dir`. Installing needs internet access, so get the user's permission first.

## Tests

- **Default (safe, offline, sends no input):** `venv\Scripts\python.exe -m pytest -q`. Expect all tests to pass, with 2 skipped. It takes about 7 s and includes a real stdio MCP session plus real screenshots. It never moves the mouse or types.
- **Live tests (`tests/test_live.py`)** move the real mouse cursor. They are skipped unless `HUMANLIKE_LIVE_TESTS=1` is set. **Never run them without the user's explicit consent.** The user may be working on the machine.
- Never write new tests that send real input. Use the fakes in `tests/conftest.py` (`FakeMouseBackend`, `FakeKeyboardBackend`, `Recorder`), and inject them with `Mouse(backend, rng=..., sleep=rec.sleep)`, `Keyboard(...)` and `server.set_runtime(...)`.
- Seed randomness with `random.Random(seed)`. The windmouse path generator uses NumPy's global random generator, so call `numpy.random.seed(...)` as well.

## Conventions

- Python ≥ 3.10, fully typed, `from __future__ import annotations`. Keep comments minimal: only for constraints the code can't show.
- Keep `humanize.py` pure. Real input goes only through the backend classes in `mouse.py` and `keyboard.py`.
- Every setting lives in a `config.py` dataclass, with bounds given through `_opt(default, lo, hi)`. When you add one, also:
  - add it to `humanlike.example.toml` (a test checks that every key appears there);
  - add it to the README settings table;
  - add it to the presets if relevant.
- Raise `ValueError`, `ConfigError` or `FailSafeTriggered` for expected failures. Tool bodies wrap them as `ToolError`, so the model sees the message.
- Release every pressed key and button in `finally` blocks.
- `pyautogui` must not be imported before `screen.enable_dpi_awareness()` runs. Backends import it lazily.
- Don't delete files without the user's permission. Don't run long builds; `pip install -e .` is the only install step.
- After editing, run the offline tests.
