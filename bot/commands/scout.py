#!/usr/bin/env python3
"""Scout command — scrape a CTF platform and create challenge threads."""

import asyncio
import json
import logging
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands

from db.challenges import get_existing_ctfd_ids, upsert_challenge
from db.ctfs import get_active_ctf, upsert_ctf
from discord_ui.embeds import challenge_embed, triage_embed
from discord_ui.threads import create_challenge_thread, setup_forum_tags, slugify
from platforms.base import CTFPlatform
from platforms.ctfd import CTFdPlatform
from platforms.picoctf import PicoCTFPlatform

log = logging.getLogger(__name__)


class ScoutCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="scout", description="Scrape a CTF platform and create challenge threads")
    @app_commands.describe(
        url="CTF platform URL (e.g. https://ctf.kernelcon.org or https://play.picoctf.org)",
        platform="Platform type (auto-detected from URL if not specified)",
        limit="Max number of challenges to import (default: all)",
        category="Only import this category (e.g. forensics, crypto, web exploitation)",
        autosolve="Auto-start solving all challenges after scouting",
        race="Auto-solve using multi-model racing (overrides autosolve)",
        deep="Run deep analysis mode for each challenge",
        event="Filter by event name (picoCTF only, e.g. 'picoCTF 2026')",
        manager="Enable manager corrections during solving (default: from config)",
        fast="Enable fast mode for solvers (faster output, higher cost)",
    )
    @app_commands.choices(
        platform=[
            app_commands.Choice(name="Auto-detect", value="auto"),
            app_commands.Choice(name="CTFd", value="ctfd"),
            app_commands.Choice(name="picoCTF", value="picoctf"),
        ]
    )
    async def scout(
        self,
        interaction: discord.Interaction,
        url: str,
        platform: str = "auto",
        limit: int | None = None,
        category: str | None = None,
        autosolve: bool = False,
        race: bool = False,
        deep: bool = False,
        event: str | None = None,
        manager: bool | None = None,
        fast: bool | None = None,
    ):
        await interaction.response.defer(thinking=True)

        if not self._is_authorized(interaction):
            await interaction.followup.send("Not authorized.", ephemeral=True)
            return

        config = self.bot.config
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("Must be used in a server.")
            return

        # Detect platform
        platform_type = platform
        if platform_type == "auto":
            platform_type = self._detect_platform(url)

        # Create platform client
        try:
            client = self._create_client(platform_type, url, event=event)
        except ValueError as e:
            await interaction.followup.send(str(e))
            return

        # Parse CTF name from URL (or event name)
        parsed = urlparse(url)
        if event:
            ctf_name = slugify(event)
        else:
            ctf_name = parsed.hostname.split(".")[0] if parsed.hostname else "ctf"

        try:
            event_msg = f" (event: {event})" if event else ""
            await interaction.edit_original_response(content=f"Fetching challenges from {platform_type}{event_msg}...")
            challenges = await client.fetch_challenges()
        except Exception as e:
            await interaction.followup.send(f"Failed to fetch challenges: {e}")
            await client.close()
            return

        if not challenges:
            await interaction.followup.send("No challenges found.")
            await client.close()
            return

        # Filter out under-maintenance challenges
        maintenance = [c for c in challenges if c.extra.get("under_maintenance")]
        if maintenance:
            log.info(f"Skipping {len(maintenance)} under-maintenance challenges: " f"{[c.name for c in maintenance]}")
            challenges = [c for c in challenges if not c.extra.get("under_maintenance")]

        # Filter out already-solved challenges (solved on the platform)
        already_solved = [c for c in challenges if c.solved_by_me]
        if already_solved:
            log.info(
                f"Skipping {len(already_solved)} already-solved challenges: " f"{[c.name for c in already_solved]}"
            )
            challenges = [c for c in challenges if not c.solved_by_me]

        # Filter by category if specified
        if category:
            cat_filter = category.lower()
            challenges = [c for c in challenges if cat_filter in c.category.lower()]
            if not challenges:
                await interaction.followup.send(f"No challenges in category '{category}'.")
                await client.close()
                return

        # Sort by points
        challenges.sort(key=lambda c: c.points)

        # Check for existing CTF — reuse category/channels if re-scouting
        existing_ctf = get_active_ctf(self.bot.db, str(guild.id))
        discord_category = None
        cat_channels: dict[str, discord.TextChannel] = {}
        ctf_id = None

        if existing_ctf and existing_ctf.url == url:
            # Re-scout: reuse existing Discord category and channels
            ctf_id = existing_ctf.id
            if existing_ctf.category_id:
                discord_category = guild.get_channel(int(existing_ctf.category_id))

            # Find existing category channels (text or forum)
            if discord_category and isinstance(discord_category, discord.CategoryChannel):
                for ch in discord_category.channels:
                    if isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
                        cat_channels[ch.name] = ch

            # Get already-tracked challenge IDs to skip
            existing_ids = get_existing_ctfd_ids(self.bot.db, ctf_id)
            new_challenges = [c for c in challenges if c.id not in existing_ids]

            log.info(f"Re-scout: {len(challenges)} fetched, {len(existing_ids)} tracked, {len(new_challenges)} new")

            if not new_challenges:
                await interaction.followup.send(
                    f"No new challenges found. {len(existing_ids)} already tracked out of {len(challenges)} fetched."
                )
                await client.close()
                return

            challenges = new_challenges

        # Apply limit AFTER dedup — limit controls how many NEW challenges to add
        if limit and limit > 0:
            challenges = challenges[:limit]

        await interaction.edit_original_response(
            content=f"Found {len(challenges)} challenges to add. Creating threads..."
        )

        # Create Discord category if needed
        if discord_category is None:
            discord_category = await guild.create_category(ctf_name.upper())

        # Determine flag format
        flag_format = r"picoCTF\{.*\}" if platform_type == "picoctf" else r"kernel\{.*\}"

        # Group new challenges by category
        by_cat: dict[str, list] = {}
        for chall in challenges:
            cat = slugify(chall.category)
            by_cat.setdefault(cat, []).append(chall)

        # Create forum channels for each category
        for cat_slug in sorted(by_cat.keys()):
            if cat_slug not in cat_channels:
                channel = await guild.create_forum(cat_slug, category=discord_category)
                await setup_forum_tags(channel)
                cat_channels[cat_slug] = channel

        first_channel = next(iter(cat_channels.values()))

        # Store CTF in DB if new
        if ctf_id is None:
            ctf_id = upsert_ctf(
                self.bot.db,
                ctf_name,
                url,
                str(guild.id),
                str(first_channel.id),
                category_id=str(discord_category.id),
                flag_format=flag_format,
            )

        created = 0
        total = len(challenges)
        for i, chall in enumerate(challenges):
            try:
                # Pin category slug from list endpoint (detail endpoint may differ)
                cat_slug = slugify(chall.category)

                # Fetch full details (description, files) if not in list
                if not chall.description:
                    try:
                        chall = await client.fetch_challenge(chall.id)
                    except Exception as e:
                        log.warning(f"Could not fetch detail for {chall.name}: {e}")

                if (i + 1) % 5 == 0:
                    await interaction.edit_original_response(content=f"Creating threads... {i + 1}/{total}")

                # Find the right category channel (using list-endpoint slug)
                channel = cat_channels.get(cat_slug, first_channel)

                slug = slugify(chall.name)
                thread_name = f"{slug}-{chall.points}pt"[:100]
                files_url = None
                if hasattr(self.bot, "file_server_base_url") and self.bot.file_server_base_url:
                    files_url = f"{self.bot.file_server_base_url}/{cat_slug}/{slug}/"
                embed = challenge_embed(
                    chall.name,
                    chall.category,
                    chall.points,
                    chall.description,
                    chall.files,
                    chall.solves,
                    files_url=files_url,
                )
                thread = await create_challenge_thread(channel, thread_name, embed)

                # Create challenge directory (use pinned cat_slug for consistency)
                chall_dir = config.ctf_root / "challenges" / cat_slug / slug
                chall_dir.mkdir(parents=True, exist_ok=True)

                # Download files
                for file_url in chall.files:
                    filename = file_url.split("/")[-1].split("?")[0]
                    if not filename:
                        filename = "challenge_file"
                    try:
                        await client.download_file(file_url, chall_dir / filename)
                    except Exception as e:
                        log.warning(f"Failed to download {file_url}: {e}")

                # Write challenge metadata
                meta = {
                    "id": chall.id,
                    "name": chall.name,
                    "category": chall.category,
                    "points": chall.points,
                    "description": chall.description,
                    "files": chall.files,
                    "solves": chall.solves,
                    "platform": platform_type,
                    "extra": chall.extra,
                }
                (chall_dir / "challenge.json").write_text(json.dumps(meta, indent=2))

                # Store in DB
                upsert_challenge(
                    self.bot.db,
                    ctf_id,
                    chall.id,
                    chall.name,
                    slug,
                    chall.category,
                    chall.points,
                    chall.description,
                    str(thread.id),
                    str(chall_dir),
                )
                created += 1

                # Rate limit delay
                await asyncio.sleep(1)

            except Exception as e:
                log.error(f"Failed to create thread for {chall.name}: {e}")

        # Post triage summary to the channel where scout was invoked
        invoke_channel = interaction.channel
        if isinstance(invoke_channel, discord.Thread):
            invoke_channel = invoke_channel.parent

        summary = triage_embed(
            ctf_name,
            [
                {
                    "name": c.name,
                    "category": c.category,
                    "value": c.points,
                    "solved_by_me": c.solved_by_me,
                }
                for c in challenges
            ],
        )
        await invoke_channel.send(embed=summary)

        await client.close()
        await interaction.followup.send(
            f"Scouted **{ctf_name}**: {created} new challenges added across " f"{len(cat_channels)} category channels"
        )

        # Auto-solve if requested — only solve the challenges we just created
        if (autosolve or race) and created > 0:
            from ai.queue import SolveQueue
            from db.challenges import get_unsolved

            # Only enqueue challenges we just scouted (not stale DB entries
            # whose threads may have been deleted)
            scouted_ids = {c.id for c in challenges}
            unsolved = [c for c in get_unsolved(self.bot.db, ctf_id) if c.ctfd_id in scouted_ids]
            if unsolved:
                if not hasattr(self.bot, "solve_queue") or self.bot.solve_queue is None:
                    self.bot.solve_queue = SolveQueue(self.bot, concurrency=self.bot.config.autosolve_concurrency)

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
                self.bot.solve_queue.corrections_enabled = (
                    manager if manager is not None else self.bot.config.manager_corrections
                )
                self.bot.solve_queue.fast_mode = fast if fast is not None else self.bot.config.fast_mode
                await self.bot.solve_queue.enqueue(unsolved)
                # Use the channel where /scout was called for the dashboard
                invoke_channel = interaction.channel
                if isinstance(invoke_channel, discord.Thread):
                    invoke_channel = invoke_channel.parent
                await self.bot.solve_queue.start(invoke_channel)
                mode = "Deep Analysis" if deep else ("Race" if race else "Auto-solve")
                await invoke_channel.send(f"**{mode} started** for {len(unsolved)} unsolved challenges.")

    def _detect_platform(self, url: str) -> str:
        url_lower = url.lower()
        if "picoctf" in url_lower:
            return "picoctf"
        return "ctfd"

    def _create_client(self, platform_type: str, url: str, event: str | None = None) -> CTFPlatform:
        config = self.bot.config
        if platform_type == "picoctf":
            return PicoCTFPlatform(event=event)
        elif platform_type == "ctfd":
            ctfd_url = config.ctfd_url or url
            if not config.ctfd_token and not config.ctfd_session:
                raise ValueError("CTFd requires CTFD_TOKEN or CTFD_SESSION in .env")
            return CTFdPlatform(ctfd_url, token=config.ctfd_token, session=config.ctfd_session)
        else:
            raise ValueError(f"Unknown platform: {platform_type}")

    def _is_authorized(self, interaction: discord.Interaction) -> bool:
        allowed = self.bot.config.allowed_user_ids
        if not allowed:
            return True
        return interaction.user.id in allowed


async def setup(bot: commands.Bot):
    await bot.add_cog(ScoutCog(bot))
