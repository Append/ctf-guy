#!/usr/bin/env python3
"""Reset command — tear down a CTF's Discord channels and DB records."""

import logging
import shutil
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from db.challenges import get_all
from db.ctfs import get_active_ctf

log = logging.getLogger(__name__)


class ResetCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="reset", description="Delete all Discord channels and DB records for the active CTF")
    @app_commands.describe(
        files="Also delete challenge files from disk (default: False)",
    )
    async def reset(
        self,
        interaction: discord.Interaction,
        files: bool = False,
    ):
        if not self._is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        ctf = get_active_ctf(self.bot.db, str(guild.id))
        if not ctf:
            await interaction.response.send_message("No active CTF to reset.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        deleted_channels = 0
        deleted_challenges = 0
        deleted_dirs = 0

        # Delete Discord forum channels + threads, then the category
        if ctf.category_id:
            category = guild.get_channel(int(ctf.category_id))
            if category and isinstance(category, discord.CategoryChannel):
                for ch in list(category.channels):
                    try:
                        await ch.delete(reason=f"CTF reset: {ctf.name}")
                        deleted_channels += 1
                    except Exception as e:
                        log.warning(f"Failed to delete channel {ch.name}: {e}")
                try:
                    await category.delete(reason=f"CTF reset: {ctf.name}")
                except Exception as e:
                    log.warning(f"Failed to delete category: {e}")

        # Delete challenge files from disk
        if files:
            challenges = get_all(self.bot.db, ctf.id)
            for chall in challenges:
                if chall.challenge_dir:
                    d = Path(chall.challenge_dir)
                    if d.exists():
                        shutil.rmtree(d, ignore_errors=True)
                        deleted_dirs += 1

        # Delete DB records
        conn = self.bot.db
        challenges = get_all(conn, ctf.id)
        deleted_challenges = len(challenges)
        conn.execute("DELETE FROM challenges WHERE ctf_id = ?", (ctf.id,))
        conn.execute("DELETE FROM ctfs WHERE id = ?", (ctf.id,))
        conn.commit()

        parts = [
            f"**{ctf.name}** reset:",
            f"{deleted_channels} channels deleted",
            f"{deleted_challenges} challenge records removed",
        ]
        if files:
            parts.append(f"{deleted_dirs} directories removed")

        await interaction.followup.send(" | ".join(parts))

    def _is_authorized(self, interaction: discord.Interaction) -> bool:
        return self.bot.config.is_user_allowed(interaction.user.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(ResetCog(bot))
