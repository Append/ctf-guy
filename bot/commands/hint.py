#!/usr/bin/env python3
"""Hint command — inject operator hints into an active or future solve."""

import logging
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from db.challenges import get_by_thread

log = logging.getLogger(__name__)


class HintCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="hint", description="Send a hint to the solver for this challenge"
    )
    @app_commands.describe(message="Hint text (e.g. 'try XOR with key 0x42')")
    async def hint(self, interaction: discord.Interaction, message: str):
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

        if not challenge.challenge_dir:
            await interaction.response.send_message(
                "Challenge has no directory configured.", ephemeral=True
            )
            return

        # Append hint to operator_hints.txt
        hints_file = Path(challenge.challenge_dir) / "operator_hints.txt"
        timestamp = datetime.now().strftime("%H:%M")
        entry = f"[{timestamp}] {interaction.user.display_name}: {message}\n"

        with open(hints_file, "a") as f:
            f.write(entry)

        log.info(f"Operator hint for {challenge.name}: {message}")
        await interaction.response.send_message(
            f"Hint saved for **{challenge.name}**. "
            f"Next `/solve` will include it in the prompt.",
            silent=True,
        )

    def _is_authorized(self, interaction: discord.Interaction) -> bool:
        allowed = self.bot.config.allowed_user_ids
        if not allowed:
            return True
        return interaction.user.id in allowed


async def setup(bot: commands.Bot):
    await bot.add_cog(HintCog(bot))
