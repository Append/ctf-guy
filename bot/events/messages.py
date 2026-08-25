#!/usr/bin/env python3
"""Message event handler — respond to @mentions in challenge threads."""

import logging

import discord
from discord.ext import commands

from ai.solver import Solver
from db.challenges import get_by_thread

log = logging.getLogger(__name__)


class MessagesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.solver = Solver(bot.config, bot.db)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore our own messages
        if message.author == self.bot.user:
            return

        # Only respond to @mentions in threads
        if not isinstance(message.channel, discord.Thread):
            return

        if self.bot.user not in message.mentions:
            return

        # Check authorization
        if (
            self.bot.config.allowed_user_ids
            and message.author.id not in self.bot.config.allowed_user_ids
        ):
            return

        thread = message.channel
        challenge = get_by_thread(self.bot.db, str(thread.id))
        if not challenge:
            return

        # Strip the mention from the message
        content = message.content
        for mention in message.mentions:
            content = content.replace(f"<@{mention.id}>", "").replace(
                f"<@!{mention.id}>", ""
            )
        content = content.strip()

        if not content:
            await thread.send("What would you like me to look at?")
            return

        async with thread.typing():
            try:
                await self.solver.respond(
                    thread=thread,
                    user_message=content,
                    challenge_name=challenge.name,
                    category=challenge.category,
                    points=challenge.points,
                    description=challenge.description or "",
                    challenge_dir=challenge.challenge_dir or ".",
                )
            except Exception as e:
                log.error(f"Message response failed: {e}", exc_info=True)
                await thread.send(f"Error: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(MessagesCog(bot))
