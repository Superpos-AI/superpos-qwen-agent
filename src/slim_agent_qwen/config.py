"""Qwen-specific config: extends BaseConfig with Qwen API key + base URL + model."""

from __future__ import annotations

import os
from dataclasses import dataclass

from slim_agent_core import BaseConfig


@dataclass
class QwenConfig(BaseConfig):
    """Adds Qwen Code CLI-specific knobs on top of the universal BaseConfig."""

    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen3-coder-plus"
    qwen_reasoning_effort: str = "medium"  # low|medium|high

    def __post_init__(self) -> None:
        if not self.executor_kind or self.executor_kind == "generic":
            self.executor_kind = "qwen"
        super().__post_init__()

    @classmethod
    def from_env(cls) -> "QwenConfig":
        base = cls._base_env_kwargs()
        base.update(
            executor_kind="qwen",
            qwen_api_key=os.environ.get("QWEN_API_KEY", ""),
            qwen_base_url=os.environ.get(
                "QWEN_BASE_URL",
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            ),
            qwen_model=os.environ.get("QWEN_MODEL", "qwen3-coder-plus"),
            qwen_reasoning_effort=os.environ.get("QWEN_REASONING_EFFORT", "medium"),
        )
        if os.environ.get("QWEN_MAX_TURNS"):
            base["executor_max_turns"] = int(os.environ["QWEN_MAX_TURNS"])
        return cls(**base)
