"""Regression guard for the ``superpos-agent-core`` version floor.

``entrypoint.sh`` invokes ``python3 -m superpos_agent_core.github_auth setup``.
That module only exists in ``superpos-agent-core`` 0.1.2 and later.  Because
the entrypoint swallows failures with ``|| echo ...`` (intentionally — proxy
auth via ``superpos-github`` still works), a too-low pin would silently drop
static ``GITHUB_TOKEN`` auth with no visible error.

These tests assert the version floor stays ``>= 0.1.2`` in every place the
dependency is declared (``pyproject.toml``, ``requirements.txt`` and
``uv.lock``) so the pins cannot silently regress below the version that
provides ``github_auth``.

Stdlib-only: parses the files with ``tomllib``/``re`` so no extra test deps
are needed.
"""

from __future__ import annotations

import re
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redefine]

REPO_ROOT = Path(__file__).resolve().parent.parent

# The minimum version of superpos-agent-core that ships the ``github_auth``
# module used by entrypoint.sh.
MIN_AGENT_CORE = (0, 1, 2)


def _version_tuple(text: str) -> tuple[int, ...]:
    """Parse a dotted numeric version (e.g. ``0.1.2``) into a tuple of ints."""
    return tuple(int(part) for part in text.split("."))


def test_pyproject_agent_core_floor_is_at_least_0_1_2():
    """``pyproject.toml`` pins superpos-agent-core to ``~=0.1.2`` or higher."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    deps = data["project"]["dependencies"]
    matches = [d for d in deps if d.replace(" ", "").startswith("superpos-agent-core")]
    assert matches, f"superpos-agent-core not found in dependencies: {deps}"

    spec = matches[0]
    m = re.search(r"superpos-agent-core\s*[~>=]+\s*([0-9]+(?:\.[0-9]+)*)", spec)
    assert m, f"could not parse version from dependency spec: {spec!r}"
    assert _version_tuple(m.group(1)) >= MIN_AGENT_CORE, (
        f"pyproject.toml pins superpos-agent-core below {MIN_AGENT_CORE} "
        f"(got {spec!r}); github_auth module would be missing"
    )


def test_requirements_agent_core_floor_is_at_least_0_1_2():
    """``requirements.txt`` pins superpos-agent-core to ``~=0.1.2`` or higher."""
    lines = (REPO_ROOT / "requirements.txt").read_text().splitlines()
    matches = [
        ln.strip()
        for ln in lines
        if ln.strip() and not ln.strip().startswith("#") and ln.replace(" ", "").startswith("superpos-agent-core")
    ]
    assert matches, "superpos-agent-core not found in requirements.txt"

    spec = matches[0]
    m = re.search(r"superpos-agent-core\s*[~>=]+\s*([0-9]+(?:\.[0-9]+)*)", spec)
    assert m, f"could not parse version from requirement: {spec!r}"
    assert _version_tuple(m.group(1)) >= MIN_AGENT_CORE, (
        f"requirements.txt pins superpos-agent-core below {MIN_AGENT_CORE} "
        f"(got {spec!r}); github_auth module would be missing"
    )


def test_uv_lock_resolves_agent_core_to_at_least_0_1_2():
    """``uv.lock`` resolves superpos-agent-core to ``>= 0.1.2``."""
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text())
    pkgs = [p for p in lock.get("package", []) if p.get("name") == "superpos-agent-core"]
    assert pkgs, "superpos-agent-core not found in uv.lock"

    locked = _version_tuple(pkgs[0]["version"])
    assert locked >= MIN_AGENT_CORE, (
        f"uv.lock resolves superpos-agent-core to {pkgs[0]['version']!r}, "
        f"below the required floor {MIN_AGENT_CORE}; github_auth module would be missing"
    )
