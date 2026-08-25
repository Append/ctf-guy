#!/usr/bin/env python3
"""Autosolve command — queue and auto-solve challenges."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ai.queue import SolveQueue
from db.challenges import get_unsolved
from db.ctfs import get_active_ctf

log = logging.getLogger(__name__)


class AutosolveCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="autosolve", description="Auto-solve all unsolved challenges")
    @app_commands.describe(
        action="start (default), stop, or status",
        race="Use multi-model racing instead of single solver",
        deep="Run deep analysis mode for each challenge",
        category="Only solve this category (e.g. crypto, web, rev)",
        concurrency="Max concurrent workers (default: from config)",
        model="Model override for single-solver mode",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Start solving", value="start"),
            app_commands.Choice(name="Stop solving", value="stop"),
            app_commands.Choice(name="Show status", value="status"),
        ],
        model=[
            app_commands.Choice(name="Haiku (fast)", value="haiku"),
            app_commands.Choice(name="Sonnet (balanced)", value="sonnet"),
            app_commands.Choice(name="Opus (strongest)", value="opus"),
            app_commands.Choice(name="Codex (OpenAI)", value="codex"),
        ],
    )
    async def autosolve(
        self,
        interaction: discord.Interaction,
        action: str = "start",
        race: bool = False,
        deep: bool = False,
        category: str | None = None,
        concurrency: int | None = None,
        model: str | None = None,
    ):
        if not self._is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        if action == "status":
            await self._show_status(interaction)
            return

        if action == "stop":
            await self._stop(interaction)
            return

        # Start
        await interaction.response.defer(thinking=True)

        ctf = get_active_ctf(self.bot.db, str(guild.id))
        if not ctf:
            await interaction.followup.send("No active CTF. Use `/scout` first.")
            return

        unsolved = get_unsolved(self.bot.db, ctf.id)

        # Filter out under-maintenance challenges
        import json
        from pathlib import Path

        clean = []
        for c in unsolved:
            if c.challenge_dir:
                meta_path = Path(c.challenge_dir) / "challenge.json"
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text())
                        if meta.get("extra", {}).get("under_maintenance"):
                            continue
                    except Exception:
                        pass
            clean.append(c)
        unsolved = clean

        # Filter by category if specified
        if category:
            cat_filter = category.lower()
            unsolved = [c for c in unsolved if cat_filter in c.category.lower()]

        if not unsolved:
            await interaction.followup.send("No matching unsolved challenges (or all under maintenance)!")
            return

        # Get or create the queue
        effective_concurrency = concurrency or self.bot.config.autosolve_concurrency
        if not hasattr(self.bot, "solve_queue") or self.bot.solve_queue is None:
            self.bot.solve_queue = SolveQueue(self.bot, concurrency=effective_concurrency)

        if self.bot.solve_queue.running:
            await interaction.followup.send(f"Queue already running. {self.bot.solve_queue.get_status_summary()}")
            return

        # Apply model override to config temporarily
        if model:
            self.bot.config.autosolve_model = model

        # Detect series dependencies for queue ordering
        from ai.dependency import detect_dependencies

        deps = await detect_dependencies(unsolved, self.bot.config)
        self.bot.solve_queue.dependencies = deps

        if deep and race:
            await interaction.followup.send(
                "deep and race are incompatible — deep mode takes precedence, race disabled.",
                ephemeral=True,
            )
            race = False

        self.bot.solve_queue.race_mode = race
        self.bot.solve_queue.deep_mode = deep
        self.bot.solve_queue.concurrency = effective_concurrency
        await self.bot.solve_queue.enqueue(unsolved)

        # Use the channel where the command was invoked for status updates
        status_channel = interaction.channel
        if isinstance(status_channel, discord.Thread):
            status_channel = status_channel.parent

        await self.bot.solve_queue.start(status_channel)
        mode = "Deep analyzing" if deep else ("Racing" if race else "Auto-solving")
        cat_msg = f" ({category})" if category else ""
        await interaction.followup.send(
            f"**{mode}** {len(unsolved)} challenges{cat_msg} with {effective_concurrency} workers."
        )

        # Don't await — let it run in the background
        # The queue workers are asyncio tasks that run independently

    async def _show_status(self, interaction: discord.Interaction):
        if not hasattr(self.bot, "solve_queue") or self.bot.solve_queue is None:
            await interaction.response.send_message("No auto-solve running.", ephemeral=True)
            return

        status = self.bot.solve_queue.get_status_dict()
        embed = discord.Embed(
            title="Auto-Solve Status",
            color=0x2ECC71 if status["running"] else 0x95A5A6,
        )
        embed.add_field(name="Running", value="Yes" if status["running"] else "No", inline=True)
        embed.add_field(name="Solved", value=f"{status['solved']}/{status['total']}", inline=True)
        embed.add_field(name="Failed", value=str(status["failed"]), inline=True)
        embed.add_field(name="In Progress", value=str(status["solving"]), inline=True)
        embed.add_field(name="Queued", value=str(status["queued"]), inline=True)

        await interaction.response.send_message(embed=embed)

    async def _stop(self, interaction: discord.Interaction):
        if not hasattr(self.bot, "solve_queue") or self.bot.solve_queue is None:
            await interaction.response.send_message("No auto-solve running.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        await self.bot.solve_queue.stop()
        await interaction.followup.send("Auto-solve stopped.")

    def _is_authorized(self, interaction: discord.Interaction) -> bool:
        allowed = self.bot.config.allowed_user_ids
        if not allowed:
            return True
        return interaction.user.id in allowed


async def setup(bot: commands.Bot):
    await bot.add_cog(AutosolveCog(bot))
