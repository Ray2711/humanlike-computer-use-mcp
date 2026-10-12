# Agent setup runbook: humanlike-computer-use-mcp

This is a deterministic runbook for an AI agent installing this MCP server on a user's machine and registering it with an MCP client. Run the steps in order. Do not skip the verification steps.

> **Before you start, tell the user:** this server moves their **real mouse** and types on their **real keyboard** when a client calls its tools. Setup itself sends no input; nothing in steps 1–6 moves the mouse or presses keys.

---

## 0. Ground rules for the agent

1. Use the project's own venv for every Python and pip command (`venv\Scripts\python.exe` on Windows, `venv/bin/python` elsewhere).
2. **PowerShell:** chain commands with `;`. Never use `&&` (Windows PowerShell 5 rejects it).
3. `pip install` needs internet access. **Ask the user before running it.**
4. **Never run anything that clicks, types, drags, scrolls, or moves the mouse without explicit user consent.** That includes:
   - the live tests (`HUMANLIKE_LIVE_TESTS=1`);
   - `mouse_move`, `click`, `drag`, `scroll`, `type_text`, `press_key`, `hotkey`, `mouse_down` and `mouse_up` calls.
5. When editing an MCP client config, **merge** the new entry into it. Never overwrite the file or remove other servers.
6. Never commit user config files (`.cursor/mcp.json`, `humanlike.toml`, Claude config files) to git. They can contain tokens for other servers. Don't print their secrets in chat.
7. Don't delete files without permission.

---

## 1. Prerequisites check

### Python

Python **3.10 or newer** is required. The server was developed on 3.14.

```powershell
python --version          # Windows (or: py -3 --version)
```
```bash
python3 --version         # macOS / Linux
```

If Python is older than 3.10 or missing, stop and ask the user to install a newer Python. Do not install it yourself without permission.

### Operating system notes

| OS | Status | What to check / tell the user |
| --- | --- | --- |
| **Windows 10/11** | Primary target; fully tested | Nothing extra. The server makes itself per-monitor DPI aware at startup. Input cannot reach windows running **as Administrator** unless the MCP client also runs elevated (Windows UIPI). |
| **macOS** | Code paths exist; untested here | macOS blocks synthetic input and screen capture until the user grants permissions to the app that **launches** the server (Cursor, Claude, Terminal, …):<br>• **Privacy & Security → Accessibility**, so pynput and pyautogui can control the mouse and keyboard.<br>• **Privacy & Security → Screen Recording**, so `mss` can capture real window contents (otherwise you get a blank or desktop-only image).<br>The app must be restarted after granting them. `pip` installs the required `pyobjc` packages automatically as pyautogui dependencies. The `win` key name maps to ⌘ Command. |
| **Linux (X11)** | Code paths exist; untested here | It needs an X11 session with `DISPLAY` set. pyautogui and pynput use Xlib, and `mss` uses X11 capture. If pyautogui complains, the distro may need `python3-tk` / `python3-dev`. |
| **Linux (Wayland)** | Generally **not supported** | Wayland compositors block synthetic input and screen capture from X11 clients, so moves, keys and screenshots may silently do nothing or return black images. Recommend logging into an "X11 / Xorg" session. |

Detecting the session type on Linux: `echo $XDG_SESSION_TYPE` prints `x11` or `wayland`.

---

## 2. Get the repository

If the repo is not already on disk, ask the user where it is or where to clone it. Then change into the repo root, the directory that contains `pyproject.toml`. All paths below are relative to it.

Record the **absolute repo path**, called `<REPO>` from here on:

```powershell
(Resolve-Path .).Path                     # Windows
```
```bash
pwd -P                                    # macOS / Linux
```

---

## 3. Create the venv and install

