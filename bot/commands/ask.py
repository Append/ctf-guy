#!/usr/bin/env python3
"""Ask command — ask the AI a question about a challenge."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ai.solver import Solver
from db.challenges import get_by_thread

log = logging.getLogger(__name__)


class AskCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.solver = Solver(bot.config, bot.db)

    @app_commands.command(
        name="ask", description="Ask the AI solver a question about this challenge"
    )
    @app_commands.describe(question="Your question")
    async def ask(self, interaction: discord.Interaction, question: str):
        if not self._is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "Use this command in a challenge thread.", ephemeral=True
            )
            return

        thread = interaction.channel
        challenge = get_by_thread(self.bot.db, str(thread.id))
        if not challenge:
            await interaction.response.send_message(
                "This thread isn't linked to a challenge.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            await self.solver.respond(
                thread=thread,
                user_message=question,
                challenge_name=challenge.name,
                category=challenge.category,
                points=challenge.points,
                description=challenge.description or "",
                challenge_dir=challenge.challenge_dir or ".",
            )
            await interaction.followup.send("Done.")
        except Exception as e:
            log.error(f"Ask failed: {e}", exc_info=True)
            await interaction.followup.send(f"Error: {e}")

    def _is_authorized(self, interaction: discord.Interaction) -> bool:
        return self.bot.config.is_user_allowed(interaction.user.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(AskCog(bot))
