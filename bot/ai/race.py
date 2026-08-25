#!/usr/bin/env python3
"""Multi-solver race coordinator.

Races multiple Claude Code instances (different models) simultaneously.
First to find a correct flag wins. Cross-solver findings shared via
findings.jsonl in the challenge directory.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import discord

from ai.claude_code import SolveResult, _safe_send
from ai.manager import SolveManager
from ai.manager_feed import ManagerFeed
from config import Config

log = logging.getLogger(__name__)


async def race_solvers(
    thread: discord.Thread,
    challenge_dir: str,
    prompt: str,
    models: list[str],
    config: Config,
    timeout: int = 600,
    max_budget: float | None = None,
    challenge_id: int | None = None,
    corrections_enabled: bool = True,
    fast: bool = False,
) -> SolveResult | None:
    """Race multiple Claude Code instances with different models.

    Each solver runs in its own tmpfs workspace. The first to produce
    a correct flag wins and the others are cancelled.

    Args:
        thread: Discord thread for status updates
        challenge_dir: Real challenge directory
        prompt: Solve prompt
        models: List of model names to race (e.g. ["haiku", "opus"])
        config: Bot config
        timeout: Per-solver timeout
        max_budget: Per-solver budget cap

    Returns:
        SolveResult from the winning solver, or None if all fail.
    """
    challenge_path = Path(challenge_dir).resolve()
    race_start_time = time.time()
    solver_names = list(models)
    if config.codex_enabled:
        solver_names.append("codex")
    await _safe_send(thread, f"**Racing {len(solver_names)} solvers:** {', '.join(solver_names)}")
    log.info(f"Race: starting {len(models)} solvers for {challenge_dir}")

    # Build challenge context for manager
    challenge_meta = {
        "name": challenge_path.name,
        "category": "",
        "points": 0,
        "files": [],
        "description": "",
        "challenge_dir": str(challenge_path),
    }
    challenge_json = challenge_path / "challenge.json"
    if challenge_json.exists():
        try:
            meta = json.loads(challenge_json.read_text())
            challenge_meta["category"] = meta.get("category", "")
            challenge_meta["points"] = meta.get("points", 0)
            challenge_meta["files"] = meta.get("files", [])
            challenge_meta["description"] = meta.get("description", "")[:500]
        except Exception:
            pass

    # Launch Claude Code solvers — each gets bwrap isolation + its own manager
    # Manager is tied to racer lifetime via _run_with_manager wrapper
    tasks = []
    for i, model in enumerate(models):
        feed = ManagerFeed()
        model_timeout = config.race_timeouts.get(model, timeout)
        task = asyncio.create_task(
            _run_with_manager(
                config,
                feed,
                challenge_path,
                challenge_meta,
                thread,
                _run_racer(
                    thread,
                    challenge_path,
                    challenge_path,
                    prompt,
                    model,
                    config,
                    model_timeout,
                    max_budget,
                    solver_id=i + 1,
                    event_callback=feed.push,
                    category=challenge_meta.get("category"),
                    challenge_id=challenge_id,
                    fast=fast,
                ),
                corrections_enabled=corrections_enabled,
            )
        )
        tasks.append(task)

    # Launch Codex racer if enabled — now uses bwrap internally
    if config.codex_enabled:
        from ai.codex_solver import is_codex_available

        if is_codex_available():
            codex_feed = ManagerFeed()
            codex_timeout = config.race_timeouts.get("codex", timeout)
            codex_task = asyncio.create_task(
                _run_with_manager(
                    config,
                    codex_feed,
                    challenge_path,
                    challenge_meta,
                    thread,
                    _run_codex_racer(
                        thread,
                        challenge_path,
                        challenge_path,
                        prompt,
                        codex_timeout,
                        solver_id=len(models) + 1,
                        event_callback=codex_feed.push,
                        challenge_id=challenge_id,
                    ),
                    corrections_enabled=corrections_enabled,
                )
            )
            tasks.append(codex_task)
            await _safe_send(thread, "**Codex** joined the race.")
        else:
            log.info("Codex enabled but CLI not found — skipping")

    # Wait for a winner or all solvers to finish.
    # Flag event fires instantly on correct submission via /submit handler.
    # Winner's _process_stream handles its own grace period — race loop
    # just cancels losers and returns immediately so the queue moves on.
    winner_result = None
    winning_model = "unknown"
    from ai.flag_events import register

    flag_event = register(challenge_id) if challenge_id is not None else None
    flag_sentinel = asyncio.create_task(flag_event.wait()) if flag_event else None

    try:
        remaining = set(tasks)
        while remaining:
            wait_set = set(remaining)
            if flag_sentinel and not flag_sentinel.done():
                wait_set.add(flag_sentinel)
            done, still_pending = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED, timeout=5)
            remaining = still_pending - ({flag_sentinel} if flag_sentinel else set())

            # Check completed tasks for results
            for task in done:
                if task is flag_sentinel:
                    continue
                try:
                    result = task.result()
                except Exception:
                    continue

            # Flag confirmed via event
            flag_confirmed = flag_event is not None and flag_event.is_set()

            if flag_confirmed:
                from ai.flag_events import get_result as _get_flag_result

                flag_data = _get_flag_result(challenge_id)
                flag_content = flag_data.flag if flag_data else ""

                # Identify winner by solver_id from the flag event
                winner_task = None
                if flag_data and flag_data.solver_id:
                    try:
                        winner_idx = int(flag_data.solver_id) - 1  # solver_id is 1-based
                        if 0 <= winner_idx < len(tasks):
                            winner_task = tasks[winner_idx]
                            winning_model = solver_names[winner_idx]
                            if winner_task.done() and not winner_task.cancelled():
                                try:
                                    winner_result = winner_task.result()
                                except Exception:
                                    pass
                    except (ValueError, IndexError):
                        pass

                # Fallback: check completed tasks (non-race submit or solver_id parse failure)
                if winner_task is None:
                    for task, name in zip(tasks, solver_names, strict=False):
                        if task.done() and not task.cancelled():
                            try:
                                r = task.result()
                                if r and r.output:
                                    winner_result = r
                                    winning_model = name
                                    winner_task = task
                                    break
                            except Exception:
                                continue
                        elif not task.done() and winner_task is None:
                            winner_task = task
                            winning_model = name

                if not winner_result:
                    winner_result = SolveResult(output=flag_content or "flag confirmed")
                log.info(
                    f"Race: flag confirmed (winner={winning_model}, solver_id={flag_data.solver_id if flag_data else '?'})"
                )

                # Cancel losers immediately
                losers = [t for t in remaining if t is not winner_task]
                for task in losers:
                    task.cancel()
                await asyncio.gather(*losers, return_exceptions=True)

                # Winner's _process_stream handles its own grace period —
                # don't block here, let the queue move to the next challenge
                if winner_task and not winner_task.done():
                    log.info(f"Race: {winning_model} continues in background for deliverables")

                break

    except Exception as e:
        log.error(f"Race error: {e}", exc_info=True)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if flag_sentinel and not flag_sentinel.done():
            flag_sentinel.cancel()
        # NOTE: do NOT unregister here — the caller (queue/_solve_challenge)
        # reads get_result() after race_solvers returns. Caller must unregister.

    if winner_result:
        log.info(f"Race: winner={winning_model} (cost=${winner_result.cost_usd:.4f})")
        from ai.telemetry import ship_metric

        ship_metric("ctf_race_winner", 1, model=winning_model, challenge=challenge_path.name)
    else:
        log.info("Race: no solver found a flag")

    return winner_result


async def _run_with_manager(
    config: Config,
    feed: ManagerFeed,
    challenge_path: Path,
    challenge_meta: dict,
    thread,
    racer_coro,
    corrections_enabled: bool = True,
) -> SolveResult | None:
    """Run a racer coroutine with a manager that dies when the racer finishes."""
    mgr = SolveManager(config, corrections_enabled=corrections_enabled)
    mgr_task = asyncio.create_task(
        mgr.monitor(
            feed,
            challenge_path,
            challenge_meta,
            thread,
            max_interventions=config.manager_max_interventions,
        )
    )
    try:
        return await racer_coro
    finally:
        mgr_task.cancel()
        try:
            await mgr_task
        except (asyncio.CancelledError, Exception):
            pass


async def _run_racer(
    thread: discord.Thread,
    workspace: Path,
    real_challenge_dir: Path,
    prompt: str,
    model: str,
    config: Config,
    timeout: int,
    max_budget: float | None,
    solver_id: int,
    event_callback=None,
    category: str | None = None,
    challenge_id: int | None = None,
    fast: bool = False,
) -> SolveResult | None:
    """Run a single solver in the race. Copies artifacts back on success."""
    model_label = model.split("/")[-1] if "/" in model else model
    log.info(f"Racer {solver_id} ({model_label}): starting in {workspace}")

    try:
        race_prompt = prompt

        # Use bwrap for real isolation — each racer gets its own tmpfs overlay
        # on the challenge dir. Solver sees real paths but writes go to overlay.
        from ai.claude_code import _run_bwrap, _build_mcp_config, _cleanup_mcp_config
        from ai.attack_graph import ToolCallCollector

        tool_collector = ToolCallCollector()
        mcp_config = _build_mcp_config(category, str(real_challenge_dir))
        try:
            result = await _run_bwrap(
                thread,
                str(real_challenge_dir),
                race_prompt,
                timeout=timeout,
                model=model,
                effort=config.autosolve_effort,
                subagent_model="haiku",
                max_budget=max_budget,
                telem_labels={
                    "challenge": real_challenge_dir.name,
                    "model": model_label,
                    "solver_id": str(solver_id),
                },
                event_callback=event_callback,
                mcp_config=mcp_config,
                tool_collector=tool_collector,
                challenge_id=challenge_id,
                solver_id=str(solver_id),
                fast=fast,
            )
        finally:
            _cleanup_mcp_config(mcp_config)

        if result and result.output:
            # bwrap copies artifacts back to real_challenge_dir automatically
            flag_path = real_challenge_dir / "flag.txt"
            flag_found = flag_path.exists() and bool(flag_path.read_text().strip())
            if flag_found:
                await _safe_send(thread, f"**Racer {solver_id} ({model_label}) found a flag!**")
                log.info(f"Racer {solver_id} ({model_label}): flag found!")

            # Only generate attack graph for the winning racer
            if flag_found and tool_collector.nodes:
                from ai.attack_graph import generate_attack_graph

                # Read challenge metadata for graph context
                chall_name = real_challenge_dir.name
                chall_desc = ""
                chall_points = 0
                chall_json = real_challenge_dir / "challenge.json"
                if chall_json.exists():
                    try:
                        _meta = json.loads(chall_json.read_text())
                        chall_name = _meta.get("name", chall_name)
                        chall_desc = _meta.get("description", "")[:500]
                        chall_points = _meta.get("points", 0)
                    except Exception:
                        pass
                asyncio.create_task(
                    generate_attack_graph(
                        challenge_dir=str(real_challenge_dir),
                        solver_output=result.output,
                        collector=tool_collector,
                        thread=thread,
                        config=config,
                        challenge_name=chall_name,
                        category=category or "",
                        points=chall_points,
                        description=chall_desc,
                        model=model_label,
                        flag_found=flag_found,
                        cost_usd=result.cost_usd,
                        num_turns=result.num_turns,
                        duration_ms=result.duration_ms,
                    )
                )

        return result

    except asyncio.CancelledError:
        log.info(f"Racer {solver_id} ({model_label}): cancelled (another solver won)")
        return None
    except Exception as e:
        log.warning(f"Racer {solver_id} ({model_label}): error: {e}")
        return None


async def _run_codex_racer(
    thread: discord.Thread,
    workspace: Path,
    real_challenge_dir: Path,
    prompt: str,
    timeout: int,
    solver_id: int,
    event_callback=None,
    challenge_id: int | None = None,
) -> SolveResult | None:
    """Run Codex as a racer. Copies artifacts back on success."""
    log.info(f"Racer {solver_id} (codex): starting in {workspace}")

    try:
        from ai.codex_solver import solve_with_codex

        result = await solve_with_codex(
            thread,
            str(workspace),
            prompt,
            timeout=timeout,
            telem_labels={
                "challenge": real_challenge_dir.name,
                "solver_id": str(solver_id),
            },
            event_callback=event_callback,
            challenge_id=challenge_id,
        )

        if result and result.output:
            # bwrap inside solve_with_codex copies artifacts back automatically
            flag_path = real_challenge_dir / "flag.txt"
            if flag_path.exists() and flag_path.read_text().strip():
                await _safe_send(thread, f"**Racer {solver_id} (codex) found a flag!**")
                log.info(f"Racer {solver_id} (codex): flag found!")

        return result

    except asyncio.CancelledError:
        log.info(f"Racer {solver_id} (codex): cancelled (another solver won)")
        return None
    except Exception as e:
        log.warning(f"Racer {solver_id} (codex): error: {e}")
        return None
