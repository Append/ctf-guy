#!/usr/bin/env python3
"""Discord thread/forum post creation and management."""

import logging
import re

import discord

log = logging.getLogger(__name__)

# Status prefixes for thread names (used when forums aren't available)
STATUS_SOLVED = "\u2705"  # green checkmark
STATUS_NEEDS_HELP = "\U0001f534"  # red circle
STATUS_UNSOLVED = "\u2b1c"  # white square

# Forum tag names
TAG_UNSOLVED = "Unsolved"
TAG_SOLVED = "Solved"
TAG_NEEDS_HELP = "Needs Help"
TAG_IN_PROGRESS = "In Progress"


def challenge_thread_name(category: str, name: str, points: int) -> str:
    """Generate a thread/post name: {slug}-{points}pt.

    Truncated to Discord's 100-char limit.
    """
    slug = slugify(name)
    thread_name = f"{slug}-{points}pt"
    return thread_name[:100]


async def setup_forum_tags(forum: discord.ForumChannel) -> dict[str, discord.ForumTag]:
    """Ensure the forum has our status tags. Returns tag name -> tag mapping."""
    existing = {tag.name: tag for tag in forum.available_tags}

    tags_to_create = []
    for tag_name, emoji in [
        (TAG_UNSOLVED, "\u2b1c"),
        (TAG_IN_PROGRESS, "\U0001f7e1"),  # yellow circle
        (TAG_NEEDS_HELP, "\U0001f534"),
        (TAG_SOLVED, "\u2705"),
    ]:
        if tag_name not in existing:
            tags_to_create.append(discord.ForumTag(name=tag_name, emoji=emoji))

    if tags_to_create:
        all_tags = list(forum.available_tags) + tags_to_create
        await forum.edit(available_tags=all_tags[:20])  # Discord max 20 tags
        # Refresh
        existing = {tag.name: tag for tag in forum.available_tags}

    return existing


async def create_challenge_thread(
    channel: discord.TextChannel | discord.ForumChannel,
    name: str,
    embed: discord.Embed,
) -> discord.Thread:
    """Create a thread/forum post for a challenge.

    Supports both regular text channels (creates thread from message)
    and forum channels (creates forum post).
    """
    if isinstance(channel, discord.ForumChannel):
        # Forum post — create thread with embed as starter message
        tags = {tag.name: tag for tag in channel.available_tags}
        applied = []
        if TAG_UNSOLVED in tags:
            applied.append(tags[TAG_UNSOLVED])

        thread_with_msg = await channel.create_thread(
            name=name,
            embed=embed,
            applied_tags=applied,
            silent=True,
        )
        # create_thread returns (Thread, Message) for forums
        return (
            thread_with_msg[0]
            if isinstance(thread_with_msg, tuple)
            else thread_with_msg
        )
    else:
        # Regular text channel — create message then thread
        msg = await channel.send(embed=embed, silent=True)
        thread = await msg.create_thread(name=name)
        return thread


async def update_thread_status(
    thread: discord.Thread,
    status: str,
) -> None:
    """Update a thread's status via forum tags or name prefix.

    status: "solved", "needs_help", "in_progress", or "unsolved"
    """
    tag_map = {
        "solved": TAG_SOLVED,
        "needs_help": TAG_NEEDS_HELP,
        "in_progress": TAG_IN_PROGRESS,
        "unsolved": TAG_UNSOLVED,
    }

    try:
        parent = thread.parent
        if isinstance(parent, discord.ForumChannel):
            # Forum mode — update tags
            available = {tag.name: tag for tag in parent.available_tags}
            target_tag_name = tag_map.get(status, TAG_UNSOLVED)
            target_tag = available.get(target_tag_name)

            if target_tag:
                # Remove old status tags, add new one
                status_tag_names = set(tag_map.values())
                new_tags = [
                    t for t in thread.applied_tags if t.name not in status_tag_names
                ]
                new_tags.append(target_tag)

                if status == "solved":
                    await thread.edit(applied_tags=new_tags, archived=True, locked=True)
                else:
                    await thread.edit(applied_tags=new_tags)
            return

        # Regular thread mode — use name prefixes
        prefix_map = {
            "solved": STATUS_SOLVED,
            "needs_help": STATUS_NEEDS_HELP,
            "unsolved": STATUS_UNSOLVED,
            "in_progress": STATUS_UNSOLVED,
        }
        new_prefix = prefix_map.get(status, STATUS_UNSOLVED)

        current_name = thread.name
        for prefix in (STATUS_SOLVED, STATUS_NEEDS_HELP, STATUS_UNSOLVED):
            if current_name.startswith(prefix):
                current_name = current_name[len(prefix) :].lstrip()
                break

        new_name = f"{new_prefix} {current_name}"[:100]

        if status == "solved":
            await thread.edit(name=new_name, archived=True, locked=True)
        elif new_name != thread.name:
            await thread.edit(name=new_name)

    except discord.Forbidden:
        log.warning(f"Can't update thread {thread.id} — missing permissions")
    except Exception as e:
        log.warning(f"Failed to update thread: {e}")


def slugify(text: str) -> str:
    """Convert text to a URL/filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")