**Windows (PowerShell):**

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -e .
```

**macOS / Linux:**

```bash
python3 -m venv venv
venv/bin/python -m pip install -e .
```

- Add `".[dev]"` instead of `.` if you also want to run the test suite (it adds `pytest`).
- **If pip fails with `Permission denied` / `[Errno 13]` in its cache directory** (e.g. `...\AppData\Local\pip\cache\wheels\...`), retry with:
  ```powershell
  venv\Scripts\python.exe -m pip install --no-cache-dir -e .
  ```
- Do not run wheel or sdist builds. The editable install is all that's needed.

---

## 4. Verify the install (sends no input)

This imports the server and lists its tools. It does **not** import pyautogui or pynput and does not touch the mouse, keyboard or screen.

Windows:

```powershell
venv\Scripts\python.exe -c "import asyncio; from humanlike_mcp.server import mcp; print(sorted(t.name for t in asyncio.run(mcp.list_tools())))"
```

macOS / Linux:

```bash
venv/bin/python -c "import asyncio; from humanlike_mcp.server import mcp; print(sorted(t.name for t in asyncio.run(mcp.list_tools())))"
```

Expected output, 18 tools:

```
['click', 'drag', 'get_config', 'get_cursor_position', 'get_screen_size', 'hotkey', 'list_presets', 'mouse_down', 'mouse_move', 'mouse_up', 'press_key', 'reset_config', 'screenshot', 'scroll', 'set_config', 'type_text', 'use_preset', 'wait']
```

Also validate the configuration that the server will see. This step reads `HUMANLIKE_*` environment variables and `./humanlike.toml` / `HUMANLIKE_CONFIG`:

```powershell
venv\Scripts\python.exe -c "from humanlike_mcp.config import ConfigManager; m = ConfigManager(); print('preset:', m.preset, 'file:', m.config_path)"
```

Optionally, if the dev extra is installed, run the offline test suite. It is safe and sends no input:

```powershell
venv\Scripts\python.exe -m pytest -q
```

Expect all tests to pass, with 2 skipped (the live tests). **Do not** set `HUMANLIKE_LIVE_TESTS=1` unless the user explicitly agrees to have their mouse moved.

---

## 5. Resolve the launch command

The client must start the server with the **absolute path of the venv's interpreter**. There are two equivalent launch forms:

| Form | Windows | macOS / Linux |
| --- | --- | --- |
| Module (recommended) | `<REPO>\venv\Scripts\python.exe` + args `["-m", "humanlike_mcp.server"]` | `<REPO>/venv/bin/python` + args `["-m", "humanlike_mcp.server"]` |
| Console script | `<REPO>\venv\Scripts\humanlike-computer-use-mcp.exe`, no args | `<REPO>/venv/bin/humanlike-computer-use-mcp`, no args |

Print the exact path to use:

```powershell
(Resolve-Path venv\Scripts\python.exe).Path
```
```bash
echo "$(pwd -P)/venv/bin/python"
```

In JSON, Windows backslashes must be doubled (`C:\\Users\\...`); forward slashes (`C:/Users/...`) also work.

The examples below use these placeholders. Substitute the real paths:

- Windows: `C:\\Users\\alice\\humanlike-computer-use-mcp\\venv\\Scripts\\python.exe`
- POSIX: `/Users/alice/humanlike-computer-use-mcp/venv/bin/python` (macOS) or `/home/alice/...` (Linux)

The server entry is named **`humanlike-computer-use`** everywhere below.

---

## 6. Register the server with the MCP client

### 6.1 Merge, don't clobber

Most clients use a JSON file with a top-level `"mcpServers"` object. Procedure:

1. If the file exists, **read it** and parse it as JSON.
2. If `mcpServers.humanlike-computer-use` already exists, ask the user before replacing it.
3. Add or update only that key. Keep every other server and every other top-level key unchanged.
4. Write the file back as valid JSON and re-read it to confirm it parses.

A safe merge helper (works on any OS; pass the config path and the interpreter path):

```powershell
venv\Scripts\python.exe -c "import json,sys,pathlib; p=pathlib.Path(sys.argv[1]).expanduser(); p.parent.mkdir(parents=True, exist_ok=True); d=json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() and p.read_text(encoding='utf-8-sig').strip() else {}; d.setdefault('mcpServers', {})['humanlike-computer-use']={'command': sys.argv[2], 'args': ['-m','humanlike_mcp.server'], 'env': {'HUMANLIKE_PRESET': 'natural'}}; p.write_text(json.dumps(d, indent=2)+'\n', encoding='utf-8'); print('updated', p)" "<CONFIG_PATH>" "<ABSOLUTE_VENV_PYTHON>"
```

On macOS/Linux, use `venv/bin/python` in the same way. If the existing file is not valid JSON (some clients allow comments), **stop and edit it by hand** rather than overwrite it.

### 6.2 Cursor

Config files:

| Scope | Path |
| --- | --- |
| Project (this workspace only) | `<project>/.cursor/mcp.json` |
| Global (all workspaces) | Windows `%USERPROFILE%\.cursor\mcp.json`; macOS/Linux `~/.cursor/mcp.json` |

Windows:

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

POSIX:

```json
{
  "mcpServers": {
    "humanlike-computer-use": {
      "command": "/Users/alice/humanlike-computer-use-mcp/venv/bin/python",
      "args": ["-m", "humanlike_mcp.server"],
      "env": {
        "HUMANLIKE_PRESET": "natural"
      }
    }
  }
}
```

Afterwards, the user enables or refreshes the server in **Cursor Settings → MCP**, or reloads the window.

### 6.3 Claude Desktop

Config file locations:

| OS | Path |
| --- | --- |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux | No official Claude Desktop build. Unofficial builds typically use `~/.config/Claude/claude_desktop_config.json`. |

Windows:

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

macOS:

```json
{
  "mcpServers": {
    "humanlike-computer-use": {
      "command": "/Users/alice/humanlike-computer-use-mcp/venv/bin/python",
      "args": ["-m", "humanlike_mcp.server"],
      "env": {
        "HUMANLIKE_PRESET": "natural"
      }
    }
  }
}
```

Claude Desktop must be **fully quit and restarted** to pick up changes. Closing the window is not enough; quit it from the tray or menu bar.

### 6.4 Claude Code

Use the CLI. Everything after `--` is the command and its arguments. `--scope user` makes the server available in all projects; `local` (the default) applies to the current project only, and `project` writes a shareable `.mcp.json`.

Windows (PowerShell):

```powershell
claude mcp add humanlike-computer-use --scope user -e HUMANLIKE_PRESET=natural -- "C:\Users\alice\humanlike-computer-use-mcp\venv\Scripts\python.exe" -m humanlike_mcp.server
```

macOS / Linux:

```bash
claude mcp add humanlike-computer-use --scope user -e HUMANLIKE_PRESET=natural -- /Users/alice/humanlike-computer-use-mcp/venv/bin/python -m humanlike_mcp.server
```

Verify with `claude mcp list` (or `/mcp` inside a Claude Code session). Remove the entry with `claude mcp remove humanlike-computer-use`, but only if the user asks.

### 6.5 OpenAI Codex CLI

Codex reads `~/.codex/config.toml` (on Windows, `%USERPROFILE%\.codex\config.toml`). Append this table; don't rewrite the file:

```toml
[mcp_servers.humanlike-computer-use]
command = "C:\\Users\\alice\\humanlike-computer-use-mcp\\venv\\Scripts\\python.exe"   # POSIX: "/Users/alice/humanlike-computer-use-mcp/venv/bin/python"
args = ["-m", "humanlike_mcp.server"]

