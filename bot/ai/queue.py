#!/usr/bin/env python3
"""Auto-solve queue — dispatches concurrent Claude Code solvers."""

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path

import discord

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
from db.challenges import ChallengeRecord
from discord_ui.threads import update_thread_status

log = logging.getLogger(__name__)


class SolveQueue:
    """Manages concurrent auto-solving of CTF challenges."""

    def __init__(self, bot, concurrency: int = 10):
        self.bot = bot
        self.concurrency = concurrency
        self.race_mode: bool = False
        self.deep_mode: bool = False
        self.corrections_enabled: bool = True
        self.fast_mode: bool = False
        self.queue: asyncio.Queue[ChallengeRecord] = asyncio.Queue()
        self._background_tasks: set[asyncio.Task] = set()
        self.dependencies: dict[int, list[int]] = {}  # challenge_id -> [prereq_ids]
        self._requeue_count: dict[int, int] = {}  # circuit breaker for blocked challenges
        self._monitor_task: asyncio.Task | None = None
        self.workers: list[asyncio.Task] = []
        self.status: dict[int, str] = {}  # challenge.id -> queued|solving|solved|failed
        self.challenge_names: dict[int, str] = {}  # challenge.id -> name
        self.running = False
        self._status_channel: discord.TextChannel | None = None
        self._dashboard_msg: discord.Message | None = None
        self._dashboard_task: asyncio.Task | None = None
        self._solved_count = 0
        self._failed_count = 0
        self._total_count = 0
        self._start_time: float = 0
        self._recent_solves: list[str] = []
        self._recent_fails: list[str] = []
        self._instance_cache: dict[int, str] = {}  # ctfd_id -> description from instance launch
        self._instance_lock = asyncio.Semaphore(2)  # Max 2 concurrent Playwright browser launches
        self._total_cost: float = 0.0

    async def enqueue(self, challenges: list[ChallengeRecord]):
        """Add challenges to the queue, sorted by points ascending."""
        sorted_challs = sorted(challenges, key=lambda c: c.points)
        for c in sorted_challs:
            if c.id not in self.status:
                self.status[c.id] = "queued"
                self.challenge_names[c.id] = f"{c.name} ({c.points}pt)"
                await self.queue.put(c)
        self._total_count = len(self.status)
        log.info(f"Queue: {self.queue.qsize()} challenges enqueued")

    async def start(self, status_channel: discord.TextChannel | None = None):
        """Spawn worker tasks and begin processing."""
        # If previous run finished but wasn't cleaned up, reset
        if self.running and all(t.done() for t in self.workers):
            log.info("Previous queue run finished, resetting")
            self.running = False
            self.workers = []
            if self._dashboard_task:
                self._dashboard_task.cancel()
                self._dashboard_task = None

        if self.running:
            # Queue is actively running — add new items to the existing run
            log.info(f"Queue running, adding {self.queue.qsize()} new items to existing run")
            self._total_count = len(self.status)
            return

        self.running = True
        self._status_channel = status_channel
        # Reset counters — only count items actually in the queue this run
        self._solved_count = 0
        self._failed_count = 0
        self._total_count = self.queue.qsize()
        # Reset status to only include queued items
        self.status = {cid: s for cid, s in self.status.items() if s == "queued"}
        self._start_time = time.time()
        self._recent_solves = []
        self._recent_fails = []

        # Create the live dashboard message
        if self._status_channel:
            embed = self._build_dashboard_embed()
            self._dashboard_msg = await self._status_channel.send(embed=embed)
            self._dashboard_task = asyncio.create_task(self._dashboard_loop())

        # Spawn workers — fewer in race mode since each challenge spawns N racers
        effective_concurrency = self.concurrency
        if self.race_mode:
            num_racers = len(self.bot.config.race_models) + (1 if self.bot.config.codex_enabled else 0)
            effective_concurrency = max(1, self.concurrency // num_racers)

        # Memory-based worker cap disabled — resource leak fixes (awaiting
        # cancelled tasks, CancelledError handling) address the root cause.
        # Re-enable if running on very constrained machines:
        # try:
        #     with open("/proc/meminfo") as f:
        #         for line in f:
        #             if line.startswith("MemTotal:"):
        #                 total_mb = int(line.split()[1]) / 1024
        #                 solvers_per_worker = num_racers if self.race_mode else 1
        #                 mem_per_worker = solvers_per_worker * 1536
        #                 max_by_mem = max(2, int((total_mb - 2048) / mem_per_worker))
        #                 if max_by_mem < effective_concurrency:
        #                     log.info(f"Queue: capping workers to {max_by_mem}")
        #                     effective_concurrency = max_by_mem
        #                 break
        # except Exception:
        #     pass

        for i in range(min(effective_concurrency, self.queue.qsize())):
            task = asyncio.create_task(self._worker(i + 1))
            self.workers.append(task)

        log.info(f"Queue: started {len(self.workers)} workers")

        # Monitor task: wait for all workers, then clean up
        self._monitor_task = asyncio.create_task(self._monitor_completion())

    async def stop(self):
        """Stop all workers gracefully."""
        self.running = False

        # Stop dashboard refresh
        if self._dashboard_task:
            self._dashboard_task.cancel()
            self._dashboard_task = None

        # Stop monitor task
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._monitor_task
            self._monitor_task = None

        # Drain the queue
        while not self.queue.empty():
            try:
                c = self.queue.get_nowait()
                self.status[c.id] = "cancelled"
            except asyncio.QueueEmpty:
                break

        # Cancel workers
        for task in self.workers:
            task.cancel()

        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)

        self.workers = []

        # Cancel background tasks (analysis, etc.)
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        # Final dashboard update
        await self._update_dashboard()

        log.info("Queue: stopped")

    async def wait(self):
        """Wait for all workers to finish."""
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)

        self.running = False

        if self._dashboard_task:
            self._dashboard_task.cancel()
            self._dashboard_task = None

        # Final dashboard update
        await self._update_dashboard()

    async def _batch_launch_instances(self):
        """Pre-launch all picoCTF instances in a single browser session.

        This avoids multiple workers fighting over Playwright windows.
        Results are cached in self._instance_cache for workers to use.
        """
        # Collect all picoCTF challenges that need instances
        pico_challenges = []
        items = list(self.queue._queue)  # Peek at queue without consuming
        for c in items:
            if detect_platform(c.challenge_dir) == "picoctf":
                pico_challenges.append(c)

        if not pico_challenges:
            return

        log.info(f"Batch-launching {len(pico_challenges)} picoCTF instances...")

        if self._status_channel:
            await self._status_channel.send(f"Launching {len(pico_challenges)} picoCTF instances...")

        try:
            from playwright.async_api import async_playwright

            cookies_file = Path(__file__).parent.parent / "data" / "pico_cookies.json"
            if not cookies_file.exists():
                log.warning("No picoCTF cookies — instances won't be launched")
                return

            import json as json_mod

            raw_cookies = json_mod.loads(cookies_file.read_text())

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=False,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--ozone-platform=x11",  # Force X11 — Wayland under WSLg hides the window
                    ],
                )
                try:
                    context = await browser.new_context(
                        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    )
                    await context.add_cookies(raw_cookies)
                    page = await context.new_page()

                    for i, challenge in enumerate(pico_challenges):
                        try:
                            log.info(f"Launching instance {i+1}/{len(pico_challenges)}: {challenge.name}")

                            await page.goto(
                                f"https://play.picoctf.org/practice/challenge/{challenge.ctfd_id}",
                                wait_until="domcontentloaded",
                            )
                            await asyncio.sleep(2)

                            # Click Launch Instance if it exists
                            launch_btn = await page.query_selector("button:has-text('Launch Instance')")
                            if launch_btn:
                                await launch_btn.click()
                                await asyncio.sleep(3)

                            # Fetch instance data via the browser's session
                            instance_data = await page.evaluate(f"""
                                async () => {{
                                    const r = await fetch('/api/challenges/{challenge.ctfd_id}/instance/');
                                    if (!r.ok) return null;
                                    return await r.json();
                                }}
                            """)

                            if instance_data and instance_data.get("description"):
                                import re as re_mod

                                desc_html = instance_data.get("description", "")
                                text = re_mod.sub(r"<[^>]+>", "", desc_html)
                                text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

                                urls = re_mod.findall(r'href=["\']([^"\']+)["\']', desc_html)
                                endpoints = instance_data.get("endpoints", [])
                                status = instance_data.get("status", "")
                                expires = instance_data.get("expires_in", 0)

                                parts = [text.strip()]
                                if urls:
                                    parts.append(f"\nDownload/service URLs: {', '.join(urls)}")
                                if endpoints:
                                    parts.append(f"\nService endpoints: {json.dumps(endpoints)}")
                                if status:
                                    parts.append(f"\nInstance status: {status} (expires in {expires}s)")

                                desc = "\n".join(parts)
                                self._instance_cache[challenge.ctfd_id] = desc
                                log.info(f"  -> {status}, {len(desc)} chars")

                                # Update challenge.json
                                if challenge.challenge_dir:
                                    meta_path = Path(challenge.challenge_dir) / "challenge.json"
                                    if meta_path.exists():
                                        try:
                                            meta = json.loads(meta_path.read_text())
                                            meta["description"] = desc
                                            meta_path.write_text(json.dumps(meta, indent=2))
                                        except Exception:
                                            pass
                            else:
                                log.warning(f"  -> no instance data for {challenge.name}")

                            # Close the dialog by pressing Escape
                            await page.keyboard.press("Escape")
                            await asyncio.sleep(0.5)

                        except Exception as e:
                            log.warning(f"Failed to launch instance for {challenge.name}: {e}")

                    # Save fresh cookies
                    fresh_cookies = await context.cookies()
                    cookies_file.write_text(json.dumps(fresh_cookies, indent=2))
                finally:
                    await browser.close()

        except Exception as e:
            log.error(f"Batch instance launch failed: {e}", exc_info=True)

        log.info(f"Batch launch complete: {len(self._instance_cache)} instances cached")

    async def _monitor_completion(self):
        """Wait for all workers to finish, then clean up."""
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)

        self.running = False
        if self._dashboard_task:
            self._dashboard_task.cancel()
            self._dashboard_task = None

        # Final dashboard update
        await self._update_dashboard()
        log.info(f"Queue complete: {self.get_status_summary()}")

        # Free memory — clear accumulated state from this run
        self.status.clear()
        self.challenge_names.clear()
        self._instance_cache.clear()
        self.workers.clear()

    async def _worker(self, worker_id: int):
        """Worker loop: pull from queue, solve, report."""
        log.info(f"Worker {worker_id}: started")

        while self.running:
            try:
                challenge = await asyncio.wait_for(self.queue.get(), timeout=5)
            except (TimeoutError, asyncio.QueueEmpty):
                if self.queue.empty():
                    break
                continue

            # Check if dependencies are met
            if challenge.id in self.dependencies:
                prereqs = self.dependencies[challenge.id]
                unmet = [pid for pid in prereqs if self.status.get(pid) != "solved"]
                if unmet:
                    requeues = self._requeue_count.get(challenge.id, 0)
                    if requeues >= 3:
                        log.warning(
                            f"Worker {worker_id}: {challenge.name} — prerequisites unsolved after 3 requeues, attempting anyway"
                        )
                    else:
                        log.info(
                            f"Worker {worker_id}: {challenge.name} blocked by {len(unmet)} unsolved prerequisites, re-queuing"
                        )
                        self.status[challenge.id] = "blocked"
                        self._requeue_count[challenge.id] = requeues + 1
                        await self.queue.put(challenge)
                        self.queue.task_done()
                        await asyncio.sleep(1)  # Avoid tight re-queue loop
                        continue

            log.info(f"Worker {worker_id}: solving {challenge.name} ({challenge.points}pts)")
            self.status[challenge.id] = "solving"

            try:
                solved = await self._solve_challenge(challenge)
                name = self.challenge_names.get(challenge.id, challenge.name)
                if solved is None:
                    # Skipped (stale thread, missing data) — don't count as failed
                    self.status[challenge.id] = "skipped"
                    self._total_count -= 1  # Don't count toward total
                elif solved:
                    self.status[challenge.id] = "solved"
                    self._solved_count += 1
                    self._recent_solves.append(name)
                    self._recent_solves = self._recent_solves[-10:]
                else:
                    self.status[challenge.id] = "failed"
                    self._failed_count += 1
                    self._recent_fails.append(name)
                    self._recent_fails = self._recent_fails[-10:]
            except Exception as e:
                log.error(f"Worker {worker_id}: error on {challenge.name}: {e}", exc_info=True)
                from ai.telemetry import ship_log

                ship_log(
                    "agent.error",
                    challenge=challenge.name,
                    category=challenge.category,
                    error=str(e)[:300],
                )
                self.status[challenge.id] = "failed"
                self._failed_count += 1
                self._recent_fails.append(self.challenge_names.get(challenge.id, challenge.name))
                self._recent_fails = self._recent_fails[-10:]

            self.queue.task_done()

        log.info(f"Worker {worker_id}: done")

    async def _solve_challenge(self, challenge: ChallengeRecord) -> bool:
        """Full solve flow for one challenge. Returns True if flag was found."""
        # Skip categories that can't be solved remotely (badge/hardware)
        from ai.playbooks import SKIP_CATEGORIES

        if challenge.category.lower() in SKIP_CATEGORIES:
            log.info(f"Skipping {challenge.name} — {challenge.category} category not solvable remotely")
            return None

        # Find the Discord thread
        thread = None
        if challenge.thread_id:
            try:
                thread = await self.bot.fetch_channel(int(challenge.thread_id))
            except Exception:
                log.warning(f"Could not find thread for {challenge.name}")

        if not thread:
            log.warning(f"No thread for {challenge.name}, skipping (stale thread ID)")
            return None  # None = skipped (not counted as failed)

        # Get description — launch instance if picoCTF
        description = challenge.description or ""
        is_pico = detect_platform(challenge.challenge_dir) == "picoctf"

        if challenge.ctfd_id in self._instance_cache:
            description = self._instance_cache[challenge.ctfd_id]
        elif is_pico and challenge.challenge_dir:
            # Throttle Playwright launches to avoid blocking the event loop
            async with self._instance_lock:
                if challenge.ctfd_id in self._instance_cache:
                    description = self._instance_cache[challenge.ctfd_id]
                else:
                    try:
                        instance_info = await launch_pico_instance(challenge.ctfd_id, challenge.challenge_dir)
                        if instance_info:
                            description = instance_info
                            self._instance_cache[challenge.ctfd_id] = instance_info
                    except Exception as e:
                        log.warning(f"Instance launch failed for {challenge.name}: {e}")

        # Build prompt
        flag_format = detect_flag_format(challenge.challenge_dir)
        prompt = build_solve_prompt(challenge, description, self.bot.config.ctf_root, flag_format)

        # Write flag submission script for mid-solve testing
        platform = "picoctf" if is_pico else "ctfd"
        if challenge.challenge_dir:
            submit_url = getattr(self.bot, "file_server_base_url", "http://localhost:8080")
            token = getattr(self.bot, "file_server_token", "")
            write_submit_script(
                challenge.challenge_dir,
                challenge.ctfd_id,
                platform,
                submit_url=submit_url,
                solver_id="queue",
                token=token,
            )
            if platform == "picoctf":
                write_restart_script(
                    challenge.challenge_dir,
                    challenge.ctfd_id,
                    restart_url=submit_url,
                    token=token,
                )

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

        # Calculate dynamic timeout and budget based on points
        cfg = self.bot.config
        timeout = min(
            cfg.autosolve_timeout_base + challenge.points * cfg.autosolve_timeout_per_point,
            cfg.autosolve_timeout_max,
        )
        # Budget: 0 = unlimited (subscription mode), >0 = per-challenge cap
        if cfg.autosolve_max_budget > 0:
            max_budget = min(
                0.25 + challenge.points * 0.005,
                cfg.autosolve_max_budget,
            )
        else:
            max_budget = None

        # Start trace
        from ai.tracer import SolveTracer

        tracer = SolveTracer(challenge.challenge_dir or ".")
        tracer.solve_start(
            challenge.name,
            challenge.category,
            challenge.points,
            model=cfg.autosolve_model,
            effort=cfg.autosolve_effort,
            budget=max_budget,
        )

        # Dispatch solver
        model = cfg.autosolve_model
        effort = cfg.autosolve_effort
        subagent = cfg.autosolve_subagent
        budget_str = f"${max_budget:.2f}" if max_budget else "unlimited"

        if self.deep_mode:
            from ai.deep_solve import deep_solve
            from ai.manager import SolveManager
            from ai.manager_feed import ManagerFeed

            log.info(
                f"Deep solving {challenge.name} "
                f"(model={model}, effort={effort}, timeout={timeout}s, budget={budget_str})"
            )

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
                    with contextlib.suppress(Exception):
                        challenge_meta["files"] = json.loads(cj.read_text()).get("files", [])
            mgr = SolveManager(cfg, corrections_enabled=self.corrections_enabled)
            mgr_task = asyncio.create_task(
                mgr.monitor(
                    feed,
                    challenge.challenge_dir or ".",
                    challenge_meta,
                    thread,
                    max_interventions=cfg.manager_max_interventions,
                )
            )

            try:
                result = await deep_solve(
                    thread=thread,
                    challenge_dir=Path(challenge.challenge_dir or "."),
                    prompt=prompt,
                    config=cfg,
                    solver_timeout=timeout,
                    model=model,
                    effort=effort,
                    subagent_model=subagent,
                    max_budget=max_budget,
                    event_callback=feed.push,
                    category=challenge.category,
                    challenge_id=challenge.ctfd_id,
                    fast=self.fast_mode,
                )
            finally:
                mgr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await mgr_task
        elif self.race_mode:
            from ai.race import race_solvers

            race_models = cfg.race_models
            log.info(f"Racing {challenge.name} with {race_models} " f"(timeout={timeout}s, budget={budget_str})")
            # Race mode handles its own manager per racer
            result = await race_solvers(
                thread,
                challenge.challenge_dir or ".",
                prompt,
                models=race_models,
                config=cfg,
                timeout=timeout,
                max_budget=max_budget,
                challenge_id=challenge.ctfd_id,
                corrections_enabled=self.corrections_enabled,
                fast=self.fast_mode,
            )
        else:
            log.info(
                f"Dispatching {challenge.name} to Claude Code "
                f"(model={model}, effort={effort}, timeout={timeout}s, budget={budget_str})"
            )

            # Start manager for this solve
            from ai.manager import SolveManager
            from ai.manager_feed import ManagerFeed

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
                    with contextlib.suppress(Exception):
                        challenge_meta["files"] = json.loads(cj.read_text()).get("files", [])
            mgr = SolveManager(cfg, corrections_enabled=self.corrections_enabled)
            mgr_task = asyncio.create_task(
                mgr.monitor(
                    feed,
                    challenge.challenge_dir or ".",
                    challenge_meta,
                    thread,
                    max_interventions=cfg.manager_max_interventions,
                )
            )

            try:
                if model == "codex":
                    from ai.codex_solver import solve_with_codex

                    result = await solve_with_codex(
                        thread,
                        challenge.challenge_dir or ".",
                        prompt,
                        timeout=timeout,
                        event_callback=feed.push,
                        challenge_id=challenge.ctfd_id,
                    )
                else:
                    result = await solve_with_claude_code(
                        thread,
                        challenge.challenge_dir or ".",
                        prompt,
                        model=model,
                        effort=effort,
                        subagent_model=subagent,
                        timeout=timeout,
                        max_budget=max_budget,
                        event_callback=feed.push,
                        category=challenge.category,
                        challenge_id=challenge.ctfd_id,
                        fast=self.fast_mode,
                    )
            finally:
                mgr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await mgr_task

        # Check flag status — prefer flag event (authoritative), fall back to flag.txt
        # Read and stash result THEN unregister — race loop left it for us to read
        from ai.flag_events import get_result as _get_flag_result
        from ai.flag_events import unregister as _unregister_flag

        flag_result = _get_flag_result(challenge.ctfd_id) if challenge.ctfd_id else None
        if challenge.ctfd_id:
            _unregister_flag(challenge.ctfd_id)

        # Track cost + trace
        if flag_result:
            flag_found_check = True
        else:
            flag_path_check = Path(challenge.challenge_dir or ".") / "flag.txt"
            flag_found_check = (
                flag_path_check.exists() and bool(flag_path_check.read_text().strip())
                if challenge.challenge_dir
                else False
            )
        if result:
            self._total_cost += result.cost_usd
            log.info(f"Solve cost for {challenge.name}: ${result.cost_usd:.4f} (total: ${self._total_cost:.2f})")
            tracer.solve_complete(result.cost_usd, result.num_turns, result.duration_ms, flag_found_check)
        else:
            # Race/solve returned None — still log completion
            tracer.solve_complete(0, 0, 0, flag_found_check)

        # Generate attack graph in background
        if result and getattr(result, "tool_collector", None) and result.tool_collector.nodes:
            from ai.attack_graph import generate_attack_graph

            task = asyncio.create_task(
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
                    model=model,
                    flag_found=flag_found_check,
                    cost_usd=result.cost_usd,
                    num_turns=result.num_turns,
                    duration_ms=result.duration_ms,
                )
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        # Write progress file
        flag_found = flag_found_check
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

        # Learn from the attempt (update category patterns)
        learn_from_challenge(
            challenge.challenge_dir or ".",
            self.bot.config.ctf_root,
            cost_usd=result.cost_usd if result else 0,
            num_turns=result.num_turns if result else 0,
            duration_ms=result.duration_ms if result else 0,
            model=model,
        )

        # Check for missing tools
        await check_missing_tools(
            challenge.challenge_dir or ".",
            thread,
            self.bot.config.allowed_user_ids,
        )

        # Check for flag and handle result
        if flag_found_check:
            if flag_result:
                # Flag already confirmed via /submit callback — just mark solved
                from db.challenges import mark_solved

                mark_solved(self.bot.db, challenge.id, flag_result.flag)
                await thread.send(f"**CORRECT!** `{flag_result.flag}`")
                if isinstance(thread, discord.Thread):
                    await update_thread_status(thread, "solved")
                return True
            else:
                # flag.txt fallback — submit via platform API
                result = await try_auto_submit(
                    thread,
                    challenge,
                    self.bot.db,
                    self.bot.config.allowed_user_ids,
                    config=self.bot.config,
                )
                return result
        else:
            # Analyze failure in background (don't slow down the queue)
            if result and result.output:
                from ai.trace_analyzer import analyze_and_post

                task = asyncio.create_task(
                    analyze_and_post(
                        thread,
                        result.output,
                        challenge.name,
                        challenge.category,
                        challenge.points,
                        challenge.description or "",
                        challenge.challenge_dir or ".",
                        self.bot.config,
                    )
                )
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

            # No flag — ping users
            pings = (
                " ".join(f"<@{uid}>" for uid in self.bot.config.allowed_user_ids)
                if self.bot.config.allowed_user_ids
                else ""
            )
            await thread.send(
                f"{pings} Auto-solve finished but no flag found for **{challenge.name}**. "
                f"Try `/solve` with an approach hint.",
                silent=False,
            )
            if isinstance(thread, discord.Thread):
                await update_thread_status(thread, "needs_help")
            return False

    async def _dashboard_loop(self):
        """Background task that refreshes the dashboard embed every 15 seconds."""
        try:
            while self.running:
                await asyncio.sleep(15)
                await self._update_dashboard()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(f"Dashboard loop error: {e}")

    async def _update_dashboard(self):
        """Edit the dashboard message with current status."""
        if not self._dashboard_msg:
            return
        try:
            embed = self._build_dashboard_embed()
            await self._dashboard_msg.edit(embed=embed)
        except Exception as e:
            log.warning(f"Dashboard update failed: {e}")

    def _build_dashboard_embed(self) -> discord.Embed:
        """Build the live dashboard embed."""
        solving = sum(1 for s in self.status.values() if s == "solving")
        queued = sum(1 for s in self.status.values() if s == "queued")
        completed = self._solved_count + self._failed_count
        elapsed = time.time() - self._start_time if self._start_time else 0

        # Color: green if running and solving, yellow if queued, gray if done
        mode = "Deep Analysis" if self.deep_mode else ("Race" if self.race_mode else "Auto-Solve")
        if not self.running:
            color = 0x95A5A6  # Gray — done
            title = f"{mode} Complete"
        elif solving > 0:
            color = 0x2ECC71  # Green — actively solving
            title = f"{mode} In Progress"
        else:
            color = 0xF39C12  # Yellow — starting up
            title = f"{mode} Starting"

        embed = discord.Embed(title=title, color=color)

        # Progress bar
        if self._total_count > 0:
            pct = completed / self._total_count
            bar_len = 20
            filled = int(bar_len * pct)
            bar = "=" * filled + "-" * (bar_len - filled)
            embed.description = f"`[{bar}]` {completed}/{self._total_count} ({pct:.0%})"

        # Stats
        embed.add_field(name="Solved", value=str(self._solved_count), inline=True)
        embed.add_field(name="Failed", value=str(self._failed_count), inline=True)
        embed.add_field(name="In Progress", value=str(solving), inline=True)
        blocked = sum(1 for s in self.status.values() if s == "blocked")
        embed.add_field(name="Queued", value=str(queued), inline=True)
        if blocked > 0:
            embed.add_field(name="Blocked", value=str(blocked), inline=True)
        embed.add_field(name="Total Cost", value=f"${self._total_cost:.2f}", inline=True)
        if completed > 0:
            embed.add_field(
                name="Avg Cost",
                value=f"${self._total_cost / completed:.3f}",
                inline=True,
            )

        # Elapsed time
        if elapsed > 0:
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            embed.add_field(name="Elapsed", value=f"{mins}m {secs}s", inline=True)

            # Rate
            if completed > 0:
                rate = elapsed / completed
                embed.add_field(name="Avg/challenge", value=f"{rate:.0f}s", inline=True)

        # Currently solving
        in_progress = [self.challenge_names.get(cid, f"#{cid}") for cid, s in self.status.items() if s == "solving"]
        if in_progress:
            embed.add_field(
                name="Currently Solving",
                value="\n".join(f"- {name}" for name in in_progress[:10]),
                inline=False,
            )

        # Recent solves
        if self._recent_solves:
            embed.add_field(
                name="Recent Solves",
                value="\n".join(f"- {name}" for name in self._recent_solves[-5:]),
                inline=False,
            )

        # Recent fails
        if self._recent_fails:
            embed.add_field(
                name="Recent Fails",
                value="\n".join(f"- {name}" for name in self._recent_fails[-5:]),
                inline=False,
            )

        embed.set_footer(text="Updates every 15s | /autosolve status for details")

        # Ship queue metrics to VictoriaMetrics
        from ai.telemetry import ship_metric

        ship_metric("ctf_queue_depth", float(solving), status="solving")
        ship_metric("ctf_queue_depth", float(queued), status="queued")
        ship_metric("ctf_queue_depth", float(self._solved_count), status="solved")
        ship_metric("ctf_queue_depth", float(self._failed_count), status="failed")
        ship_metric("ctf_active_solvers", float(solving))
        if self._total_cost > 0:
            ship_metric("ctf_solve_cost_usd_total", self._total_cost)
        return embed

    def get_status_summary(self) -> str:
        """One-line status summary."""
        solving = sum(1 for s in self.status.values() if s == "solving")
        queued = sum(1 for s in self.status.values() if s == "queued")
        return (
            f"Solved: {self._solved_count}/{self._total_count} | "
            f"In progress: {solving} | "
            f"Failed: {self._failed_count} | "
            f"Queued: {queued}"
        )

    def get_status_dict(self) -> dict:
        """Full status for embeds."""
        return {
            "total": self._total_count,
            "solved": self._solved_count,
            "failed": self._failed_count,
            "solving": sum(1 for s in self.status.values() if s == "solving"),
            "queued": sum(1 for s in self.status.values() if s == "queued"),
            "running": self.running,
        }
