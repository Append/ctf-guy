#!/usr/bin/env python3
"""Discord embed builders for challenge info and status."""

import discord

from ai.playbooks import CATEGORY_COLORS, normalize_category
from db.challenges import ChallengeRecord


def challenge_embed(
    name: str,
    category: str,
    points: int,
    description: str,
    files: list[str] | None = None,
    solves: int = 0,
    files_url: str | None = None,
) -> discord.Embed:
    """Build an embed for a challenge thread's initial message."""
    color = CATEGORY_COLORS.get(normalize_category(category), 0x7F8C8D)
    embed = discord.Embed(
        title=f"{name}",
        description=_truncate(description, 4000),
        color=color,
    )
    embed.add_field(name="Category", value=category, inline=True)
    embed.add_field(name="Points", value=str(points), inline=True)
    embed.add_field(name="Solves", value=str(solves), inline=True)

    if files:
        file_list = "\n".join(f"- `{f.split('/')[-1]}`" for f in files[:10])
        embed.add_field(name="Files", value=file_list, inline=False)

    if files_url:
        embed.add_field(name="Local Files", value=f"[Browse]({files_url})", inline=False)

    embed.set_footer(text="Use /solve to start AI solving | /ask to ask a question")
    return embed


def triage_embed(
    ctf_name: str,
    challenges: list[dict],
) -> discord.Embed:
    """Build a triage summary embed after scouting."""
    embed = discord.Embed(
        title=f"CTF Scouted: {ctf_name}",
        color=0x2ECC71,
    )

    # Group by category
    by_cat: dict[str, list[dict]] = {}
    for c in challenges:
        cat = c.get("category", "misc").lower()
        by_cat.setdefault(cat, []).append(c)

    total_points = sum(c.get("value", 0) for c in challenges)

    for cat, challs in sorted(by_cat.items()):
        points = sum(c.get("value", 0) for c in challs)
        lines = []
        for c in sorted(challs, key=lambda x: x.get("value", 0)):
            status = "solved" if c.get("solved_by_me") else f"{c.get('value', '?')}pt"
            lines.append(f"`{status}` {c['name']}")
        embed.add_field(
            name=f"{cat.upper()} ({len(challs)} challenges, {points}pts)",
            value="\n".join(lines[:10]),
            inline=False,
        )

    embed.set_footer(text=f"{len(challenges)} challenges | {total_points} total points")
    return embed


def status_embed(
    ctf_name: str,
    challenges: list[ChallengeRecord],
) -> discord.Embed:
    """Build a status/scoreboard embed."""
    solved = [c for c in challenges if c.solved]
    unsolved = [c for c in challenges if not c.solved]
    total_points = sum(c.points for c in challenges)
    captured_points = sum(c.points for c in solved)

    embed = discord.Embed(
        title=f"Status: {ctf_name}",
        color=0x3498DB,
    )
    embed.add_field(name="Solved", value=f"{len(solved)}/{len(challenges)}", inline=True)
    embed.add_field(name="Points", value=f"{captured_points}/{total_points}", inline=True)

    # Per-category breakdown
    by_cat: dict[str, tuple[int, int]] = {}  # cat -> (solved, total)
    for c in challenges:
        cat = c.category.lower()
        s, t = by_cat.get(cat, (0, 0))
        by_cat[cat] = (s + (1 if c.solved else 0), t + 1)

    breakdown = []
    for cat, (s, t) in sorted(by_cat.items()):
        bar = "=" * s + "-" * (t - s)
        breakdown.append(f"`[{bar}]` **{cat}** {s}/{t}")

    if breakdown:
        embed.add_field(name="Categories", value="\n".join(breakdown), inline=False)

    if unsolved:
        easiest = sorted(unsolved, key=lambda c: c.points)[:5]
        next_up = "\n".join(f"- {c.name} ({c.points}pt, {c.category})" for c in easiest)
        embed.add_field(name="Next targets", value=next_up, inline=False)

    return embed


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
