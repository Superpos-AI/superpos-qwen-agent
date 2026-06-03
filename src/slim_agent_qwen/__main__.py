"""Backwards-compatibility entrypoint: ``python -m slim_agent_qwen``.

Forwards to :mod:`superpos_agent_qwen.__main__` so existing operator
invocations keep working.  Emits a ``DeprecationWarning`` to encourage
migration to the new module name.
"""

from __future__ import annotations

import warnings

from superpos_agent_qwen.__main__ import cli

warnings.warn(
    "'python -m slim_agent_qwen' is deprecated; use "
    "'python -m superpos_agent_qwen' instead.",
    DeprecationWarning,
    stacklevel=2,
)

if __name__ == "__main__":
    cli()
