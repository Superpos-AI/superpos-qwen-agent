"""Smoke tests: verify the package is importable and core classes are available."""

from superpos_agent_qwen import QwenConfig, QwenExecutor, QwenRuntimeConfig


def test_package_importable():
    """The top-level package and its public names are importable."""
    assert QwenConfig is not None
    assert QwenExecutor is not None
    assert QwenRuntimeConfig is not None


def test_qwen_config_defaults():
    """QwenConfig can be instantiated with defaults and has expected fields."""
    cfg = QwenConfig()
    assert cfg.qwen_model == "qwen3-coder-plus"
    assert cfg.qwen_base_url == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert cfg.qwen_reasoning_effort == "medium"
    assert cfg.executor_kind == "qwen"


def test_runtime_config_known_models():
    """QwenRuntimeConfig exposes a non-empty tuple of known models."""
    assert len(QwenRuntimeConfig.KNOWN_MODELS) > 0
    assert "qwen3-coder-plus" in QwenRuntimeConfig.KNOWN_MODELS


def test_runtime_config_effort_levels():
    """Effort levels include the three expected values."""
    assert QwenRuntimeConfig.EFFORT_LEVELS == ("low", "medium", "high")
