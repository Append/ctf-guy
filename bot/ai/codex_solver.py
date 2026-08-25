#!/usr/bin/env python3
"""Codex CLI solver wrapper (optional).

Only used if CODEX_ENABLED=true and `codex` CLI is installed.
No cost tracking — uses timeout as the only constraint.
"""

import asyncio
import json
import logging
import shutil
import tempfile

from ai.claude_code import (
    SolveResult,
    _safe_send,
    _is_docker_start_cmd,
    _extract_container_ids,
    cleanup_docker_containers,
)

log = logging.getLogger(__name__)

# Rate limit tracking — skip codex until cooldown expires
_rate_limited_until: float = 0


def is_codex_available() -> bool:
    """Check if codex CLI is installed and not rate limited."""
    import time

    if not shutil.which("codex"):
        return False
    if time.time() < _rate_limited_until:
        remaining = int(_rate_limited_until - time.time())
        log.info(f"Codex rate limited for {remaining}s more — skipping")
        return False
    return True


async def solve_with_codex(
    thread,
    challenge_dir: str,
    prompt: str,
    timeout: int = 600,
    telem_labels: dict | None = None,
    event_callback=None,
    challenge_id: int | None = None,
) -> SolveResult | None:
    """Run Codex CLI on a challenge.

    Uses `codex exec` in full-auto mode. No cost tracking
    (Codex doesn't report it). Returns SolveResult.
    """
    from ai.telemetry import ship_log, ship_metric
    from pathlib import Path

    _labels = {"challenge": Path(challenge_dir).name, "model": "codex"}
    if telem_labels:
        _labels.update(telem_labels)

    if not is_codex_available():
        await _safe_send(thread, "Codex CLI not found — skipping.")
        return None

    await _safe_send(thread, "Dispatching to Codex (bwrap sandbox)...")
    log.info(f"Codex: starting in {challenge_dir}")

    from pathlib import Path
    from ai.sandbox import create_bwrap_workspace, build_bwrap_cmd, sync_back_artifacts

    challenge_path = Path(challenge_dir).resolve()
    tmpdir, upperdir = create_bwrap_workspace(challenge_path)

    try:
        codex_cmd = [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
            "-C",
            str(challenge_path),
            prompt,
        ]
        bwrap_cmd = build_bwrap_cmd(challenge_path, upperdir, codex_cmd)

        from ai.sandbox import solver_env

        proc = await asyncio.create_subprocess_exec(
            *bwrap_cmd,
            cwd=str(challenge_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024 * 1024,  # 1GB — Codex can emit large JSON lines
            start_new_session=True,
            env=solver_env(),
        )

        # Stream stdout line-by-line (same as Claude Code)
        full_output = []
        result_text = ""
        _pending_docker_call = False  # next tool_output is from a docker command
        docker_containers: list[str] = []

        async def _read_stream():
            assert proc.stdout is not None
            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    etype = event.get("type", "")

                    # Codex JSONL format:
                    #   {"type": "item.completed", "item": {"type": "agent_message", "text": "..."}}
                    #   {"type": "item.completed", "item": {"type": "tool_call", "name": "...", ...}}
                    #   {"type": "item.completed", "item": {"type": "tool_output", "output": "..."}}
                    #   {"type": "turn.completed", "usage": {...}}
                    if etype == "item.completed":
                        item = event.get("item", {})
                        itype = item.get("type", "")
                        if itype == "agent_message":
                            text = item.get("text", "")
                            if text:
                                log.info(f"Codex text ({len(text)} chars): {text[:100]}...")
                                full_output.append(text)
                                ship_log(
                                    "agent.text",
                                    text=text[:200],
                                    char_count=len(text),
                                    **_labels,
                                )
                                if event_callback:
                                    event_callback("text", text=text[:500])
                        elif itype == "tool_call":
                            name = item.get("name", "?")
                            log.info(f"Codex tool call: {name}")
                            ship_log("agent.tool_call", tool_name=name, **_labels)
                            ship_metric("ctf_tool_calls_total", 1, tool_name=name, **_labels)
                            if event_callback:
                                event_callback(
                                    "tool_call",
                                    tool_name=name,
                                    args=item.get("arguments", ""),
                                )
                            # Track docker commands
                            cmd_str = str(item.get("arguments", ""))
                            _pending_docker_call = _is_docker_start_cmd(cmd_str)
                        elif itype == "tool_output":
                            out = item.get("output", "")
                            log.info(f"Codex tool output ({len(out)} chars)")
                            ship_log("agent.tool_result", output_len=len(out), **_labels)
                            if event_callback:
                                event_callback("tool_result", output_len=len(out))
                            if _pending_docker_call:
                                _pending_docker_call = False
                                cids = _extract_container_ids(out)
                                if cids:
                                    docker_containers.extend(cids)
                                    log.info(f"Tracked docker containers from Codex: {cids}")
                        else:
                            log.debug(f"Codex item: {itype}")
                    elif etype == "turn.completed":
                        usage = event.get("usage", {})
                        tokens = usage.get("output_tokens", 0)
                        log.info(f"Codex turn done (output_tokens={tokens})")
                        ship_metric(
                            "ctf_tokens_total",
                            float(tokens),
                            direction="output",
                            **_labels,
                        )
                    elif etype in ("error", "turn.failed"):
                        error_msg = event.get("message", "") or event.get("error", {}).get("message", "")
                        log.warning(f"Codex error: {error_msg[:200]}")
                        ship_log("agent.error", error=error_msg[:300], **_labels)
                        if "limit" in error_msg.lower() or "rate" in error_msg.lower():
                            global _rate_limited_until
                            import time as _time, re as _re

                            # Try to parse reset time like "try again at 8:26 PM"
                            cooldown = 1800  # Default 30 min
                            time_match = _re.search(
                                r"(\d{1,2}):(\d{2})\s*(AM|PM)",
                                error_msg,
                                _re.IGNORECASE,
                            )
                            if time_match:
                                from datetime import datetime

                                h, m, ap = (
                                    int(time_match.group(1)),
                                    int(time_match.group(2)),
                                    time_match.group(3).upper(),
                                )
                                if ap == "PM" and h != 12:
                                    h += 12
                                elif ap == "AM" and h == 12:
                                    h = 0
                                now = datetime.now()
                                reset = now.replace(hour=h, minute=m, second=0)
                                if reset < now:
                                    from datetime import timedelta

                                    reset = reset + timedelta(days=1)
                                cooldown = max(60, int((reset - now).total_seconds()))
                            _rate_limited_until = _time.time() + cooldown
                            log.warning(f"Codex rate limited — disabled for {cooldown}s")
                            ship_metric(
                                "ctf_codex_rate_limited",
                                1,
                                cooldown_seconds=str(cooldown),
                            )
                            await _safe_send(
                                thread,
                                f"Codex rate limited — disabled for {cooldown // 60}m. {error_msg[:150]}",
                            )
                    elif etype in ("thread.started", "turn.started"):
                        log.debug(f"Codex event: {etype}")
                    else:
                        log.debug(f"Codex event: {etype}")
                except json.JSONDecodeError:
                    log.debug(f"Codex non-JSON line: {line[:200]}")
                    full_output.append(line)

        from ai.flag_events import register, FLAG_GRACE_PERIOD

        flag_event = None
        if challenge_id is not None:
            flag_event = register(challenge_id)

        try:
            reader_task = asyncio.create_task(_read_stream())

            if flag_event is not None:
                flag_wait = asyncio.create_task(flag_event.wait())
                done, _ = await asyncio.wait(
                    {reader_task, flag_wait},
                    timeout=timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                flag_wait.cancel()

                if reader_task in done:
                    await proc.wait()
                elif flag_event.is_set():
                    log.info(f"Codex: flag confirmed — extending timeout by {FLAG_GRACE_PERIOD}s")
                    ship_log("agent.flag_timeout_bypass", **_labels)
                    ship_metric("ctf_flag_timeout_bypass", 1.0, **_labels)
                    try:
                        await asyncio.wait_for(reader_task, timeout=FLAG_GRACE_PERIOD)
                        await proc.wait()
                    except TimeoutError:
                        from ai.sandbox import kill_process_tree

                        kill_process_tree(proc)
                        await proc.wait()
                        await _safe_send(thread, "Codex grace period expired.")
                        ship_log("agent.grace_period_expired", **_labels)
                else:
                    reader_task.cancel()
                    try:
                        await reader_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    from ai.sandbox import kill_process_tree

                    kill_process_tree(proc)
                    await proc.wait()
                    await _safe_send(thread, f"Codex timed out after {timeout}s")
                    ship_log("agent.timeout", timeout=timeout, **_labels)
                    return None
            else:
                try:
                    await asyncio.wait_for(reader_task, timeout=timeout)
                except TimeoutError:
                    from ai.sandbox import kill_process_tree

                    kill_process_tree(proc)
                    await proc.wait()
                    await _safe_send(thread, f"Codex timed out after {timeout}s")
                    ship_log("agent.timeout", timeout=timeout, **_labels)
                    return None
                await proc.wait()
        except asyncio.CancelledError:
            from ai.sandbox import kill_process_tree

            kill_process_tree(proc)
            await proc.wait()
            log.info("Codex: cancelled")
            ship_log("agent.cancelled", **_labels)
            raise
        finally:
            # Never unregister here — caller reads get_result() first
            pass

        stderr_data = await proc.stderr.read() if proc.stderr else b""
        if stderr_data:
            stderr_text = stderr_data.decode(errors="replace")[:500]
            log.warning(f"Codex stderr: {stderr_text}")
            ship_log("agent.error", error=stderr_text[:300], **_labels)
        if not full_output:
            log.warning(f"Codex produced no output (rc={proc.returncode})")
            ship_log("agent.error", error=f"no output, rc={proc.returncode}", **_labels)

        # Check for rate limit in any output (stream events OR stderr)
        all_text = " ".join(full_output) + " " + (stderr_data.decode(errors="replace") if stderr_data else "")
        if "usage limit" in all_text.lower() or "rate limit" in all_text.lower():
            global _rate_limited_until
            import time as _time

            _rate_limited_until = _time.time() + 1800  # 30 min default
            log.warning("Codex rate limited (detected from output/stderr) — disabled for 30m")
            ship_metric("ctf_codex_rate_limited", 1, cooldown_seconds="1800")
            await _safe_send(thread, "Codex rate limited — disabled for 30m.")

        result_text = "\n".join(full_output) if full_output else ""

        if result_text.strip():
            await _safe_send(thread, f"Codex output:\n```\n{result_text[:1500]}\n```")

        log.info(f"Codex: finished ({len(result_text)} bytes)")

        return SolveResult(
            output=result_text,
            cost_usd=0.0,  # Codex doesn't report cost
            num_turns=0,
            duration_ms=0,
        )

    except FileNotFoundError:
        await _safe_send(thread, "Codex CLI not found.")
        return None
    except Exception as e:
        log.error(f"Codex error: {e}", exc_info=True)
        return None
    finally:
        # Sync artifacts before cleaning up tmpdir — critical for race mode
        # where solver may be cancelled after flag submission
        try:
            if upperdir.exists():
                sync_back_artifacts(upperdir, challenge_path, "codex")
        except Exception as e:
            log.warning(f"Codex artifact sync in finally failed: {e}")
        shutil.rmtree(tmpdir, ignore_errors=True)
        if docker_containers:
            await cleanup_docker_containers(docker_containers)
