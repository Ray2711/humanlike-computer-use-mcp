"""Drive a real model through a private, isolated opencode server against the input-free test MCP server.

Usage (from the repo root):
  venv/Scripts/python.exe integrations/opencode/calibration-test/run.py serve    # start the private server
  venv/Scripts/python.exe integrations/opencode/calibration-test/run.py audit    # tool list, no model call
  venv/Scripts/python.exe integrations/opencode/calibration-test/run.py 1 [--prompt "..."]
  venv/Scripts/python.exe integrations/opencode/calibration-test/run.py stop     # stop the private server

The private server uses its own config directory (OPENCODE_CONFIG_DIR) and port, so the user's opencode
service and global config are untouched. Everything is written to .calib-runs/ (git-ignored).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
RUNS = ROOT / ".calib-runs"
CONFIG_DIR = RUNS / "opencode-config"
WORK = RUNS / "work"
LOG = RUNS / "server.jsonl"
STATE = RUNS / "serve-state.json"
FLAG = RUNS / "audit-flag.txt"
PYTHON = ROOT / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
MODEL = "abliteration-ai/abliterated-model"
SERVER = "humanlike-calib"
PORT = 4917

DEFAULT_PROMPT = (
    "Call the calibrate tool, then call calibrate_submit with the centre of each numbered target exactly as you "
    "would pass it to click. Report the tool's result (matched convention and max_error_px)."
)

PERMISSIONS = [
    {"action": "*", "resource": "*", "effect": "deny"},
    {"action": "execute", "resource": "*", "effect": "allow"},
    {"action": f"{SERVER}*", "resource": "*", "effect": "allow"},
]


def write_config(env: dict[str, str], permissions: list[dict[str, str]]) -> None:
    (CONFIG_DIR / "plugins").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "integrations/opencode/plugins/keep-last-images.ts", CONFIG_DIR / "plugins/keep-last-images.ts")
    shutil.copyfile(HERE / "tool-audit.ts", CONFIG_DIR / "plugins/tool-audit.ts")
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "model": MODEL,
        "update": "disable",
        "share": "disabled",
        "snapshots": False,
        "lsp": False,
        "formatter": False,
        "permissions": permissions,
        "agents": {name: {"permissions": permissions} for name in ("build", "plan", "general", "explore")},
        "providers": {
            "abliteration-ai": {"models": {"abliterated-model": {"capabilities": {"input": ["text", "image"]}}}}
        },
        "mcp": {
            "servers": {
                SERVER: {
                    "type": "local",
                    "command": [str(PYTHON), str(HERE / "safe_server.py")],
                    "environment": {"HUMANLIKE_DEBUG__LOG_PATH": str(LOG), **env},
                }
            }
        },
    }
    (CONFIG_DIR / "opencode.json").write_text(json.dumps(config, indent=2), encoding="utf-8")


def base_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("HUMANLIKE_", "OPENCODE_", "CALIB_"))}
    env.update(
        OPENCODE_CONFIG_DIR=str(CONFIG_DIR),
        OPENCODE_DISABLE_PROJECT_CONFIG="1",
        OPENCODE_DISABLE_AUTOUPDATE="1",
    )
    return env


def load_state() -> dict[str, Any]:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def serve(extra: dict[str, str], permissive: bool) -> int:
    if load_state().get("pid"):
        print("a private server is already recorded; run 'stop' first", file=sys.stderr)
        return 2
    RUNS.mkdir(exist_ok=True)
    WORK.mkdir(exist_ok=True)
    FLAG.write_text("off", encoding="utf-8")
    write_config(extra, [{"action": "*", "resource": "*", "effect": "allow"}] if permissive else PERMISSIONS)
    password = secrets.token_urlsafe(24)
    env = base_env()
    env.update(
        OPENCODE_SERVER_PASSWORD=password,
        CALIB_AUDIT_FLAG=str(FLAG),
        CALIB_AUDIT_OUT=str(RUNS / "audit.json"),
    )
    exe = shutil.which("opencode")
    assert exe is not None, "opencode is not on PATH"
    log = (RUNS / "serve.log").open("a", encoding="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(
        [exe, "serve", "--hostname", "127.0.0.1", "--port", str(PORT), "--log-level", "info"],
        cwd=WORK, env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=flags,
    )
    STATE.write_text(json.dumps({"pid": proc.pid, "password": password, "permissive": permissive}), encoding="utf-8")
    for _ in range(60):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2)
            break
        except OSError as exc:
            if getattr(exc, "code", None):
                break
    print(f"private opencode server pid {proc.pid} on port {PORT}")
    return 0


def stop() -> int:
    state = load_state()
    pid = state.get("pid")
    if not pid:
        print("no private server recorded")
        return 0
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    else:
        os.kill(pid, 15)
    STATE.write_text(json.dumps({}), encoding="utf-8")
    print(f"stopped private server pid {pid}")
    return 0


def read_jsonl(path: Path, offset: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("rb") as fh:
        fh.seek(offset)
        data = fh.read().decode("utf-8")
    return [json.loads(line) for line in data.splitlines() if line.strip()]


def summarize(run: str, records: list[dict[str, Any]]) -> str:
    lines = [f"# Run {run} data", ""]
    targets: list[list[int]] | None = None
    for rec in records:
        event = rec.get("event")
        if event == "calibrate":
            targets = rec.get("targets")
            lines.append(f"- calibrate: image {rec.get('image')}, targets {targets}")
        elif event == "calibrate_submit":
            points = rec.get("points")
            lines.append(f"- calibrate_submit points: {json.dumps(points)}")
            if "error" in rec:
                lines.append(f"  - error: {rec['error']}")
            else:
                lines.append(
                    f"  - matched {rec.get('matched')!r}, space {rec.get('coordinate_space')}, "
                    f"max_error_px {rec.get('max_error_px')}, calibration {rec.get('calibration')}"
                )
            if targets and isinstance(points, list):
                for i, (t, p) in enumerate(zip(targets, points), start=1):
                    try:
                        dx, dy = float(p[0]) - t[0], float(p[1]) - t[1]
                        lines.append(
                            f"  - target {i}: truth {t}, reported {p}, raw delta ({dx:.0f}, {dy:.0f}) = "
                            f"{math.hypot(dx, dy):.0f}px"
                        )
                    except (TypeError, ValueError, IndexError, KeyError):
                        lines.append(f"  - target {i}: truth {t}, reported {p!r}")
        else:
            lines.append(f"- {event}: {json.dumps({k: v for k, v in rec.items() if k not in ('ts', 'event')})[:600]}")
    return "\n".join(lines) + "\n"


def run_prompt(run: str, prompt: str, timeout: int) -> int:
    state = load_state()
    if not state.get("pid"):
        print("start the private server first: run.py serve", file=sys.stderr)
        return 2
    if run != "audit" and state.get("permissive"):
        print("refusing a model run against a permissive (allow-all) server", file=sys.stderr)
        return 2
    transcript = RUNS / f"run-{run}.json"
    if run != "audit" and transcript.exists():
        print(f"{transcript} already exists; pick a new run number", file=sys.stderr)
        return 2
    if run == "audit":
        FLAG.write_text("on", encoding="utf-8")
        prompt = "Reply with OK."
    offset = LOG.stat().st_size if LOG.exists() else 0
    env = base_env()
    env["OPENCODE_SERVER_PASSWORD"] = state["password"]
    exe = shutil.which("opencode")
    assert exe is not None, "opencode is not on PATH"
    cmd = [exe, "run", "--server", f"http://127.0.0.1:{PORT}", "--format", "json", "-m", MODEL,
           "--title", f"calib-test-{run}", prompt]
    started = time.time()
    proc = subprocess.run(cmd, cwd=WORK, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    transcript.write_text(proc.stdout, encoding="utf-8")
    (RUNS / f"run-{run}.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    time.sleep(1.0)
    (RUNS / f"run-{run}.data.md").write_text(summarize(run, read_jsonl(LOG, offset)), encoding="utf-8")
    print(f"exit {proc.returncode} after {time.time() - started:.0f}s; stdout {len(proc.stdout)} chars")
    print(proc.stderr[-1500:])
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", help="serve | stop | audit | <run number>")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--env", action="append", default=[], help="extra server env KEY=VALUE (serve only)")
    parser.add_argument("--permissive", action="store_true", help="serve with allow-all (audit only)")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    if args.command == "serve":
        return serve(dict(item.split("=", 1) for item in args.env), args.permissive)
    if args.command == "stop":
        return stop()
    return run_prompt(args.command, args.prompt, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
