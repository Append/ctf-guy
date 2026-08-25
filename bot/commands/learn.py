#!/usr/bin/env python3
"""Learn command — scan all solved challenges and build pattern files."""

import discord
from discord import app_commands
from discord.ext import commands

from ai.learner import scan_and_build_patterns


class LearnCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="learn", description="Scan all solved challenges and rebuild pattern files"
    )
    async def learn(self, interaction: discord.Interaction):
        if not self._is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        counts = scan_and_build_patterns(self.bot.config.ctf_root)

        if not counts:
            await interaction.followup.send("No READMEs found to learn from.")
            return

        total = sum(counts.values())
        breakdown = "\n".join(
            f"- **{cat}**: {n} patterns" for cat, n in sorted(counts.items())
        )
        await interaction.followup.send(
            f"Learned from **{total}** challenges across {len(counts)} categories:\n{breakdown}"
        )

    def _is_authorized(self, interaction: discord.Interaction) -> bool:
        allowed = self.bot.config.allowed_user_ids
        if not allowed:
            return True
        return interaction.user.id in allowed


async def setup(bot: commands.Bot):
    await bot.add_cog(LearnCog(bot))
