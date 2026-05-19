#!/bin/bash
set -e

# Configure git identity if provided
if [ -n "$GIT_USER_NAME" ]; then
    git config --global user.name "$GIT_USER_NAME"
fi
if [ -n "$GIT_USER_EMAIL" ]; then
    git config --global user.email "$GIT_USER_EMAIL"
fi

# Configure GitHub CLI auth if token provided.
#
# We intentionally do NOT use `git config --global url.<token-URL>.insteadOf`
# here — that pattern embeds the token into every clone's .git/config as the
# origin remote, so `git remote -v` prints the token in cleartext.  Instead we
# let `gh` register a credential helper; git fetches the token on demand and
# never persists it into repo configs or remote URLs.
if [ -n "$GITHUB_TOKEN" ]; then
    echo "$GITHUB_TOKEN" | gh auth login --with-token 2>/dev/null || true
    gh auth setup-git 2>/dev/null || true
fi

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

# Run module setup (install deps, update QWEN.md)
python3 -m superpos_agent_core.module_setup \
    --modules-dir /workspace/.qwen/modules \
    --agents-md /workspace/QWEN.md \
    || echo "Warning: module setup failed"

exec "$@"
