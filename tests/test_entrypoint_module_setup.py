"""Integration test for the entrypoint's ``module_setup --bin-dir`` invocation.

The entrypoint.sh runs::

    python3 -m superpos_agent_core.module_setup \\
        --modules-dir /workspace/.qwen/modules \\
        --agents-md /workspace/QWEN.md \\
        --bin-dir /workspace/.qwen/modules-bin

This test exercises the same command against temp dirs and asserts that the
bundled module scripts (``superpos-issues``, etc.) get symlinked into the
bin dir and are executable from PATH.  Without this, a regression in the
``--bin-dir`` wiring (or a missing flag on an older ``superpos-agent-core``
pin) would not be caught by CI — only ``ruff`` and the existing import
smoke test run.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

# Skip the whole module if core isn't importable in this environment.  CI
# installs the package (which pulls in superpos-agent-core via the
# pyproject dependency), so under normal CI conditions this skip does not
# fire — it only protects local runs where the dep is missing.
superpos_agent_core = pytest.importorskip(
    "superpos_agent_core.module_setup",
    reason="superpos-agent-core not installed; entrypoint smoke test cannot run",
)


def test_module_setup_bin_dir_symlinks_bundled_scripts(tmp_path):
    """Running module_setup with --bin-dir produces executable symlinks.

    Mirrors the entrypoint.sh invocation exactly (modulo paths) so a
    regression in the flag name or in how bundled modules are discovered
    would fail this test.
    """
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    agents_md = tmp_path / "QWEN.md"
    agents_md.write_text("# QWEN\n")
    bin_dir = tmp_path / "modules-bin"

    # Use the same invocation form as entrypoint.sh: `python3 -m
    # superpos_agent_core.module_setup ...`.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "superpos_agent_core.module_setup",
            "--modules-dir",
            str(modules_dir),
            "--agents-md",
            str(agents_md),
            "--bin-dir",
            str(bin_dir),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"module_setup exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert bin_dir.is_dir(), f"--bin-dir {bin_dir} was not created"

    # At least one bundled-module symlink must exist.  We don't hardcode
    # the full list because bundled modules can be added/removed over
    # time; we just assert the wiring works end-to-end.
    entries = list(bin_dir.iterdir())
    assert entries, (
        f"--bin-dir is empty; expected bundled module script symlinks. "
        f"stderr from module_setup:\n{result.stderr}"
    )

    # superpos-issues is the canonical bundled module referenced in the
    # PR description, so assert it specifically.
    issues_link = bin_dir / "superpos-issues"
    assert issues_link.exists(), (
        f"expected 'superpos-issues' symlink in {bin_dir}; "
        f"got: {[p.name for p in entries]}"
    )

    # The link target must point at a real, executable file.
    target = issues_link.resolve()
    assert target.is_file(), f"symlink target {target} is not a file"
    assert os.access(target, os.X_OK), f"symlink target {target} is not executable"


def test_module_setup_supports_bin_dir_flag():
    """``module_setup --help`` advertises the ``--bin-dir`` flag.

    Cheap guard against a downgrade of ``superpos-agent-core`` to a
    version that predates the flag — without it the entrypoint's
    ``--bin-dir`` arg would be rejected at container start.
    """
    result = subprocess.run(
        [sys.executable, "-m", "superpos_agent_core.module_setup", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--bin-dir" in result.stdout, (
        f"--bin-dir flag missing from module_setup --help output:\n{result.stdout}"
    )
