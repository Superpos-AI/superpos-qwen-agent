"""Qwen runtime config: registers known models for /model list."""

from __future__ import annotations

from slim_agent_core import RuntimeConfig


class QwenRuntimeConfig(RuntimeConfig):
    """Runtime knobs specialized for Qwen models served via DashScope."""

    KNOWN_MODELS: tuple[str, ...] = (
        "qwen3-coder-plus",
        "qwen3-coder-flash",
        "qwen3-max",
        "qwen3-plus",
        "qwen3-turbo",
        "qwen-max",
        "qwen-plus",
        "qwen-turbo",
        "qwen-coder-plus",
        "qwen-coder-turbo",
    )

    EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high")
