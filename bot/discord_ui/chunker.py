#!/usr/bin/env python3
"""Split long messages to fit Discord's 2000 character limit."""

import discord

MAX_LEN = 1900  # Leave buffer for formatting


async def send_chunked(
    channel: discord.abc.Messageable,
    text: str,
    silent: bool = True,
) -> list[discord.Message]:
    """Split text into chunks and send as multiple messages.

    Silent by default — no notification sound.
    Preserves code block fences across chunk boundaries.
    """
    if channel is None:
        return []
    chunks = chunk_text(text, MAX_LEN)
    messages = []
    for chunk in chunks:
        msg = await channel.send(chunk, silent=silent)
        messages.append(msg)
    return messages


def chunk_text(text: str, max_len: int = MAX_LEN) -> list[str]:
    """Split text into chunks, respecting newlines and code blocks."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    remaining = text
    in_code_block = False
    code_lang = ""

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # Find a good split point
        split_at = _find_split_point(remaining, max_len, in_code_block)
        chunk = remaining[:split_at]
        remaining = remaining[split_at:]

        # Track code block state
        fence_count = chunk.count("```")
        if fence_count % 2 == 1:
            # Odd number of fences — we're crossing a code block boundary
            if in_code_block:
                # Close the block at the end of this chunk
                chunk += "\n```"
                in_code_block = False
            else:
                # Find the language tag
                last_fence = chunk.rfind("```")
                after_fence = chunk[last_fence + 3 : last_fence + 20]
                code_lang = after_fence.split("\n")[0].strip()
                chunk += "\n```"
                in_code_block = True

        chunks.append(chunk)

        # Re-open code block in next chunk if needed
        if in_code_block and remaining:
            remaining = f"```{code_lang}\n{remaining}"

    return chunks


def _find_split_point(text: str, max_len: int, in_code_block: bool) -> int:
    """Find the best point to split text, preferring newline boundaries."""
    # Leave room for potential code block closing
    effective_max = max_len - 10 if in_code_block else max_len

    # Try to split at a double newline (paragraph break)
    idx = text.rfind("\n\n", 0, effective_max)
    if idx > effective_max // 2:
        return idx + 2

    # Try to split at a single newline
    idx = text.rfind("\n", 0, effective_max)
    if idx > effective_max // 3:
        return idx + 1

    # Try to split at a space
    idx = text.rfind(" ", 0, effective_max)
    if idx > effective_max // 3:
        return idx + 1

    # Hard split
    return effective_max
