#!/usr/bin/env python3
"""Submit command — submit a flag to the CTF platform."""

import json
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from db.challenges import get_by_thread, mark_solved
from discord_ui.threads import update_thread_status
from platforms.ctfd import CTFdPlatform
from platforms.picoctf import PicoCTFPlatform

log = logging.getLogger(__name__)


class SubmitCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="submit", description="Submit a flag for this challenge")
    @app_commands.describe(flag="The flag to submit")
    async def submit(self, interaction: discord.Interaction, flag: str):
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

        # Detect platform from challenge.json
        platform_type = "ctfd"
        if challenge.challenge_dir:
            meta_path = Path(challenge.challenge_dir) / "challenge.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    platform_type = meta.get("platform", "ctfd")
                except Exception:
                    pass

        # Check dedup + throttle
        from ai.flag_tracker import flag_tracker

        if flag_tracker.check_dedup(challenge.ctfd_id, flag):
            await interaction.followup.send(f"Flag `{flag}` already submitted for this challenge.")
            return
        cooldown = flag_tracker.get_cooldown_remaining(challenge.ctfd_id)
        if cooldown > 0:
            await interaction.followup.send(f"Submission throttled — {cooldown}s cooldown remaining.")
            return

        try:
            if platform_type == "picoctf":
                client = PicoCTFPlatform()
                result = await client.submit_flag(challenge.ctfd_id, flag)
                await client.close()
            else:
                config = self.bot.config
                if not config.ctfd_url or not config.ctfd_token:
                    await interaction.followup.send("CTFd credentials not configured.")
                    return
                client = CTFdPlatform(config.ctfd_url, token=config.ctfd_token, session=config.ctfd_session)
                result = await client.submit_flag(challenge.ctfd_id, flag)
                await client.close()
        except Exception as e:
            await interaction.followup.send(f"Submission error: {e}")
            return

        flag_tracker.record(challenge.ctfd_id, flag, result.status)

        if result.status == "correct":
            mark_solved(self.bot.db, challenge.id, flag)
            if challenge.challenge_dir:
                flag_path = Path(challenge.challenge_dir) / "flag.txt"
                flag_path.write_text(flag + "\n")
            from ai.flag_events import notify

            notify(challenge.ctfd_id, flag=flag, solver_id="manual")
            await interaction.followup.send(f"**CORRECT!** `{flag}`")
            await update_thread_status(thread, "solved")
        elif result.status == "already_solved":
            mark_solved(self.bot.db, challenge.id, flag)
            from ai.flag_events import notify

            notify(challenge.ctfd_id, flag=flag, solver_id="manual")
            await interaction.followup.send(f"Already solved. `{flag}`")
            await update_thread_status(thread, "solved")
        else:
            await interaction.followup.send(f"**Incorrect.** {result.message}")

    def _is_authorized(self, interaction: discord.Interaction) -> bool:
        return self.bot.config.is_user_allowed(interaction.user.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(SubmitCog(bot))
