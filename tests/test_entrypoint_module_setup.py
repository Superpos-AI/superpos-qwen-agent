"""Smoke tests for entrypoint.sh module_setup wiring.

Regression coverage for the case where ``module_setup`` was invoked
without ``--bin-dir``, leaving bundled-module CLIs (advertised in
``QWEN.md``) unreachable from ``$PATH`` inside the container.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO_ROOT / "entrypoint.sh"
DOCKERFILE = REPO_ROOT / "Dockerfile"

MODULES_BIN_DIR = "/workspace/.qwen/modules-bin"


def _module_setup_block() -> str:
    text = ENTRYPOINT.read_text()
    match = re.search(
        r"python3 -m superpos_agent_core\.module_setup[^\n]*(?:\n\s+[^\n]+)*",
        text,
    )
    assert match, "module_setup invocation not found in entrypoint.sh"
    return match.group(0)


def test_entrypoint_passes_bin_dir_to_module_setup() -> None:
    block = _module_setup_block()
    assert "--bin-dir" in block, (
        "entrypoint.sh must pass --bin-dir to module_setup so bundled "
        "module scripts are symlinked onto PATH at container startup"
    )
    assert MODULES_BIN_DIR in block, (
        f"--bin-dir must point at {MODULES_BIN_DIR} (the directory the "
        "Dockerfile prepends to PATH)"
    )


def test_dockerfile_prepends_modules_bin_to_path() -> None:
    text = DOCKERFILE.read_text()
    assert f'ENV PATH="{MODULES_BIN_DIR}:$PATH"' in text, (
        f"Dockerfile must keep {MODULES_BIN_DIR} on $PATH so symlinks "
        "written there by module_setup --bin-dir are resolvable"
    )


def test_entrypoint_creates_modules_bin_dir() -> None:
    text = ENTRYPOINT.read_text()
    assert f"mkdir -p {MODULES_BIN_DIR}" in text, (
        f"entrypoint.sh must ensure {MODULES_BIN_DIR} exists before "
        "module_setup tries to write symlinks into it"
    )
