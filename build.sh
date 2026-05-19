#!/bin/bash
# Build the slim-qwen-agent image locally.
# Run from the Slim-Agent-Qwen/ directory.

set -e

cd "$(dirname "$0")"

# superpos-agent-core is fetched from GitHub during the pip install step,
# so the build context is just this repo.
docker compose build "$@"
