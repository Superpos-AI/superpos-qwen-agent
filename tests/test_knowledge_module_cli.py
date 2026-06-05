"""Guard the workspace superpos-knowledge CLI against read-only drift.

The Dockerfile symlinks ``workspace/.qwen/modules/superpos-knowledge/scripts/
superpos-knowledge`` onto PATH, where it *shadows* the ``superpos-knowledge``
CLI bundled inside ``superpos-agent-core``.  The bundled CLI supports bounded
writes (``create`` / ``update``); if this workspace copy only implements the
read-only subcommands, any ``superpos-knowledge create`` / ``update`` call
(e.g. from a ``knowledge_fillin`` task) fails with an argparse
``invalid choice`` error.

These tests assert the workspace copy keeps the write subcommands so it stays
in sync with the bundled CLI it shadows.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (
    REPO_ROOT
    / "workspace"
    / ".qwen"
    / "modules"
    / "superpos-knowledge"
    / "scripts"
    / "superpos-knowledge"
)

# The script imports superpos_agent_core at module load; skip if it's absent
# (CI installs it via the pyproject dependency, so the skip won't fire there).
pytest.importorskip(
    "superpos_agent_core",
    reason="superpos-agent-core not installed; knowledge CLI cannot be imported",
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def test_script_exists():
    assert SCRIPT.is_file(), f"workspace knowledge CLI missing at {SCRIPT}"


@pytest.mark.parametrize("subcommand", ["create", "update"])
def test_write_subcommands_present(subcommand):
    """``create`` / ``update`` must parse, not error as an invalid choice.

    ``<subcommand> --help`` exits 0 once the subparser exists; a read-only
    copy would exit 2 with 'invalid choice'.
    """
    result = _run(subcommand, "--help")
    assert result.returncode == 0, (
        f"'{subcommand} --help' exited {result.returncode}; the workspace "
        f"copy is missing the '{subcommand}' subcommand and would shadow the "
        f"bundled CLI's write support.\nstderr:\n{result.stderr}"
    )
    assert "invalid choice" not in result.stderr


def test_all_bundled_subcommands_present():
    """Top-level --help advertises both read and write subcommands."""
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    for cmd in ("search", "get", "list", "graph", "topics", "decisions",
                "create", "update"):
        assert cmd in result.stdout, (
            f"subcommand '{cmd}' missing from --help; workspace CLI has "
            f"drifted from the bundled superpos-agent-core CLI.\n{result.stdout}"
        )
