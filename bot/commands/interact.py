#!/usr/bin/env python3
"""Interact command — spin up a shared terminal session for a challenge."""

import asyncio
import contextlib
import logging
import os
import secrets
import shlex
import socket

import discord
from discord import app_commands
from discord.ext import commands

from db.challenges import get_by_thread

log = logging.getLogger(__name__)

# Track active sessions: thread_id -> session info
_active_sessions: dict[str, dict] = {}

# Port range for ttyd instances
PORT_START = int(os.environ.get("TTYD_PORT_START", "9000"))
PORT_END = int(os.environ.get("TTYD_PORT_END", "9099"))


def _find_free_port() -> int:
    """Find a free port in the configured range."""
    for port in range(PORT_START, PORT_END + 1):
        # Check if port is already used by an active session
        if any(s["port"] == port for s in _active_sessions.values()):
            continue
        # Check if port is actually free
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free ports in range {PORT_START}-{PORT_END}")


class InteractCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="interact", description="Open a shared terminal for this challenge"
    )
    @app_commands.describe(
        action="start (default), stop, or status",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Start terminal", value="start"),
            app_commands.Choice(name="Stop terminal", value="stop"),
            app_commands.Choice(name="Show status", value="status"),
        ]
    )
    async def interact(
        self,
        interaction: discord.Interaction,
        action: str = "start",
    ):
        if not self._is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "Use this command in a challenge thread.", ephemeral=True
            )
            return

        thread = interaction.channel
        thread_id = str(thread.id)

        if action == "stop":
            await self._stop_session(interaction, thread_id)
            return

        if action == "status":
            await self._show_status(interaction, thread_id)
            return

        # Start a new session
        await interaction.response.defer(thinking=True)

        # Check if session already exists
        if thread_id in _active_sessions:
            session = _active_sessions[thread_id]
            if session.get("process") and session["process"].returncode is None:
                host = os.environ.get("TTYD_HOST", _get_host())
                await interaction.followup.send(
                    embed=self._session_embed(session, host)
                )
                return

        challenge = get_by_thread(self.bot.db, thread_id)
        if not challenge:
            await interaction.followup.send(
                "This thread isn't linked to a challenge.", ephemeral=True
            )
            return

        try:
            session = await self._start_session(challenge, thread_id)
            host = os.environ.get("TTYD_HOST", _get_host())
            await interaction.followup.send(embed=self._session_embed(session, host))
        except Exception as e:
            log.error(f"Failed to start terminal session: {e}", exc_info=True)
            await interaction.followup.send(f"Failed to start terminal: {e}")

    async def _start_session(self, challenge, thread_id: str) -> dict:
        """Start a ttyd + zellij session for a challenge."""
        port = _find_free_port()
        password = secrets.token_urlsafe(8)
        session_name = f"ctf-{challenge.slug}"
        challenge_dir = challenge.challenge_dir or "."

        # Kill any stale zellij session with this name
        try:
            kill_proc = await asyncio.create_subprocess_exec(
                "zellij",
                "kill-session",
                session_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await kill_proc.wait()
        except Exception:
            pass

        # Build the shell command that ttyd will run
        # Attach to existing session if it exists, otherwise create new
        shell_cmd = (
            f"cd {shlex.quote(str(challenge_dir))} && "
            f"zellij attach {shlex.quote(session_name)} --create "
            f"options --default-shell zsh"
        )

        # Launch ttyd
        proc = await asyncio.create_subprocess_exec(
            "ttyd",
            "-p",
            str(port),
            "-W",  # Writable
            "-c",
            f"ctf:{password}",  # Basic auth
            "-t",
            "fontSize=14",
            "-t",
            "fontFamily=monospace",
            "zsh",
            "-c",
            shell_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        session = {
            "port": port,
            "password": password,
            "session_name": session_name,
            "challenge_name": challenge.name,
            "challenge_dir": challenge_dir,
            "process": proc,
            "pid": proc.pid,
        }
        _active_sessions[thread_id] = session

        log.info(
            f"Terminal session started: {session_name} on port {port} (pid {proc.pid})"
        )

        # Monitor process in background
        session["_monitor_task"] = asyncio.create_task(
            self._monitor_session(thread_id, proc)
        )

        return session

    async def _monitor_session(self, thread_id: str, proc):
        """Watch for session process exit and clean up."""
        await proc.wait()
        if thread_id in _active_sessions:
            session = _active_sessions.pop(thread_id)
            log.info(
                f"Terminal session ended: {session['session_name']} (rc={proc.returncode})"
            )

    async def _stop_session(self, interaction: discord.Interaction, thread_id: str):
        """Stop an active terminal session."""
        if thread_id not in _active_sessions:
            await interaction.response.send_message(
                "No active terminal session.", ephemeral=True
            )
            return

        session = _active_sessions.pop(thread_id)

        # Cancel monitor task
        monitor = session.get("_monitor_task")
        if monitor:
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await monitor

        proc = session.get("process")
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()

        # Also kill the zellij session
        try:
            kill_proc = await asyncio.create_subprocess_exec(
                "zellij",
                "kill-session",
                session["session_name"],
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await kill_proc.wait()
        except Exception:
            pass

        log.info(f"Terminal session stopped: {session['session_name']}")
        await interaction.response.send_message(
            f"Terminal session stopped for **{session['challenge_name']}**."
        )

    async def _show_status(self, interaction: discord.Interaction, thread_id: str):
        """Show status of terminal session."""
        if thread_id not in _active_sessions:
            await interaction.response.send_message(
                "No active terminal session.", ephemeral=True
            )
            return

        session = _active_sessions[thread_id]
        proc = session.get("process")
        alive = proc and proc.returncode is None

        if not alive:
            _active_sessions.pop(thread_id, None)
            await interaction.response.send_message(
                "Terminal session has ended.", ephemeral=True
            )
            return

        host = os.environ.get("TTYD_HOST", _get_host())
        await interaction.response.send_message(
            embed=self._session_embed(session, host)
        )

    def _session_embed(self, session: dict, host: str) -> discord.Embed:
        """Build an embed with session connection info."""
        port = session["port"]
        password = session["password"]
        url = f"http://{host}:{port}"

        embed = discord.Embed(
            title=f"Terminal: {session['challenge_name']}",
            description=(
                f"**Open in browser:** [{url}]({url})\n\n"
                f"**Username:** `ctf`\n"
                f"**Password:** `{password}`\n\n"
                f"This is a shared zellij session. Multiple people can connect.\n"
                f"Run `claude` inside to start an interactive AI solver."
            ),
            color=0x2ECC71,
        )
        embed.add_field(
            name="Session", value=f"`{session['session_name']}`", inline=True
        )
        embed.add_field(
            name="Directory", value=f"`{session['challenge_dir']}`", inline=True
        )
        embed.set_footer(
            text="Use /interact action:Stop to close | All work saves to the challenge directory"
        )
        return embed

    def _is_authorized(self, interaction: discord.Interaction) -> bool:
        return self.bot.config.is_user_allowed(interaction.user.id)


def _get_host() -> str:
    """Get the best hostname for sharing.

    Prefers Tailscale hostname, falls back to local IP.
    """
    # Try Tailscale first
    try:
        import subprocess

        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            import json as json_mod

            status = json_mod.loads(result.stdout)
            self_node = status.get("Self", {})
            dns_name = self_node.get("DNSName", "")
            if dns_name:
                return dns_name.rstrip(".")
            # Fall back to Tailscale IP
            ts_ips = self_node.get("TailscaleIPs", [])
            if ts_ips:
                return ts_ips[0]
    except Exception:
        pass

    # Fall back to local IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


async def setup(bot: commands.Bot):
    await bot.add_cog(InteractCog(bot))
