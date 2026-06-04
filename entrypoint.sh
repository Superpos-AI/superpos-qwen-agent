#!/bin/bash
set -e

# Configure git identity if provided
if [ -n "$GIT_USER_NAME" ]; then
    git config --global user.name "$GIT_USER_NAME"
fi
if [ -n "$GIT_USER_EMAIL" ]; then
    git config --global user.email "$GIT_USER_EMAIL"
fi

# Configure GitHub auth. Centralized in agent-core so every agent stays in sync.
# Token precedence (static GITHUB_TOKEN is canonical and never expires):
#   1. GITHUB_TOKEN set → gh auth login --with-token + gh auth setup-git.
#   2. else a github_app service connection is discoverable → register a git
#      credential helper that mints short-lived installation tokens from the
#      Superpos broker on demand (no long-lived secret in the container).
#   3. else → direct git/gh auth skipped; GitHub is still reachable through the
#      Superpos proxy (the `superpos-github` module).
#
# We deliberately avoid `git config url.<token>.insteadOf`, which would bake the
# token into every clone's .git/config and leak it via `git remote -v`; both the
# gh credential helper and the broker helper hand tokens to git on demand only.
python3 -m superpos_agent_core.github_auth setup \
    || echo "Warning: GitHub auth setup failed (proxy access via superpos-github still works)"

# Materialize QWEN_API_KEY into OPENAI_API_KEY (the env var the CLI reads)
# and also persist credentials to ~/.qwen/auth.json so subsequent `qwen exec`
# calls don't need to re-validate the key on every spawn.
mkdir -p "$HOME/.qwen"
if [ -n "$QWEN_API_KEY" ]; then
    # Export under the names qwen-code consumes (OpenAI-compatible client).
    export OPENAI_API_KEY="$QWEN_API_KEY"
    export OPENAI_BASE_URL="${QWEN_BASE_URL:-https://dashscope-intl.aliyuncs.com/compatible-mode/v1}"
    export OPENAI_MODEL="${QWEN_MODEL:-qwen3-coder-plus}"

    if [ ! -f "$HOME/.qwen/auth.json" ]; then
        cat > "$HOME/.qwen/auth.json" <<EOF
{"api_key": "$QWEN_API_KEY", "base_url": "$OPENAI_BASE_URL", "auth_mode": "apikey"}
EOF
        chmod 600 "$HOME/.qwen/auth.json"
        echo "[entrypoint] Wrote $HOME/.qwen/auth.json from QWEN_API_KEY env" >&2
    fi
fi

# Run module setup: install deps, symlink scripts onto PATH, update QWEN.md.
# --bin-dir links scripts from both workspace and core-bundled modules,
# so platform tools (e.g. superpos-issues) work even when nothing is in
# /workspace/.qwen/modules.
#
# If this fails (network blip on `pip install`, broken module, etc.) the
# container still starts — the Dockerfile pre-populated modules-bin with
# build-time symlinks to workspace scripts, so those stay callable from
# PATH.  Only core-bundled tools (added at runtime) are lost in that
# degraded mode.
python3 -m superpos_agent_core.module_setup \
    --modules-dir /workspace/.qwen/modules \
    --agents-md /workspace/QWEN.md \
    --bin-dir /workspace/.qwen/modules-bin \
    || echo "Warning: module setup failed (build-time workspace symlinks remain in place)"

exec "$@"
