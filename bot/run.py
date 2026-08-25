#!/usr/bin/env python3
"""CTF Bot entry point."""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import discord
from discord.ext import commands

from config import Config
from db.schema import init_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ctfbot")

# Cog modules to load
COGS = [
    "commands.scout",
    "commands.solve",
    "commands.ask",
    "commands.submit",
    "commands.status",
    "commands.autosolve",
    "commands.hint",
    "commands.interact",
    "commands.learn",
    "commands.reset",
    "events.messages",
]


class CTFBot(commands.Bot):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.guild_messages = True

        super().__init__(
            command_prefix="!",  # Slash commands are primary, prefix as fallback
            intents=intents,
        )
        self.config = config
        self.db = None

    async def setup_hook(self):
        """Called when the bot is starting up."""
        # Initialize database
        db_path = Path(__file__).parent / "data" / "ctfbot.db"
        self.db = init_db(db_path)
        log.info(f"Database initialized at {db_path}")

        # Start challenge file server
        self._file_server_runner = None
        if self.config.file_server_port:
            from aiohttp import web

            app = web.Application()
            app["bot"] = self

            async def handle_submit(request):
                """Flag submission proxy — solvers POST here instead of using cookies directly."""
                try:
                    data = await request.json()
                    challenge_id = data.get("challenge_id")
                    flag = data.get("flag", "").strip()
                    platform = data.get("platform", "ctfd")
                    solver_id = data.get("solver_id", "")
                    if not challenge_id or not flag:
                        return web.json_response({"error": "missing challenge_id or flag"}, status=400)

                    if platform == "picoctf":
                        from platforms.picoctf import PicoCTFPlatform

                        pico = PicoCTFPlatform()
                        result = await pico.submit_flag(int(challenge_id), flag)
                        await pico.close()
                        if result.status in ("correct", "already_solved"):
                            from ai.flag_events import notify

                            notify(int(challenge_id), flag=flag, solver_id=solver_id)
                        return web.json_response({"status": result.status, "message": result.message})
                    elif platform == "ctfd":
                        if not self.config.ctfd_url or not (self.config.ctfd_token or self.config.ctfd_session):
                            return web.json_response(
                                {"error": "CTFD_URL and CTFD_TOKEN/CTFD_SESSION not configured"}, status=400
                            )
                        from ctfd.client import CTFdClient

                        client = CTFdClient(
                            self.config.ctfd_url, token=self.config.ctfd_token, session=self.config.ctfd_session
                        )
                        try:
                            result = await client.submit_flag(int(challenge_id), flag)
                        finally:
                            await client.close()
                        if result.status in ("correct", "already_solved"):
                            from ai.flag_events import notify

                            notify(int(challenge_id), flag=flag, solver_id=solver_id)
                        return web.json_response({"status": result.status, "message": result.message})
                    else:
                        return web.json_response({"error": f"unsupported platform: {platform}"}, status=400)
                except Exception as e:
                    return web.json_response({"error": str(e)}, status=500)

            async def handle_restart_instance(request):
                """Restart a picoCTF challenge instance — solvers call this when instance expires."""
                try:
                    data = await request.json()
                    challenge_id = data.get("challenge_id")
                    challenge_dir = data.get("challenge_dir", ".")
                    if not challenge_id:
                        return web.json_response({"error": "missing challenge_id"}, status=400)

                    from ai.solve_utils import launch_pico_instance

                    desc = await launch_pico_instance(int(challenge_id), challenge_dir)
                    if desc:
                        return web.json_response({"status": "ok", "description": desc})
                    else:
                        return web.json_response(
                            {"status": "error", "message": "failed to launch instance"},
                            status=500,
                        )
                except Exception as e:
                    return web.json_response({"error": str(e)}, status=500)

            app.router.add_post("/submit", handle_submit)
            app.router.add_post("/restart-instance", handle_restart_instance)
            challenges_dir = self.config.ctf_root / "challenges"
            challenges_dir.mkdir(parents=True, exist_ok=True)
            app.router.add_static("/", challenges_dir, show_index=True)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", self.config.file_server_port, reuse_address=True)
            await site.start()
            self._file_server_runner = runner

            # Resolve the base URL — reuse TTYD_HOST / tailscale auto-detection
            import os as _os

            host = _os.environ.get("TTYD_HOST", "")
            if not host:
                from commands.interact import _get_host

                host = _get_host()
            self.file_server_base_url = f"http://{host}:{self.config.file_server_port}"
            log.info(f"File server started at {self.file_server_base_url}")
        else:
            self.file_server_base_url = ""

        # Initialize telemetry
        from ai.telemetry import init_telemetry, install_log_handler

        await init_telemetry(
            self.config.victoria_logs_url or None,
            self.config.victoria_metrics_url or None,
            self.config.telemetry_batch_size,
            self.config.telemetry_flush_interval,
        )
        install_log_handler()

        # Start system performance monitor
        from ai.sysmon import start_sysmon

        self._sysmon_task = asyncio.create_task(start_sysmon(interval=15))

        # Load cogs
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info(f"Loaded cog: {cog}")
            except Exception as e:
                log.error(f"Failed to load cog {cog}: {e}", exc_info=True)

        # Sync slash commands to the configured guild
        if self.config.discord_guild_id:
            guild = discord.Object(id=self.config.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info(f"Synced commands to guild {self.config.discord_guild_id}")
        else:
            await self.tree.sync()
            log.info("Synced commands globally")

    async def on_ready(self):
        log.info(f"Online as {self.user} (ID: {self.user.id})")
        log.info(f"CTF root: {self.config.ctf_root}")
        log.info(
            f"Models: triage={self.config.triage_model} default={self.config.default_model} heavy={self.config.heavy_model}"
        )

    async def close(self):
        # Each cleanup step is independent — failures don't cascade
        async def _safe_cleanup(name, coro):
            try:
                await asyncio.wait_for(coro, timeout=15)
            except Exception as e:
                log.warning(f"Shutdown [{name}]: {e}")

        # Stop solve queue and kill child processes
        if hasattr(self, "solve_queue") and self.solve_queue:
            log.info("Stopping solve queue...")
            await _safe_cleanup("solve_queue", self.solve_queue.stop())

        # Stop sysmon
        if hasattr(self, "_sysmon_task") and self._sysmon_task:
            self._sysmon_task.cancel()
            try:
                await self._sysmon_task
            except (asyncio.CancelledError, Exception):
                pass

        # Stop file server
        if hasattr(self, "_file_server_runner") and self._file_server_runner:
            await _safe_cleanup("file_server", self._file_server_runner.cleanup())

        # Flush remaining telemetry
        from ai.telemetry import shutdown_telemetry

        await _safe_cleanup("telemetry", shutdown_telemetry())

        if self.db:
            self.db.close()
            log.info("Database closed")
        await super().close()


def main():
    try:
        config = Config.from_env()
    except KeyError as e:
        log.error(f"Missing required environment variable: {e}")
        log.error("Copy bot/.env.example to bot/.env and fill in the values")
        sys.exit(1)

    bot = CTFBot(config)

    # Graceful shutdown — prevent double-close and force exit on timeout
    _closing = False

    def handle_signal(sig, frame):
        nonlocal _closing
        if _closing:
            log.warning("Second signal received — forcing exit")
            os._exit(1)
        _closing = True
        log.info(f"Received {signal.Signals(sig).name}, shutting down...")
        try:
            asyncio.get_event_loop().create_task(bot.close())
        except RuntimeError:
            # Event loop already closed
            os._exit(1)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
