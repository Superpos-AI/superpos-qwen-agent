"""Slim-Agent-Qwen entry point — wires QwenExecutor into the core orchestrator."""

from __future__ import annotations

import asyncio
import logging

from slim_agent_core import run_agent

from .config import QwenConfig
from .qwen_executor import QwenExecutor
from .runtime_config import QwenRuntimeConfig

log = logging.getLogger(__name__)


async def main() -> None:
    config = QwenConfig.from_env()
    runtime = QwenRuntimeConfig.load(
        default_model=config.qwen_model,
        default_effort=config.qwen_reasoning_effort,
        home_dir=config.home_dir,
    )

    def _factory(cfg, rt, superpos, gateway, persona):
        return QwenExecutor(cfg, rt, superpos, gateway, persona=persona)

    await run_agent(config, runtime, executor_factory=_factory)


def cli() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    cli()
