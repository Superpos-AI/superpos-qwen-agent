# Slim-Agent-Qwen

Superpos slim agent backed by [Alibaba's Qwen Code CLI](https://github.com/QwenLM/qwen-code).

Sister project to `Slim-Agent-Claude`, `Slim-Agent-Codex`, and `Slim-Agent-Gemini` — same architecture, same Superpos integration, same Telegram bot interface; just a different LLM executor.  All the shared runtime lives in [`superpos-agent-core`](https://github.com/Superpos-AI/superpos-agent-core); this repo is a thin shell that contributes:

- `QwenExecutor` — wraps the `qwen` CLI as a subprocess and parses its JSONL events.  Qwen Code is a fork of Gemini CLI, so the event shapes are very close; the executor inherits most of the parsing logic.
- `QwenConfig` — adds `qwen_*` and `qwen_base_url` env-var bindings on top of `BaseConfig`.
- `QwenRuntimeConfig` — registers known Qwen models for the `/model list` Telegram command.
- Dockerfile / entrypoint — installs `@qwen-code/qwen-code` from npm and materializes `QWEN_API_KEY` / `QWEN_BASE_URL` into the env shape Qwen CLI expects (`OPENAI_API_KEY` / `OPENAI_BASE_URL`).

## Quick start

```bash
cp .env.example .env
# fill in SUPERPOS_*, TELEGRAM_*, QWEN_API_KEY (DashScope)
docker compose up --build
```

## Local dev

```bash
pip install -e .
python -m slim_agent_qwen
```

If you're hacking on `superpos-agent-core` in a sibling directory and want your changes picked up without re-pushing, uncomment the `[tool.uv.sources]` block in `pyproject.toml` (or `pip install -e ../superpos-agent-core` first).

## Auth modes

| Mode | Env vars | Notes |
|---|---|---|
| **DashScope API key** | `QWEN_API_KEY` + `QWEN_BASE_URL` | Pay-per-token, no rate limit on free tier |
| **OAuth login** | none (leave `QWEN_API_KEY` blank) | Free tier (2k req/day), requires interactive `qwen auth login` once to populate the `qwen_home` volume |

## Status

Qwen Code CLI is a Gemini CLI fork, so the executor reuses the Gemini event-parsing patterns directly.  If a new Qwen release changes flag names or event shapes, check `src/slim_agent_qwen/qwen_executor.py` — the JSONL extractor is the most likely place to need a tweak.