[mcp_servers.humanlike-computer-use.env]
HUMANLIKE_PRESET = "natural"
```

### 6.6 Any other stdio MCP client

The server speaks MCP over **stdin/stdout** (stdio transport). Logs go to stderr. Configure the client with:

| Field | Windows | POSIX |
| --- | --- | --- |
| transport | `stdio` | `stdio` |
| command | `C:\Users\alice\humanlike-computer-use-mcp\venv\Scripts\python.exe` | `/home/alice/humanlike-computer-use-mcp/venv/bin/python` |
| args | `-m humanlike_mcp.server` | `-m humanlike_mcp.server` |
| env (optional) | `HUMANLIKE_PRESET=natural`, `HUMANLIKE_CONFIG=<abs path>` | same |

Generic JSON form:

```json
{
  "mcpServers": {
    "humanlike-computer-use": {
      "type": "stdio",
      "command": "/home/alice/humanlike-computer-use-mcp/venv/bin/python",
      "args": ["-m", "humanlike_mcp.server"],
      "env": {}
    }
  }
}
```

The working directory doesn't matter, unless you rely on `./humanlike.toml` (see below).

---

## 7. Optional configuration

All settings have human-like defaults; configuration is optional. The layering order, from lowest to highest priority, is:

**defaults < preset < TOML file < env vars < runtime `set_config` < per-call arguments.**

| Variable | Purpose | Example |
| --- | --- | --- |
| `HUMANLIKE_PRESET` | `natural` (default), `careful`, `fast` or `instant` (no humanization) | `"HUMANLIKE_PRESET": "careful"` |
| `HUMANLIKE_CONFIG` | **Absolute** path to a TOML config file. If the path doesn't exist, the server refuses to start. | `"HUMANLIKE_CONFIG": "C:\\Users\\alice\\humanlike.toml"` |
| `HUMANLIKE_<SECTION>__<KEY>` | Any single setting (note the **double underscore**) | `"HUMANLIKE_TYPING__WPM": "55"`, `"HUMANLIKE_CLICK__HOLD_MS": "70,120"` |

Using a TOML file:

```powershell
Copy-Item humanlike.example.toml humanlike.toml
```
```bash
cp humanlike.example.toml humanlike.toml
```

Then keep only the lines the user wants to change, and set `HUMANLIKE_CONFIG` to the file's absolute path in the client's `env` block. MCP clients launch the server from a working directory you can't predict, so don't rely on the `./humanlike.toml` auto-detection.

Every value is validated. A bad key or value stops the server at startup with a message on stderr naming the problem. Re-run the `ConfigManager()` check from step 4 after any change.

---

## 8. Post-setup verification inside the client

After the client has (re)loaded the server:

1. **Tools appear.** The client lists 18 tools from `humanlike-computer-use`. Cursor shows a green status in Settings → MCP; Claude Code's `/mcp` lists the server as connected.
2. **Call `get_config`.** It returns `preset`, `config_file` and `settings`, and each setting has a `value` and a `source`. Check that the preset, and any env or file values, show the expected source (`env`, `file:<path>`, `preset:<name>`).
3. **Call `get_screen_size`.** It returns `width`/`height` of the primary monitor in physical pixels (e.g. 3840×2160 on a 4K screen with 150% scaling), plus `virtual_desktop` and `monitors`.
4. **Call `screenshot` with `{"max_width": 1024}`.** It returns an image and JSON with `image_width`, `scale` and `region`. On macOS, a black or wallpaper-only image means Screen Recording permission is missing.

All three calls only read state. **Stop here.** Do not test `mouse_move`, `click`, `type_text`, etc. unless the user explicitly asks; if they do, warn them first and use a harmless target.

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `pip` `[Errno 13] Permission denied` in the pip cache | Locked or foreign-owned pip cache | Re-run with `--no-cache-dir` |
| `No module named humanlike_mcp` when the client starts the server | Client uses a different Python | Point `command` at the **absolute venv interpreter**; re-run the step 4 check with that exact path |
| Server shows as failed or red in the client; stderr says `configuration error: ...` | Bad `HUMANLIKE_*` variable or TOML value, unknown key, missing `HUMANLIKE_CONFIG` file | Fix the named key or value (remember `__`); use an absolute config path |
| `ImportError` for `mcp.server.mcpserver` | `mcp` 1.x installed | `pip install -e .` again (requires `mcp>=2`) |
| JSON parse error after editing a client config | Unescaped Windows backslashes or a trailing comma | Use `\\` or `/` in paths; validate with `python -c "import json;json.load(open(r'<path>'))"` |
| Tools don't appear in Claude Desktop | App not fully restarted | Quit from the tray or menu bar and relaunch |
| Tool error "Fail-safe triggered…" | Cursor is in a corner of the primary screen | Move the cursor away from the corner; then retry |
| Clicks land in the wrong place | Image coordinates used without scaling | Use `screen = region.left + image_x * scale` (see [agent-usage.md](agent-usage.md)) |
| Nothing happens in an Administrator window (Windows) | UIPI blocks input from non-elevated processes | Run the client elevated, or avoid elevated windows |
| macOS: input ignored, or blank screenshots | Missing Accessibility or Screen Recording permission for the launching app | Grant both in System Settings → Privacy & Security, then restart the app |
| Linux: black screenshots, no input | Wayland session | Use an X11/Xorg session |
| Wrong characters typed | Active keyboard layout differs; target app ignores Unicode injection | Switch layout, or type ASCII; see the README Limitations |
| "Error executing tool …" with no detail | Unexpected crash | Read the server's stderr log (Cursor: Output panel → MCP) |

---

## 10. Safety rules (for the agent, always)

- **Ask before acting.** Get explicit user consent before any tool call or script that clicks, types, drags, scrolls, presses keys or moves the mouse, including the live tests. Screenshots and `get_*` calls are read-only.
- **Explain the fail-safe to the user:** moving the mouse into any **corner of the primary monitor** immediately aborts every mouse action and stops `type_text` between keystrokes. Tell them this before the first real input action. Do not disable it (`safety.failsafe=false`) unless the user insists.
- **Don't type secrets you weren't given.** Never read passwords or tokens from files to type them. If the user provides one, pass `typo_rate: 0` and avoid echoing it back.
- **Never commit user config.** Don't add `.cursor/mcp.json`, `humanlike.toml`, `claude_desktop_config.json` or `.codex/config.toml` to git; other servers' entries may hold API keys. Suggest adding `humanlike.toml` to `.gitignore` if the user creates one in a repo.
- **Merge, never clobber** client configs (section 6.1), and don't remove other servers.
- Don't delete files, including ones you created, without permission.
