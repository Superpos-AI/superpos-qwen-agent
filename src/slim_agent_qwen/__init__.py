"""Backwards-compatibility shim for the old ``slim_agent_qwen`` package name.

The package was renamed to :mod:`superpos_agent_qwen`.  This shim keeps the
legacy import path working so existing deployments don't break, but emits a
``DeprecationWarning`` to nudge operators toward the new name.
"""

from __future__ import annotations

import warnings as _warnings

import superpos_agent_qwen as _new

_warnings.warn(
    "The 'slim_agent_qwen' package has been renamed to 'superpos_agent_qwen'. "
    "The old name is provided as a compatibility shim and will be removed in a "
    "future release; please update your imports.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export the public API so ``from slim_agent_qwen import QwenConfig`` works.
from superpos_agent_qwen import (  # noqa: E402,F401
    QwenConfig,
    QwenExecutor,
    QwenRuntimeConfig,
)

__all__ = list(getattr(_new, "__all__", ("QwenConfig", "QwenExecutor", "QwenRuntimeConfig")))

# Make ``slim_agent_qwen.<anything>`` resolve to the new package's attributes,
# including submodules accessed lazily after import.
def __getattr__(name: str):  # pragma: no cover - thin delegator
    return getattr(_new, name)
