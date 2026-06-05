#!/bin/bash
# Build the superpos-qwen-agent image locally.
# Run from the Superpos-Agent-Qwen/ directory.

set -e

cd "$(dirname "$0")"

# superpos-agent-core is installed from PyPI during the pip install step,
# so the build context is just this repo.
docker compose build "$@"
