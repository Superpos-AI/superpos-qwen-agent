"""Legacy shim — forwards to :mod:`superpos_agent_qwen.runtime_config`.

Python does not route submodule imports through the parent package's
``__getattr__``, so ``import slim_agent_qwen.runtime_config`` would
otherwise fail even though ``slim_agent_qwen`` itself is importable.  This
stub keeps the old import path alive by re-exporting the new module's
public surface and registering itself as an alias in :data:`sys.modules`.
"""

from __future__ import annotations

import sys as _sys

from superpos_agent_qwen import runtime_config as _target
from superpos_agent_qwen.runtime_config import *  # noqa: F401,F403

_sys.modules[__name__] = _target
