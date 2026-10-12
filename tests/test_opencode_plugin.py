from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_FILE = ROOT / "integrations" / "opencode" / "keep-last-images.test.mjs"


def _node_strips_types(node: str) -> bool:
    out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30).stdout
    match = re.match(r"v(\d+)\.(\d+)", out.strip())
    return bool(match) and (int(match[1]), int(match[2])) >= (22, 18)


def test_keep_last_images_plugin_under_node() -> None:
    node = shutil.which("node")
    if node is None or not _node_strips_types(node):
        pytest.skip("Node >= 22.18 (built-in TypeScript type stripping) is not installed")
    result = subprocess.run(
        [node, "--test", str(TEST_FILE)], capture_output=True, text=True, timeout=120, cwd=ROOT
    )
    assert result.returncode == 0, result.stdout + result.stderr
