"""Focused tests for QwenExecutor.model_info().

Verifies that model_info() reflects the live runtime state and that
mid-session mutations to model/effort are picked up immediately.
"""

from __future__ import annotations

from unittest.mock import patch

from superpos_agent_qwen import QwenConfig, QwenExecutor, QwenRuntimeConfig


def _make_executor(model: str, effort: str) -> QwenExecutor:
    """Build a QwenExecutor with a real runtime config, mocking only I/O."""
    config = QwenConfig(
        qwen_api_key="test-key",
        qwen_model=model,
        qwen_reasoning_effort=effort,
    )
    runtime = QwenRuntimeConfig(model=model, effort=effort, path="/dev/null")

    # Bypass heavy I/O in __init__ (persona injection, dir creation, MCP
    # discovery) — none of that is relevant to model_info().
    with (
        patch.object(QwenExecutor, "_inject_persona_into_qwen_md"),
        patch("superpos_agent_qwen.qwen_executor.SessionStore"),
        patch("superpos_agent_qwen.qwen_executor.discover_modules", return_value=[]),
        patch("superpos_agent_qwen.qwen_executor.collect_mcp_servers", return_value={}),
        patch("pathlib.Path.mkdir"),
    ):
        return QwenExecutor(
            config=config,
            runtime=runtime,
            superpos=None,
            gateway=None,
        )


def test_model_info_returns_initial_values():
    """model_info() returns the model and effort the executor was created with."""
    executor = _make_executor(model="qwen3-coder-plus", effort="medium")
    info = executor.model_info()

    assert info == {"model": "qwen3-coder-plus", "effort": "medium"}


def test_model_info_reflects_runtime_mutation():
    """After mutating the runtime's model and effort, model_info() returns the new values."""
    executor = _make_executor(model="qwen3-coder-plus", effort="medium")

    # Sanity check before mutation
    assert executor.model_info() == {"model": "qwen3-coder-plus", "effort": "medium"}

    # Mutate model and effort on the live runtime object
    executor._runtime.model = "qwen3-coder-flash"
    executor._runtime.effort = "high"

    info = executor.model_info()
    assert info == {"model": "qwen3-coder-flash", "effort": "high"}


def test_model_info_after_set_model_and_set_effort():
    """model_info() picks up changes made through the RuntimeConfig set_* API."""
    executor = _make_executor(model="qwen3-coder-plus", effort="low")

    # Use the public API (set_model/set_effort) — _save() writes to /dev/null.
    executor._runtime.set_model("qwen-max")
    executor._runtime.set_effort("high")

    assert executor.model_info() == {"model": "qwen-max", "effort": "high"}
