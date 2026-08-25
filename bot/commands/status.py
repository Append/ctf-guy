#!/usr/bin/env python3
"""Status command — show CTF solve status."""

import discord
from discord import app_commands
from discord.ext import commands

from db.challenges import get_all
from db.ctfs import get_active_ctf
from discord_ui.embeds import status_embed


class StatusCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="status", description="Show current CTF solve status and scoreboard"
    )
    async def status(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "Must be used in a server.", ephemeral=True
            )
            return

        ctf = get_active_ctf(self.bot.db, str(guild.id))
        if not ctf:
            await interaction.response.send_message(
                "No active CTF. Use `/scout` first.", ephemeral=True
            )
            return

        challenges = get_all(self.bot.db, ctf.id)
        if not challenges:
            await interaction.response.send_message(
                "No challenges found.", ephemeral=True
            )
            return

        embed = status_embed(ctf.name, challenges)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(StatusCog(bot))
