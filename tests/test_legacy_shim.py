"""Tests for the legacy ``slim_agent_qwen`` compatibility shim.

The package was renamed to ``superpos_agent_qwen``.  A shim under the old
name is kept around so existing deployments don't break.  These tests pin
that behaviour:

* ``import slim_agent_qwen`` succeeds and emits a ``DeprecationWarning``.
* Public attributes of the new package are reachable via the old name.
* ``python -m slim_agent_qwen`` resolves to the new package's entrypoint.
"""

from __future__ import annotations

import importlib
import runpy
import subprocess
import sys
import warnings


def test_legacy_import_works_and_warns():
    """``import slim_agent_qwen`` succeeds and emits a DeprecationWarning."""
    # Make sure we're getting a fresh import so the warning fires.
    for name in list(sys.modules):
        if name == "slim_agent_qwen" or name.startswith("slim_agent_qwen."):
            del sys.modules[name]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        legacy = importlib.import_module("slim_agent_qwen")

    assert legacy is not None
    assert any(
        issubclass(w.category, DeprecationWarning) and "slim_agent_qwen" in str(w.message)
        for w in caught
    ), f"expected a DeprecationWarning about slim_agent_qwen, got: {[str(w.message) for w in caught]}"


def test_legacy_exposes_new_package_attributes():
    """Public symbols re-exported by the new package are reachable via the shim."""
    import superpos_agent_qwen as new_pkg

    # Drop any cached shim so it picks up the current implementation.
    sys.modules.pop("slim_agent_qwen", None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        legacy = importlib.import_module("slim_agent_qwen")

    for attr in ("QwenConfig", "QwenExecutor", "QwenRuntimeConfig"):
        assert getattr(legacy, attr) is getattr(new_pkg, attr), (
            f"{attr} on slim_agent_qwen should be the same object as on superpos_agent_qwen"
        )


def test_legacy_main_module_forwards_to_new_entrypoint():
    """``python -m slim_agent_qwen`` loads the new package's ``cli`` callable."""
    # Use runpy with a sentinel run_name so the module's ``if __name__ ==
    # "__main__"`` block does *not* execute (which would try to start the
    # agent and hit the network).  This still proves the module is loadable
    # and pulls ``cli`` in from the new package.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        ns = runpy.run_module("slim_agent_qwen.__main__", run_name="__not_main__")

    from superpos_agent_qwen.__main__ import cli as new_cli

    assert ns["cli"] is new_cli


def test_legacy_main_subprocess_emits_deprecation_warning():
    """Invoking ``python -m slim_agent_qwen`` in a subprocess emits the warning.

    We force the script to exit before actually running the agent by setting
    ``SUPERPOS_AGENT_QWEN_TEST_EXIT=1`` semantics through ``-c`` — we don't
    want to depend on env wiring, so instead we just import the shim and
    check that the DeprecationWarning made it to stderr.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-W",
            "always::DeprecationWarning",
            "-c",
            "import slim_agent_qwen, slim_agent_qwen.__main__",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"importing the shim failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "slim_agent_qwen" in proc.stderr
    assert "DeprecationWarning" in proc.stderr
