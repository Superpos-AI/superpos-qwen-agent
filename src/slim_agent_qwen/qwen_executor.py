"""Queue-based worker that invokes Alibaba's Qwen Code CLI and routes output.

CLI integration notes
---------------------
This executor wraps the ``qwen`` command from ``@qwen-code/qwen-code``.  Qwen
Code is a fork of Google's Gemini CLI, so the flag set and event shapes are
very close to ``gemini``'s.  Key assumptions (verify against ``qwen --help``
if anything breaks):

* ``qwen --output-format json --yolo "<prompt>"`` runs non-interactively and
  emits JSON events to stdout.  ``--yolo`` auto-approves tool calls;
  ``--output-format json`` switches stdout to structured JSONL.
* ``--model <id>`` selects the model (e.g. ``qwen3-coder-plus``).
* Qwen Code uses an OpenAI-compatible API.  Auth is via ``OPENAI_API_KEY``
  + ``OPENAI_BASE_URL`` env vars pointing to DashScope (or the OAuth flow
  via ``qwen auth login``).  ``QwenConfig`` keeps these as ``qwen_*`` for
  clarity and the executor maps them to the CLI's expected ``OPENAI_*``
  names just before spawn.
* MCP server config lives in ``~/.qwen/settings.json`` under ``mcpServers``.
* The CLI honours a top-level ``QWEN.md`` in the working directory as a
  system-prompt overlay (the fork's analogue of Gemini's ``GEMINI.md``).

Session resume
--------------
Qwen Code's chat session resume varies with CLI version, so we deliberately
do NOT rely on it.  Instead, ``QwenExecutor`` maintains its own per-chat
history file (``{home_dir}/history/<chat_id>.jsonl``) and prepends the last
few user/assistant turns to each new prompt — same pattern as the Gemini
agent.  Robust across CLI versions; the cost is re-sending recent context,
which Qwen's large context windows absorb without trouble.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import time
from asyncio.subprocess import PIPE
from dataclasses import dataclass
from pathlib import Path

from superpos_agent_core import (
    Executor,
    ExecutionRequest,
    RecentTasksLog,
    SessionStore,
    SuperposClient,
    TaskSummary,
    TelegramGateway,
    TelegramStreamer,
    collect_mcp_servers,
    discover_modules,
    ensure_worktree,
    is_git_repo,
    report_progress,
    worktree_path,
)

from .config import QwenConfig
from .runtime_config import QwenRuntimeConfig

log = logging.getLogger(__name__)

# ── Persona / history layout ──────────────────────────────────────────

_PERSONA_BEGIN = "<!-- PERSONA:BEGIN -->"
_PERSONA_END = "<!-- PERSONA:END -->"
_PERSONA_RE = re.compile(
    rf"{re.escape(_PERSONA_BEGIN)}.*?{re.escape(_PERSONA_END)}\n*", re.DOTALL,
)

_HISTORY_REPLAY_TURNS = 10
_MAX_PROMPT_BYTES = 500_000


@dataclass
class _HistoryTurn:
    role: str
    text: str
    timestamp: float


def _write_mcp_settings(home_dir: str, mcp_servers: dict) -> None:
    """Write Qwen's MCP server configuration to ``{home_dir}/settings.json``."""
    settings_path = Path(home_dir) / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    existing["mcpServers"] = mcp_servers
    settings_path.write_text(json.dumps(existing, indent=2))


