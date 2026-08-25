#!/usr/bin/env python3
"""Solve command — trigger AI solving in a challenge thread."""

import asyncio
import json
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from ai.claude_code import solve_with_claude_code
from ai.learner import learn_from_challenge
from ai.solve_utils import (
    build_solve_prompt,
    check_missing_tools,
    detect_flag_format,
    detect_platform,
    launch_pico_instance,
    try_auto_submit,
    write_progress_file,
    write_restart_script,
    write_submit_script,
)
from db.challenges import get_by_thread
from discord_ui.threads import update_thread_status

log = logging.getLogger(__name__)


class SolveCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="solve", description="Have the AI attempt to solve this challenge")
    @app_commands.describe(
        approach="Hint for which technique to try (optional)",
        use_hints="Include platform hints (may cost points on some CTFs)",
        model="Claude model to use (default: from config)",
        effort="Thinking effort level",
        related="Name of a related challenge to use as context (e.g. 'mus1c' when solving '1_wanna_b3_a_r0ck5tar')",
        race="Race multiple models simultaneously (haiku vs opus)",
        deep="Run deep analysis mode (teardown source + infra before solving)",
        timeout="Solver timeout in seconds (0 = no limit, default 600)",
        manager="Enable manager corrections (default: from config, set False to disable)",
        fast="Enable fast mode (faster output, higher cost)",
    )
    @app_commands.choices(
        model=[
            app_commands.Choice(name="Haiku (fast)", value="haiku"),
            app_commands.Choice(name="Sonnet (balanced)", value="sonnet"),
            app_commands.Choice(name="Opus (strongest)", value="opus"),
            app_commands.Choice(name="Codex (OpenAI)", value="codex"),
        ],
        effort=[
            app_commands.Choice(name="Low", value="low"),
            app_commands.Choice(name="Medium", value="medium"),
            app_commands.Choice(name="High (default)", value="high"),
            app_commands.Choice(name="Max", value="max"),
        ],
    )
    async def solve(
        self,
        interaction: discord.Interaction,
        approach: str | None = None,
        use_hints: bool = False,
        model: str | None = None,
        effort: str | None = None,
        related: str | None = None,
        race: bool = False,
        deep: bool = False,
        timeout: int | None = None,
        manager: bool | None = None,
        fast: bool | None = None,
    ):
        if not self._is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("Use this command in a challenge thread.", ephemeral=True)
            return

        thread = interaction.channel
        challenge = get_by_thread(self.bot.db, str(thread.id))
        if not challenge:
            await interaction.response.send_message("This thread isn't linked to a challenge.", ephemeral=True)
            return

        if challenge.solved:
            await interaction.response.send_message(f"Already solved: `{challenge.flag}`", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        # Get description — launch instance if picoCTF
        description = challenge.description or ""
        platform = detect_platform(challenge.challenge_dir)

        if platform == "picoctf" and challenge.challenge_dir:
            await thread.send("Launching picoCTF instance...")
            try:
                instance_info = await launch_pico_instance(
                    challenge.ctfd_id,
                    challenge.challenge_dir,
                    include_hints=use_hints,
                )
                if instance_info:
                    description = instance_info
            except Exception as e:
                log.warning(f"Instance launch failed: {e}")

        # Generate retry advice if prior attempts exist and no manual approach given
        if not approach and challenge.challenge_dir:
            progress_file = Path(challenge.challenge_dir) / "progress.md"
            if progress_file.exists():
                from ai.advisor import generate_retry_advice

                advice = await generate_retry_advice(challenge.challenge_dir, self.bot.config)
                if advice:
                    approach = advice
                    await thread.send(f"**Retry advice from prior attempts:**\n{advice[:500]}")

        # Build prompt
        flag_format = detect_flag_format(challenge.challenge_dir)
        prompt = build_solve_prompt(
            challenge,
            description,
            self.bot.config.ctf_root,
            flag_format,
            approach,
        )

        # Inject related challenge context if specified
        if related:
            related_ctx = self._get_related_context(related)
            if related_ctx:
                prompt += related_ctx
                await thread.send(f"Linked context from: **{related}**")
            else:
                await thread.send(f"Could not find challenge '{related}' — solving without it.")

        # Update challenge.json with fresh description
        if challenge.challenge_dir and description:
            meta_path = Path(challenge.challenge_dir) / "challenge.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    meta["description"] = description
                    meta_path.write_text(json.dumps(meta, indent=2))
                except Exception:
                    pass

        # Write the flag submission script so Claude Code can test flags mid-solve
        platform = detect_platform(challenge.challenge_dir)
        if challenge.challenge_dir:
            submit_url = getattr(self.bot, "file_server_base_url", "http://localhost:8080")
            write_submit_script(
                challenge.challenge_dir,
                challenge.ctfd_id,
                platform,
                submit_url=submit_url,
                solver_id=model or "solve",
            )
            if platform == "picoctf":
                write_restart_script(
                    challenge.challenge_dir,
                    challenge.ctfd_id,
                    restart_url=submit_url,
                )

        # Default model/effort from config if not specified
        model = model or self.bot.config.autosolve_model
        effort = effort or self.bot.config.autosolve_effort
        # 0 = no limit (very large value), None = default 600s
        log.info(f"Timeout parameter received: {timeout!r} (type={type(timeout).__name__})")
        if timeout is not None and timeout > 0:
            solve_timeout = timeout
        elif timeout == 0:
            solve_timeout = 86400  # 24h — effectively no limit
        else:
            solve_timeout = 600

        corrections_enabled = manager if manager is not None else self.bot.config.manager_corrections
        fast_enabled = fast if fast is not None else self.bot.config.fast_mode

        log.info(
            f"Solving {challenge.name} (model={model}, effort={effort}, timeout={solve_timeout}s, fast={fast_enabled})"
        )

        # Start trace
        from ai.tracer import SolveTracer

        tracer = SolveTracer(challenge.challenge_dir or ".")
        tracer.solve_start(
            challenge.name,
            challenge.category,
            challenge.points,
            model=model,
            effort=effort,
        )

        try:
            max_budget = self.bot.config.autosolve_max_budget * 2

            if deep and race:
                await thread.send("Deep mode and race mode are incompatible. Using deep mode.")
                race = False

            if deep:
                from ai.deep_solve import deep_solve
                from ai.manager_feed import ManagerFeed
                from ai.manager import SolveManager

                feed = ManagerFeed()
                challenge_meta = {
                    "name": challenge.name,
                    "category": challenge.category,
                    "points": challenge.points,
                    "files": [],
                    "description": challenge.description or "",
                    "challenge_dir": challenge.challenge_dir or "",
                }
                if challenge.challenge_dir:
                    cj = Path(challenge.challenge_dir) / "challenge.json"
                    if cj.exists():
                        try:
                            challenge_meta["files"] = json.loads(cj.read_text()).get("files", [])
                        except Exception:
                            pass
                mgr = SolveManager(self.bot.config, corrections_enabled=corrections_enabled)
                mgr_task = asyncio.create_task(
                    mgr.monitor(
                        feed,
                        challenge.challenge_dir or ".",
                        challenge_meta,
                        thread,
                        max_interventions=self.bot.config.manager_max_interventions,
                    )
                )

                try:
                    result = await deep_solve(
                        thread=thread,
                        challenge_dir=Path(challenge.challenge_dir or "."),
                        prompt=prompt,
                        config=self.bot.config,
                        solver_timeout=solve_timeout,
                        model=model,
                        effort=effort,
                        subagent_model="haiku" if model != "opus" else None,
                        max_budget=max_budget,
                        event_callback=feed.push,
                        category=challenge.category,
                        challenge_id=challenge.ctfd_id,
                        fast=fast_enabled,
                    )
                finally:
                    mgr_task.cancel()
                    try:
                        await mgr_task
                    except (asyncio.CancelledError, Exception):
                        pass
            elif race:
                # Race multiple models
                from ai.race import race_solvers

                race_models = self.bot.config.race_models
                result = await race_solvers(
                    thread,
                    challenge.challenge_dir or ".",
                    prompt,
                    models=race_models,
                    config=self.bot.config,
                    timeout=solve_timeout,
                    max_budget=max_budget,
                    challenge_id=challenge.ctfd_id,
                    corrections_enabled=corrections_enabled,
                    fast=fast_enabled,
                )
            else:
                # Single solver
                from ai.manager_feed import ManagerFeed
                from ai.manager import SolveManager

                feed = ManagerFeed()
                challenge_meta = {
                    "name": challenge.name,
                    "category": challenge.category,
                    "points": challenge.points,
                    "files": [],
                    "description": challenge.description or "",
                    "challenge_dir": challenge.challenge_dir or "",
                }
                if challenge.challenge_dir:
                    cj = Path(challenge.challenge_dir) / "challenge.json"
                    if cj.exists():
                        try:
                            challenge_meta["files"] = json.loads(cj.read_text()).get("files", [])
                        except Exception:
                            pass
                mgr = SolveManager(self.bot.config, corrections_enabled=corrections_enabled)
                mgr_task = asyncio.create_task(
                    mgr.monitor(
                        feed,
                        challenge.challenge_dir or ".",
                        challenge_meta,
                        thread,
                        max_interventions=self.bot.config.manager_max_interventions,
                    )
                )

                try:
                    if model == "codex":
                        from ai.codex_solver import solve_with_codex

                        result = await solve_with_codex(
                            thread,
                            challenge.challenge_dir or ".",
                            prompt,
                            timeout=solve_timeout,
                            event_callback=feed.push,
                            challenge_id=challenge.ctfd_id,
                        )
                    else:
                        subagent = "haiku" if model != "opus" else None
                        result = await solve_with_claude_code(
                            thread,
                            challenge.challenge_dir or ".",
                            prompt,
                            timeout=solve_timeout,
                            model=model,
                            effort=effort,
                            subagent_model=subagent,
                            max_budget=max_budget,
                            event_callback=feed.push,
                            category=challenge.category,
                            challenge_id=challenge.ctfd_id,
                            fast=fast_enabled,
                        )
                finally:
                    mgr_task.cancel()
                    try:
                        await mgr_task
                    except (asyncio.CancelledError, Exception):
                        pass

            # Write progress file with cost data
            # Read flag result then unregister — _process_stream left it for us in race mode
            from ai.flag_events import get_result as _get_flag_result, unregister as _unregister_flag

            flag_result = _get_flag_result(challenge.ctfd_id) if challenge.ctfd_id else None
            if challenge.ctfd_id:
                _unregister_flag(challenge.ctfd_id)
            if flag_result:
                flag_found = True
            else:
                flag_path = Path(challenge.challenge_dir or ".") / "flag.txt"
                flag_found = flag_path.exists() and bool(flag_path.read_text().strip())
            if result:
                write_progress_file(
                    challenge.challenge_dir or ".",
                    challenge.name,
                    result.output,
                    result.cost_usd,
                    result.num_turns,
                    result.duration_ms,
                    flag_found,
                )
                tracer.solve_complete(result.cost_usd, result.num_turns, result.duration_ms, flag_found)
                log.info(f"Solve cost: ${result.cost_usd:.4f}, turns={result.num_turns}")

                # Generate attack graph in background
                if result.tool_collector and result.tool_collector.nodes:
                    from ai.attack_graph import generate_attack_graph

                    asyncio.create_task(
                        generate_attack_graph(
                            challenge_dir=challenge.challenge_dir or ".",
                            solver_output=result.output,
                            collector=result.tool_collector,
                            thread=thread,
                            config=self.bot.config,
                            challenge_name=challenge.name,
                            category=challenge.category,
                            points=challenge.points,
                            description=challenge.description or "",
                            model=model or "",
                            flag_found=flag_found,
                            cost_usd=result.cost_usd,
                            num_turns=result.num_turns,
                            duration_ms=result.duration_ms,
                        )
                    )

            from ai.telemetry import ship_log as _ship_log, ship_metric as _ship_metric

            if flag_found:
                if flag_result:
                    # Already confirmed via /submit callback — mark solved
                    from db.challenges import mark_solved

                    mark_solved(self.bot.db, challenge.id, flag_result.flag)
                    await thread.send(f"**CORRECT!** `{flag_result.flag}`")
                    if isinstance(thread, discord.Thread):
                        await update_thread_status(thread, "solved")
                    _ship_log(
                        "solve.flag_confirmed",
                        challenge=challenge.name,
                        category=challenge.category,
                        points=challenge.points,
                        flag=flag_result.flag,
                        model=model or "",
                    )
                    _ship_metric("ctf_solves_total", 1, challenge=challenge.name, category=challenge.category)
                else:
                    await try_auto_submit(
                        thread,
                        challenge,
                        self.bot.db,
                        self.bot.config.allowed_user_ids,
                        config=self.bot.config,
                    )
            else:
                _ship_log(
                    "solve.no_flag",
                    challenge=challenge.name,
                    category=challenge.category,
                    points=challenge.points,
                    model=model or "",
                    cost_usd=result.cost_usd if result else 0,
                    num_turns=result.num_turns if result else 0,
                )
                # Analyze the failure trace
                if result and result.output:
                    from ai.trace_analyzer import analyze_and_post

                    await analyze_and_post(
                        thread,
                        result.output,
                        challenge.name,
                        challenge.category,
                        challenge.points,
                        challenge.description or "",
                        challenge.challenge_dir or ".",
                        self.bot.config,
                    )

                pings = (
                    " ".join(f"<@{uid}>" for uid in self.bot.config.allowed_user_ids)
                    if self.bot.config.allowed_user_ids
                    else ""
                )
                await thread.send(
                    f"{pings} Solve attempt finished but no flag found. "
                    f"Try `/solve` with an approach hint or `/ask`.",
                    silent=False,
                )
                await update_thread_status(thread, "needs_help")
            # Check for missing tools
            await check_missing_tools(
                challenge.challenge_dir or ".",
                thread,
                self.bot.config.allowed_user_ids,
            )
            # Learn from the attempt (update category patterns)
            learn_from_challenge(
                challenge.challenge_dir or ".",
                self.bot.config.ctf_root,
                cost_usd=result.cost_usd if result else 0,
                num_turns=result.num_turns if result else 0,
                duration_ms=result.duration_ms if result else 0,
                model=model or "",
            )
            await interaction.followup.send("Solve attempt complete.")
        except Exception as e:
            log.error(f"Solve failed: {e}", exc_info=True)
            await interaction.followup.send(f"Solve failed: {e}")

    def _get_related_context(self, name: str) -> str:
        """Find a challenge by name and return its README as context."""
        from discord_ui.threads import slugify

        challenges_dir = self.bot.config.ctf_root / "challenges"
        if not challenges_dir.exists():
            return ""

        slug = slugify(name)

        # Search all category dirs for a matching slug
        for cat_dir in challenges_dir.iterdir():
            if not cat_dir.is_dir():
                continue
            # Try exact slug match
            candidate = cat_dir / slug
            if candidate.is_dir():
                return self._read_challenge_context(candidate, name)
            # Try fuzzy — slug contained in dir name or vice versa
            for chall_dir in cat_dir.iterdir():
                if not chall_dir.is_dir():
                    continue
                if slug in chall_dir.name or chall_dir.name in slug:
                    return self._read_challenge_context(chall_dir, name)

        return ""

    def _read_challenge_context(self, chall_dir: Path, label: str) -> str:
        """Read a challenge's README and flag for use as related context."""
        parts = [f'\n\nRELATED CHALLENGE CONTEXT — "{label}" ({chall_dir.name}):\n']

        readme = chall_dir / "README.md"
        if readme.exists():
            content = readme.read_text()[:3000]
            parts.append(content)
        else:
            # No README — try challenge.json description
            meta = chall_dir / "challenge.json"
            if meta.exists():
                try:
                    data = json.loads(meta.read_text())
                    parts.append(f"Description: {data.get('description', 'N/A')}")
                except Exception:
                    pass

        flag = chall_dir / "flag.txt"
        if flag.exists():
            parts.append(f"\nFlag: {flag.read_text().strip()}")

        solve = chall_dir / "solve.py"
        if solve.exists():
            content = solve.read_text()[:2000]
            parts.append(f"\nSolve script:\n```python\n{content}\n```")

        parts.append("\n--- END RELATED CONTEXT ---")
        return "\n".join(parts)

    def _is_authorized(self, interaction: discord.Interaction) -> bool:
        allowed = self.bot.config.allowed_user_ids
        if not allowed:
            return True
        return interaction.user.id in allowed


async def setup(bot: commands.Bot):
    await bot.add_cog(SolveCog(bot))