class QwenExecutor(Executor):
    """Concrete executor that drives Alibaba's Qwen Code CLI."""

    def __init__(
        self,
        config: QwenConfig,
        runtime: QwenRuntimeConfig,
        superpos: SuperposClient | None,
        gateway: TelegramGateway | None,
        persona: str | None = None,
    ) -> None:
        super().__init__(max_parallel=config.executor_max_parallel)
        self._config = config
        self._runtime = runtime
        self._superpos = superpos
        self._gateway = gateway
        self._persona = persona

        # Persona is injected into QWEN.md in the project root
        self._inject_persona_into_qwen_md()

        # Session marker (kept for /new) + per-chat history files
        self._sessions = SessionStore(
            path=os.path.join(config.home_dir, "session_store.json"),
        )
        self._history_dir = Path(config.home_dir) / "history"
        self._history_dir.mkdir(parents=True, exist_ok=True)
        self._recent_tasks = RecentTasksLog(max_per_chat=5)

        self._semaphore = asyncio.Semaphore(config.executor_max_parallel)
        self._worktree_locks: dict[str, asyncio.Lock] = {}

        modules = discover_modules(config.modules_dir)
        mcp = collect_mcp_servers(modules)
        if mcp:
            _write_mcp_settings(config.home_dir, mcp)
            log.info("Wrote %d MCP server(s) to %s", len(mcp), config.home_dir)

    # ── Persona injection ─────────────────────────────────────────────

    def _qwen_md_path(self) -> str:
        return os.path.join(self._config.executor_working_dir, "QWEN.md")

    def _inject_persona_into_qwen_md(self) -> None:
        """Prepend persona to QWEN.md so the CLI picks it up as system prompt."""
        if not self._persona:
            return
        path = self._qwen_md_path()
        existing = ""
        if os.path.exists(path):
            with open(path, "r") as f:
                existing = f.read()
        persona_block = (
            f"{_PERSONA_BEGIN}\n"
            f"{self._persona}\n"
            f"{_PERSONA_END}\n\n"
        )
        if _PERSONA_BEGIN in existing:
            existing = _PERSONA_RE.sub(persona_block, existing)
            with open(path, "w") as f:
                f.write(existing)
        else:
            with open(path, "w") as f:
                f.write(persona_block + existing)
        log.info("Injected persona into %s", path)

    def update_persona(self, prompt: str | None, version: int | None = None) -> None:
        """Replace persona and re-inject into QWEN.md.

        ``version`` accepted for interface parity but Qwen doesn't track
        per-persona versions — the file is the single source of truth.
        """
        self._persona = prompt
        self._inject_persona_into_qwen_md()

    # ── Session / history management ──────────────────────────────────

    def clear_session(self, chat_id: int | str) -> None:
        """Drop session marker + history file for a chat."""
        self._sessions.clear(chat_id)
        history_path = self._history_path(chat_id)
        try:
            history_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("Failed to remove history file %s", history_path, exc_info=True)

    def _history_path(self, chat_id: int | str) -> Path:
        return self._history_dir / f"{chat_id}.jsonl"

    def _load_recent_history(self, chat_id: int | str, max_turns: int) -> list[_HistoryTurn]:
        path = self._history_path(chat_id)
        if not path.exists():
            return []
        try:
            lines = path.read_text().splitlines()
        except OSError:
            return []
        turns: list[_HistoryTurn] = []
        for line in lines[-max_turns:]:
            try:
                row = json.loads(line)
                turns.append(_HistoryTurn(
                    role=row.get("role", ""),
                    text=row.get("text", ""),
                    timestamp=row.get("ts", 0.0),
                ))
            except json.JSONDecodeError:
                continue
        return turns

    def _append_history(self, chat_id: int | str, role: str, text: str) -> None:
        if not text.strip():
            return
        path = self._history_path(chat_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a") as f:
                f.write(json.dumps({
                    "role": role,
                    "text": text[:50_000],
                    "ts": time.time(),
                }) + "\n")
        except OSError:
            log.warning("Failed to append history for chat %s", chat_id, exc_info=True)

    def _render_history_preamble(self, chat_id: int | str) -> str | None:
        turns = self._load_recent_history(chat_id, _HISTORY_REPLAY_TURNS)
        if not turns:
            return None
        lines = [
            "## Previous Conversation",
            (
                "These messages are from the same Telegram thread; treat them "
                "as your own prior turns (you said the assistant lines)."
            ),
            "",
        ]
        for turn in turns:
            label = "User" if turn.role == "user" else "Assistant"
            text = turn.text.strip().replace("\n", " ")
            if len(text) > 2_000:
                text = text[:2_000] + "…"
            lines.append(f"**{label}:** {text}")
        return "\n".join(lines)

    # ── Preflight ─────────────────────────────────────────────────────

    async def preflight(self) -> None:
        """Verify Qwen CLI is installed and credentials work."""
        log.info("Verifying Qwen authentication...")
        try:
            env = self._build_env()
            process = await asyncio.create_subprocess_exec(
                "qwen", "--output-format", "json", "--yolo", "hi",
                stdout=PIPE,
                stderr=PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=60,
            )
            if process.returncode != 0:
                stderr_str = stderr.decode(errors="replace")
                lower = stderr_str.lower()
                if any(s in lower for s in (
                    "authentication", "invalid api key", "unauthorized",
                    "permission denied", "not authenticated", "401",
                )):
                    print(_AUTH_HELP_INVALID_KEY, file=sys.stderr)
                    sys.exit(1)
                raise RuntimeError(
                    f"Qwen auth check failed (exit {process.returncode}): "
                    f"{stderr_str[:500]}"
                )
            log.info("Qwen authentication OK")
        except asyncio.TimeoutError:
            log.warning("Qwen auth check timed out (60s) — proceeding anyway")
        except FileNotFoundError:
            log.critical(
                "'qwen' CLI not found on PATH. "
                "Install with: npm install -g @qwen-code/qwen-code"
            )
            sys.exit(1)

    # ── Cleanup ───────────────────────────────────────────────────────

    def cleanup_stale_sessions(self, max_age_hours: int = 24) -> dict[str, int]:
        """Delete old Qwen cache + our history files older than max_age_hours."""
        counts = {"projects": 0, "session_env": 0, "bytes_freed": 0}
        cutoff = time.time() - (max_age_hours * 3600)

        if self._history_dir.is_dir():
            for entry in self._history_dir.iterdir():
                try:
                    if entry.stat().st_mtime < cutoff:
                        size = entry.stat().st_size
                        entry.unlink()
                        counts["projects"] += 1
                        counts["bytes_freed"] += size
                except OSError:
                    pass

        cache_dir = Path(self._config.home_dir) / "cache"
        if cache_dir.is_dir():
            for entry in cache_dir.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    if entry.stat().st_mtime < cutoff:
                        size = sum(
                            f.stat().st_size
                            for f in entry.rglob("*")
                            if f.is_file()
                        )
                        shutil.rmtree(entry)
                        counts["session_env"] += 1
                        counts["bytes_freed"] += size
                except OSError:
                    pass

        return counts

    # ── Worktree management ───────────────────────────────────────────

    def _get_worktree_lock(self, slot: str) -> asyncio.Lock:
        if slot not in self._worktree_locks:
            self._worktree_locks[slot] = asyncio.Lock()
        return self._worktree_locks[slot]

    def _resolve_slot(self, req: ExecutionRequest) -> str:
        if (
            req.branch
            and self._config.executor_worktree_isolation
            and is_git_repo(self._config.executor_working_dir)
        ):
            return worktree_path(self._config.executor_working_dir, req.branch)
        return "__main__"

    # ── Main consumer loop ────────────────────────────────────────────

    async def run(self) -> None:
        log.info(
            "Qwen executor started (max_parallel=%d)",
            self._config.executor_max_parallel,
        )
        while True:
            req = await self.queue.get()
            asyncio.create_task(self._run_one(req))

    async def _run_one(self, req: ExecutionRequest) -> None:
        claim_expired = asyncio.Event()
        progress_task: asyncio.Task | None = None

        # Start heartbeat IMMEDIATELY — before semaphore/worktree waits.
        if req.source == "superpos" and req.superpos_task_id and self._superpos:
            progress_task = asyncio.create_task(
                report_progress(self._superpos, req.superpos_task_id, claim_expired)
            )

        try:
            async with self._semaphore:
                if claim_expired.is_set():
                    log.warning(
                        "Claim expired while waiting for semaphore: %s",
                        req.superpos_task_id,
                    )
                    return

                slot = self._resolve_slot(req)
                wt_lock = self._get_worktree_lock(slot)

                lock_acquired = False
                try:
                    lock_task = asyncio.create_task(wt_lock.acquire())
                    expire_task = asyncio.create_task(claim_expired.wait())
                    done, pending = await asyncio.wait(
                        [lock_task, expire_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for p in pending:
                        p.cancel()
                        try:
                            await p
                        except asyncio.CancelledError:
                            pass

                    if claim_expired.is_set():
                        if lock_task in done and lock_task.result():
                            wt_lock.release()
                        log.warning(
                            "Claim expired while waiting for worktree lock: %s",
                            req.superpos_task_id,
                        )
                        return

                    lock_acquired = True
                    await self._execute(req, claim_expired)
                finally:
                    if lock_acquired:
                        wt_lock.release()
        except asyncio.CancelledError:
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                raise
            log.warning("Spurious CancelledError during execution (suppressed)")
        except Exception:
            log.exception("Execution failed for request: %s", req)
        finally:
            if progress_task:
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass
            if req.superpos_task_id:
                self.remove_superpos_task(req.superpos_task_id)
            self.queue.task_done()


    async def _execute(
        self, req: ExecutionRequest, claim_expired: asyncio.Event, retries: int = 3,
    ) -> None:
        self._active_count += 1
        if self._active_count == 1 and self._superpos:
            try:
                await self._superpos.update_status("busy")
            except Exception:
                log.debug("Failed to set agent status to busy")

        streamer = TelegramStreamer(self._gateway, req.chat_id)
        try:
            await streamer.start()
        except Exception:
            log.debug("Streamer start failed (non-fatal)")

        inner_task: asyncio.Task | None = None
        watcher_task: asyncio.Task | None = None

        async def _watch_claim_expiry() -> None:
            await claim_expired.wait()
            if inner_task is not None:
                inner_task.cancel()

        try:
            inner_task = asyncio.create_task(
                self._execute_inner(req, streamer, retries)
            )
            if req.source == "superpos" and req.superpos_task_id:
                watcher_task = asyncio.create_task(_watch_claim_expiry())
            try:
                await inner_task
            except asyncio.CancelledError:
                if claim_expired.is_set():
                    log.warning(
                        "Execution aborted: claim expired for superpos task %s",
                        req.superpos_task_id,
                    )
                else:
                    raise
        finally:
            if watcher_task:
                watcher_task.cancel()
                try:
                    await watcher_task
                except asyncio.CancelledError:
                    pass
            try:
                await streamer.finish()
            except Exception:
                log.debug("Streamer finish failed (non-fatal)", exc_info=True)
            if req.image_paths:
                for p in req.image_paths:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
            self._active_count -= 1
            if self._active_count == 0 and self._superpos:
                try:
                    await self._superpos.update_status("online")
                except Exception:
                    log.debug("Failed to set agent status to online")

    # ── Background tasks ──────────────────────────────────────────────

    async def run_background(
        self,
        task_id: str,
        prompt: str,
        task_type: str = "dream",
        timeout_seconds: int = 300,
    ) -> None:
        """Execute a background task (dream, knowledge_fillin, …) without streamer."""
        label = task_type.replace("_", " ")
        log.info("%s task %s starting in background", label.capitalize(), task_id)

        claim_expired = asyncio.Event()
        progress_task: asyncio.Task | None = None
        if self._superpos:
            progress_task = asyncio.create_task(
                report_progress(self._superpos, task_id, claim_expired)
            )

        full_text = ""

        async def _run_inner() -> None:
            nonlocal full_text
            cmd = self._build_qwen_command(prompt=prompt)
            env = self._build_env()

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=PIPE,
                stderr=PIPE,
                cwd=self._config.executor_working_dir,
                env=env,
                limit=16 * 1024 * 1024,
            )

            try:
                dedup = _EventDeduplicator()
                async for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    text = dedup.extract_text(event)
                    if text:
                        full_text += text
                await process.wait()
            finally:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except (asyncio.TimeoutError, Exception):
                        pass

        inner_task: asyncio.Task | None = None
        watcher_task: asyncio.Task | None = None

        async def _watch_claim_expiry() -> None:
            await claim_expired.wait()
            if inner_task is not None and not inner_task.done():
                inner_task.cancel()

        expired = False
        timed_out = False
        try:
            inner_task = asyncio.create_task(_run_inner())
            watcher_task = asyncio.create_task(_watch_claim_expiry())
            try:
                await asyncio.wait_for(inner_task, timeout=timeout_seconds)
            except asyncio.TimeoutError:
                timed_out = True
                log.warning(
                    "%s task %s timed out after %ds — cancelling",
                    label.capitalize(), task_id, timeout_seconds,
                )
                inner_task.cancel()
                try:
                    await inner_task
                except (asyncio.CancelledError, Exception):
                    pass
            except asyncio.CancelledError:
                if claim_expired.is_set():
                    expired = True
                    log.warning(
                        "%s task %s cancelled: claim expired",
                        label.capitalize(), task_id,
                    )
                else:
                    raise

            if expired:
                return

            if timed_out:
                if self._superpos and not claim_expired.is_set():
                    try:
                        await self._superpos.fail_task(
                            task_id,
                            f"{label.capitalize()} timed out after {timeout_seconds}s",
                        )
                    except Exception:
                        log.debug("Failed to mark timed-out task %s", task_id)
                return

            result = full_text[-2000:] if len(full_text) > 2000 else full_text
            summary = {
                "description": f"{label.capitalize()}: automated background task",
                "output_excerpt": full_text[:500] if full_text else None,
            }
            if self._superpos and not claim_expired.is_set():
                await self._superpos.complete_task(task_id, result, summary=summary)
            log.info("%s task %s completed", label.capitalize(), task_id)
        except Exception:
            log.warning("%s task %s failed", label.capitalize(), task_id, exc_info=True)
            if self._superpos and not claim_expired.is_set():
                try:
                    await self._superpos.fail_task(task_id, f"{label.capitalize()} failed")
                except Exception:
                    pass
        finally:
            if watcher_task:
                watcher_task.cancel()
                try:
                    await watcher_task
                except asyncio.CancelledError:
                    pass
            if progress_task:
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass

    # ── Command construction & inner execute ──────────────────────────

    def _build_env(self) -> dict[str, str]:
        """Map QwenConfig fields onto the OpenAI-compatible env vars the CLI expects."""
        env = {**os.environ}
        if self._config.qwen_api_key:
            env["OPENAI_API_KEY"] = self._config.qwen_api_key
        if self._config.qwen_base_url:
            env["OPENAI_BASE_URL"] = self._config.qwen_base_url
        # Some Qwen CLI versions consult OPENAI_MODEL as a default; the
        # explicit --model flag below overrides it but setting both keeps
        # behaviour consistent if the flag set changes.
        if self._runtime.model:
            env["OPENAI_MODEL"] = self._runtime.model
        return env

    def _build_qwen_command(
        self,
        prompt: str,
        cwd: str | None = None,
        system_prompt_append: str | None = None,
    ) -> list[str]:
        """Build the qwen CLI command list."""
        full_prompt = prompt
        if system_prompt_append:
            full_prompt = f"{system_prompt_append}\n\n---\n\n{prompt}"

        cmd = [
            "qwen",
            "--output-format", "json",
            "--yolo",
        ]
        if self._runtime.model:
            cmd.extend(["--model", self._runtime.model])
        if self._runtime.effort:
            cmd.extend(["-c", f"thinking_effort={self._runtime.effort}"])
        cmd.append(full_prompt)
        return cmd

    async def _execute_inner(
        self,
        req: ExecutionRequest,
        streamer: TelegramStreamer,
        retries: int,
    ) -> None:
        t0 = time.monotonic()
        full_text = ""

        cwd_override: str | None = None
        if (
            req.branch
            and self._config.executor_worktree_isolation
            and is_git_repo(self._config.executor_working_dir)
        ):
            try:
                cwd_override = await ensure_worktree(
                    self._config.executor_working_dir, req.branch,
                )
            except Exception:
                log.warning(
                    "Failed to create worktree for branch %r; falling back to default cwd",
                    req.branch, exc_info=True,
                )

        system_prompt_append: str | None = None
        if (
            not req.branch
            and self._config.executor_worktree_isolation
            and is_git_repo(self._config.executor_working_dir)
        ):
            wt_base = self._config.executor_working_dir
            system_prompt_append = (
                "## Worktree Isolation\n"
                "When this task requires implementing code changes on a new branch:\n"
                f"1. First run `git -C {wt_base} fetch origin` to get latest refs.\n"
                f"2. Choose a branch name, then: `git worktree add {wt_base}/.worktrees/<branch> -b <branch> origin/main`\n"
                f"3. Do all file edits and git operations inside `{wt_base}/.worktrees/<branch>`\n"
                "4. Commit, push the branch, and open a PR from the worktree.\n"
                "IMPORTANT: Always branch from origin/main to avoid inheriting unrelated in-progress work.\n"
                "NEVER create branches from the current HEAD of the main workspace — it may be on an unmerged feature branch.\n"
                "For conversational replies or read-only tasks, skip this entirely."
            )

        if req.source == "telegram":
            history_preamble = self._render_history_preamble(req.chat_id)
            if history_preamble:
                system_prompt_append = (
                    f"{system_prompt_append}\n\n{history_preamble}"
                    if system_prompt_append else history_preamble
                )
            recent = self._recent_tasks.render(req.chat_id)
            if recent:
                system_prompt_append = (
                    f"{system_prompt_append}\n\n{recent}"
                    if system_prompt_append else recent
                )

        effective_cwd = cwd_override or self._config.executor_working_dir

        prompt_text = req.prompt
        if req.image_paths:
            image_refs = "\n".join(f"- {p}" for p in req.image_paths)
            prompt_text = (
                f"The user sent these images. Read them first, then respond.\n"
                f"{image_refs}\n\n{prompt_text}"
            )

        if len(prompt_text) > _MAX_PROMPT_BYTES:
            log.warning("Prompt too large (%dKB), truncating", len(prompt_text) // 1024)
            prompt_text = prompt_text[:_MAX_PROMPT_BYTES] + "\n... (truncated)"

        for attempt in range(1, retries + 1):
            try:
                cmd = self._build_qwen_command(
                    prompt=prompt_text,
                    cwd=effective_cwd,
                    system_prompt_append=system_prompt_append,
                )
                env = self._build_env()

                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=PIPE,
                    stderr=PIPE,
                    cwd=effective_cwd,
                    env=env,
                    limit=16 * 1024 * 1024,
                )

                stderr_chunks: list[bytes] = []
                json_errors: list[str] = []

                log.debug("Running qwen command: %s (cwd=%s)", cmd, effective_cwd)

                async def _drain_stdout():
                    nonlocal full_text
                    dedup = _EventDeduplicator()
                    async for line in process.stdout:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            log.debug("Non-JSON line from qwen: %s", line[:200])
                            continue

                        if event.get("type") == "error":
                            json_errors.append(event.get("message", ""))
                        err_info = event.get("error")
                        if isinstance(err_info, dict):
                            json_errors.append(err_info.get("message", ""))

                        text = dedup.extract_text(event)
                        if text:
                            full_text += text
                            await streamer.append(text)

                        tool_info = dedup.extract_tool_use(event)
                        if tool_info:
                            await streamer.send_tool_notification(*tool_info)

                drain_task = asyncio.create_task(_drain_stdout())
                wait_task = asyncio.create_task(process.wait())

                _MAX_EXECUTION_SECS = 30 * 60
                try:
                    done, pending = await asyncio.wait_for(
                        asyncio.wait(
                            [drain_task, wait_task],
                            return_when=asyncio.ALL_COMPLETED,
                        ),
                        timeout=_MAX_EXECUTION_SECS,
                    )
                except asyncio.TimeoutError:
                    log.warning(
                        "Qwen execution timed out after %ds — killing process (pid=%s)",
                        _MAX_EXECUTION_SECS, process.pid,
                    )
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    pending = {drain_task, wait_task}

                for p in pending:
                    if not p.done():
                        p.cancel()
                        try:
                            await asyncio.wait_for(p, timeout=5)
                        except (asyncio.CancelledError, asyncio.TimeoutError):
                            pass

                if process.returncode is None:
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        pass

                try:
                    stderr_data = await asyncio.wait_for(
                        process.stderr.read(), timeout=10,
                    )
                    if stderr_data:
                        stderr_chunks.append(stderr_data)
                except asyncio.TimeoutError:
                    log.warning("Timed out reading stderr from qwen process")
                stderr_str = b"".join(stderr_chunks).decode(errors="replace")

                if not stderr_str.strip() and json_errors:
                    stderr_str = " | ".join(filter(None, json_errors))

                if process.returncode != 0:
                    raise _QwenProcessError(process.returncode, stderr_str)

                await streamer.finish()

                if req.source == "telegram":
                    self._append_history(req.chat_id, "user", req.prompt)
                    if full_text:
                        self._append_history(req.chat_id, "assistant", full_text)

                if req.source == "superpos" and req.superpos_task_id and self._superpos:
                    result = full_text[-2000:] if len(full_text) > 2000 else full_text
                    elapsed = int(time.monotonic() - t0)
                    summary = {
                        "description": req.prompt[:200],
                        "output_excerpt": full_text[:500] if full_text else None,
                        "duration_seconds": elapsed,
                    }
                    try:
                        await self._superpos.complete_task(
                            req.superpos_task_id, result, summary=summary,
                        )
                    except Exception:
                        log.warning(
                            "Failed to complete superpos task %s — claim may have expired",
                            req.superpos_task_id, exc_info=True,
                        )
                    self._recent_tasks.record(
                        req.chat_id,
                        TaskSummary(
                            task_id=req.superpos_task_id,
                            description=req.prompt[:200],
                            outcome="succeeded",
                            detail=full_text[:500] if full_text else "",
                        ),
                    )
                return

            except _QwenProcessError as e:
                err_str = str(e)
                lower = err_str.lower()
                is_rate_limit = (
                    "rate_limit" in lower
                    or "rate limit" in lower
                    or "quota" in lower
                    or "resource_exhausted" in lower
                    or "throttle" in lower
                    or "429" in lower
                    or "at capacity" in lower
                    or "overloaded" in lower
                )
                is_auth_error = (
                    "authentication" in lower
                    or "invalid api key" in lower
                    or "unauthorized" in lower
                    or "permission denied" in lower
                    or "not authenticated" in lower
                    or "401" in lower
                )

                if is_auth_error:
                    log.critical(
                        "Qwen authentication failed — API key invalid or not configured. "
                        "Shutting down."
                    )
                    sys.exit(1)

                is_api_500 = (
                    "internal server error" in lower
                    or "api_error" in lower
                    or "overloaded" in lower
                    or "service unavailable" in lower
                    or "502" in lower
                    or "503" in lower
                )
                if is_api_500 and attempt < retries:
                    wait = 30 * attempt
                    log.warning(
                        "API server error (attempt %d/%d), retrying in %ds: %s",
                        attempt, retries, wait, err_str[:100],
                    )
                    await streamer.append(f"\n⏳ API error, retrying in {wait}s...\n")
                    await asyncio.sleep(wait)
                    continue

                if full_text.strip():
                    log.warning(
                        "Execution produced output but failed (attempt %d/%d); "
                        "not retrying to avoid duplicate side effects",
                        attempt, retries,
                    )
                elif is_rate_limit and attempt < retries:
                    wait = 30 * attempt
                    log.warning(
                        "Rate limited (attempt %d/%d), retrying in %ds",
                        attempt, retries, wait,
                    )
                    await streamer.append(f"\n⏳ Rate limited, retrying in {wait}s...\n")
                    await asyncio.sleep(wait)
                    continue

                log.error("Qwen process error (exit %d): %s", e.returncode, e.stderr)
                try:
                    await streamer.error(f"Error: {e}")
                except asyncio.CancelledError:
                    log.warning(
                        "CancelledError while sending error to Telegram (suppressed)"
                    )
                except Exception:
                    log.warning("Failed to send error notification", exc_info=True)
                if req.source == "superpos" and req.superpos_task_id and self._superpos:
                    elapsed = int(time.monotonic() - t0)
                    summary = {
                        "description": req.prompt[:200],
                        "error": err_str[:500],
                        "duration_seconds": elapsed,
                    }
                    try:
                        await self._superpos.fail_task(
                            req.superpos_task_id, err_str, summary=summary,
                        )
                    except Exception:
                        log.warning(
                            "Failed to mark superpos task %s as failed",
                            req.superpos_task_id,
                        )
                    self._recent_tasks.record(
                        req.chat_id,
                        TaskSummary(
                            task_id=req.superpos_task_id,
                            description=req.prompt[:200],
                            outcome="failed",
                            detail=err_str[:500],
                        ),
                    )
                return

            except Exception as e:
                err_str = str(e)
                log.exception("Unexpected error during execution")
                try:
                    await streamer.error(f"Error: {e}")
                except asyncio.CancelledError:
                    log.warning(
                        "CancelledError while sending error to Telegram (suppressed)"
                    )
                except Exception:
                    log.warning("Failed to send error notification", exc_info=True)
                if req.source == "superpos" and req.superpos_task_id and self._superpos:
                    elapsed = int(time.monotonic() - t0)
                    summary = {
                        "description": req.prompt[:200],
                        "error": err_str[:500],
                        "duration_seconds": elapsed,
                    }
                    try:
                        await self._superpos.fail_task(
                            req.superpos_task_id, err_str, summary=summary,
                        )
                    except Exception:
                        log.warning(
                            "Failed to mark superpos task %s as failed",
                            req.superpos_task_id,
                        )
                    self._recent_tasks.record(
                        req.chat_id,
                        TaskSummary(
                            task_id=req.superpos_task_id,
                            description=req.prompt[:200],
                            outcome="failed",
                            detail=err_str[:500],
                        ),
                    )
                return


# ─────────────────────────────────────────────────────────────────────
#  Event parsing & deduplication for Qwen Code's JSONL output
#  (mirrors Gemini CLI's shapes since Qwen Code is a fork)
# ─────────────────────────────────────────────────────────────────────


class _EventDeduplicator:
    """Filters duplicate text and tool events from Qwen Code's JSONL stream.

    Qwen Code inherits Gemini CLI's emission pattern: streaming deltas AND a
    completed message summary containing the same text.  This class prefers
    deltas (lower latency) and skips the trailing duplicates.
    """

    def __init__(self) -> None:
        self._saw_delta = False
        self._seen_tool_keys: set[str] = set()

    def extract_text(self, event: dict) -> str:
        etype = event.get("type", "")

        if etype in ("response.created", "response.started", "turn.started"):
            self._saw_delta = False
            return ""

        if etype in (
            "response.output_text.delta",
            "content_block_delta",
            "text_delta",
        ):
            self._saw_delta = True
            return event.get("delta", event.get("text", ""))

        if etype == "text" and "text" in event:
            self._saw_delta = True
            return event["text"]

        if etype == "message" and event.get("role") == "assistant":
            if self._saw_delta:
                return ""
            parts = []
            for block in event.get("content", []):
                if isinstance(block, dict) and block.get("type") in (
                    "output_text", "text",
                ):
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "".join(parts)

        if etype == "item.completed":
            if self._saw_delta:
                return ""
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message":
                return item.get("text", "")

        return ""

    def extract_tool_use(self, event: dict) -> tuple[str, object] | None:
        etype = event.get("type", "")

        name: str | None = None
        args: object = {}
        call_id = ""

        if etype in ("function_call", "tool_call", "tool_use"):
            name = event.get("name", event.get("function", {}).get("name", "unknown"))
            args = event.get(
                "input",
                event.get(
                    "arguments",
                    event.get("function", {}).get("arguments", {}),
                ),
            )
            call_id = event.get("call_id", event.get("id", ""))
        elif etype == "item.started":
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") in (
                "function_call",
                "tool_call",
                "tool_use",
            ):
                name = item.get("name", "unknown")
                args = item.get("input", item.get("arguments", {}))
                call_id = item.get("call_id", item.get("id", ""))
            elif isinstance(item, dict) and item.get("type") == "command_execution":
                cmd = item.get("command", "")
                if cmd.startswith("/bin/bash -lc '") and cmd.endswith("'"):
                    cmd = cmd[15:-1]
                elif cmd.startswith("/bin/bash -lc "):
                    cmd = cmd[14:]
                name = "run_shell_command"
                args = {"command": cmd}
                call_id = item.get("call_id", item.get("id", ""))
            else:
                return None
        else:
            return None

        if name is None:
            return None

        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"raw": args}

        if call_id:
            dedup_key = f"id:{call_id}"
        else:
            args_str = str(args)[:200]
            dedup_key = f"na:{name}:{args_str}"

        if dedup_key in self._seen_tool_keys:
            return None
        self._seen_tool_keys.add(dedup_key)

        return (name, args)


class _QwenProcessError(Exception):
    """Raised when the qwen subprocess exits with non-zero status."""

    def __init__(self, returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"qwen process exited with code {returncode}: {stderr[:500]}"
        )


_AUTH_HELP_INVALID_KEY = """
╔══════════════════════════════════════════════════════════════╗
║          Qwen authentication failed — cannot start           ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Option 1 — OAuth (free tier, 2k requests/day):              ║
║                                                              ║
║    docker run -it \\                                          ║
║      -v qwen_auth:/home/agent/.qwen \\                        ║
║      --entrypoint qwen slim-qwen-agent auth login            ║
║                                                              ║
║    Follow the prompts to authenticate.                       ║
║    Then restart the agent (keep the -v flag).                ║
║                                                              ║
║  Option 2 — DashScope API key:                               ║
║                                                              ║
║    Set QWEN_API_KEY=sk-... in your .env file.                ║
║    Get a key from https://dashscope.aliyun.com/              ║
║    (or https://dashscope-intl.aliyuncs.com/ outside China)   ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""
