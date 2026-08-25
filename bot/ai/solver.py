#!/usr/bin/env python3
"""Core AI solver — prompt assembly, tool_use loop, response posting."""

import asyncio
import json
import logging
from pathlib import Path

import discord
import openai

from ai.executor import execute_tool
from ai.learner import get_patterns_context
from ai.models import select_model
from ai.playbooks import load_playbook
from ai.tools import SOLVER_TOOLS
from config import Config
from db.conversations import add_message, get_history
from discord_ui.chunker import send_chunked

log = logging.getLogger(__name__)

# Limit concurrent AI calls
_semaphore = asyncio.Semaphore(5)

MAX_TOOL_ROUNDS = 20  # Safety limit on tool_use loop iterations


class Solver:
    def __init__(self, config: Config, db_conn):
        self.config = config
        self.db = db_conn
        self.client = openai.AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=config.openrouter_api_key,
        )

    async def solve(
        self,
        thread: discord.Thread,
        challenge_name: str,
        category: str,
        points: int,
        description: str,
        challenge_dir: str,
        approach: str | None = None,
    ) -> None:
        """Full solve attempt — loads playbook, runs tool_use loop."""
        model = select_model(category, points, self.config)
        playbook = load_playbook(category, self.config.ctf_root)

        # Build file listing for context
        file_listing = await self._get_file_listing(challenge_dir)

        # Inject learned patterns from previous solves
        patterns_ctx = get_patterns_context(category, self.config.ctf_root)

        system_prompt = self._build_system_prompt(
            playbook,
            challenge_name,
            category,
            points,
            description,
            challenge_dir,
            file_listing,
            approach,
            patterns_ctx,
        )

        await self._run_conversation(
            thread=thread,
            system_prompt=system_prompt,
            user_message="Solve this challenge. Start with recon on the files in the challenge directory.",
            challenge_dir=challenge_dir,
            model=model,
        )

        # Learn from the attempt (parse README if created)
        from ai.learner import learn_from_challenge

        learn_from_challenge(challenge_dir, self.config.ctf_root)

    async def respond(
        self,
        thread: discord.Thread,
        user_message: str,
        challenge_name: str,
        category: str,
        points: int,
        description: str,
        challenge_dir: str,
    ) -> None:
        """Respond to a user message in a challenge thread."""
        model = select_model(category, points, self.config)
        playbook = load_playbook(category, self.config.ctf_root)
        file_listing = await self._get_file_listing(challenge_dir)
        patterns_ctx = get_patterns_context(category, self.config.ctf_root)

        system_prompt = self._build_system_prompt(
            playbook,
            challenge_name,
            category,
            points,
            description,
            challenge_dir,
            file_listing,
            patterns_context=patterns_ctx,
        )

        await self._run_conversation(
            thread=thread,
            system_prompt=system_prompt,
            user_message=user_message,
            challenge_dir=challenge_dir,
            model=model,
        )

    async def _run_conversation(
        self,
        thread: discord.Thread,
        system_prompt: str,
        user_message: str,
        challenge_dir: str,
        model: str,
    ) -> list[dict]:
        """Run the tool_use conversation loop. Returns the conversation for learning."""
        async with _semaphore:
            # Load existing history
            history = get_history(self.db, str(thread.id))

            # Build messages for the API
            messages = [{"role": "system", "content": system_prompt}]
            for msg in history:
                messages.append({"role": msg.role, "content": msg.content})

            # Add the new user message
            messages.append({"role": "user", "content": user_message})
            add_message(self.db, str(thread.id), "user", user_message)

            # Loop detection — track recent tool call signatures
            recent_calls: list[str] = []

            for round_num in range(MAX_TOOL_ROUNDS):
                try:
                    response = await self.client.chat.completions.create(
                        model=model,
                        messages=messages,
                        tools=SOLVER_TOOLS,
                        max_tokens=8192,
                    )
                except openai.APIError as e:
                    await thread.send(f"API error: {e}")
                    return messages

                choice = response.choices[0]

                # Handle text response — this is what the user sees
                if choice.message.content:
                    await send_chunked(thread, choice.message.content)
                    add_message(
                        self.db,
                        str(thread.id),
                        "assistant",
                        choice.message.content,
                        model=model,
                    )

                # Handle tool calls — execute quietly, post summary only
                if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                    messages.append(choice.message.model_dump())

                    # Build a compact status line for all tool calls this round
                    tool_summaries = []

                    for tool_call in choice.message.tool_calls:
                        fn = tool_call.function
                        tool_name = fn.name
                        try:
                            arguments = json.loads(fn.arguments)
                        except json.JSONDecodeError:
                            arguments = {}

                        # Loop detection — check if we've seen this exact call recently
                        call_sig = f"{tool_name}:{json.dumps(arguments, sort_keys=True)[:200]}"
                        if recent_calls.count(call_sig) >= 2:
                            await thread.send(
                                "**Stuck in loop** — same tool call repeated 3 times. "
                                "Stopping. Try `/ask` with a different approach."
                            )
                            return messages
                        recent_calls.append(call_sig)
                        # Keep only last 10 calls for detection
                        if len(recent_calls) > 10:
                            recent_calls.pop(0)

                        # Execute the tool (no Discord output)
                        result = await execute_tool(tool_name, arguments, challenge_dir)

                        # Build summary line
                        status_icon = "+" if result.success else "x"
                        tool_summaries.append(f"`[{status_icon}]` {self._format_tool_short(tool_name, arguments)}")

                        # Add tool result to messages
                        # Include both tool_call_id and name for cross-provider compat
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": tool_name,
                                "content": result.output,
                            }
                        )

                        # Save to conversation DB
                        add_message(
                            self.db,
                            str(thread.id),
                            "tool",
                            result.output,
                            tool_results=[
                                {
                                    "tool": tool_name,
                                    "success": result.success,
                                }
                            ],
                        )

                    # Post a single compact status message for this round
                    status_line = f"**Round {round_num + 1}:** " + " | ".join(tool_summaries)
                    await thread.send(status_line)

                    continue

                # No tool calls, we're done
                break

            return messages

    def _build_system_prompt(
        self,
        playbook: str,
        challenge_name: str,
        category: str,
        points: int,
        description: str,
        challenge_dir: str,
        file_listing: str,
        approach: str | None = None,
        patterns_context: str = "",
    ) -> str:
        # Detect platform and flag format from challenge.json
        flag_format = "kernel{...}"
        platform_name = "CTFd"
        try:
            meta_path = Path(challenge_dir) / "challenge.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                plat = meta.get("platform", "")
                if plat == "picoctf":
                    flag_format = "picoCTF{...}"
                    platform_name = "picoCTF"
        except Exception:
            pass

        parts = [playbook]

        if patterns_context:
            parts.append(patterns_context)

        parts.append("\n\n---\n")
        parts.append(f"CHALLENGE CONTEXT\n{'=' * 40}")
        parts.append(f"Platform: {platform_name}")
        parts.append(f"Flag format: {flag_format}")
        parts.append(f"Name: {challenge_name}")
        parts.append(f"Category: {category}")
        parts.append(f"Points: {points}")
        parts.append(f"Working directory: {challenge_dir}")
        parts.append(f"\nDescription:\n{description}")
        parts.append(f"\nFiles in directory:\n{file_listing}")

        if approach:
            parts.append(f"\nOperator hint: {approach}")

        parts.append("\n\nIMPORTANT RULES:")
        parts.append(f"- Flag format is {flag_format} — search for this pattern, NOT other CTF formats.")
        parts.append("- You are operating as a Discord bot. Users see your text responses — be concise.")
        parts.append(
            "- READ THE DESCRIPTION CAREFULLY. It tells you what the challenge is about and often contains URLs or hints."
        )
        parts.append("- If the description mentions a URL, download link, or service — use curl to fetch it.")
        parts.append(
            "- Many challenges require downloading files or connecting to remote services. The challenge is NOT just local files."
        )
        parts.append(
            "- Do NOT repeat a failed tool call. If something fails, try a different approach or report the blocker."
        )
        parts.append(
            "- If a file download fails (AccessDenied, 403, etc.), say so and move on. Do NOT retry the same URL."
        )
        parts.append("- When you find the flag, write it to flag.txt and report it clearly.")
        parts.append("- Write your solve script to solve.py.")
        return "\n".join(parts)

    async def _get_file_listing(self, challenge_dir: str) -> str:
        """Get a file listing with file type info."""
        try:
            proc = await asyncio.create_subprocess_shell(
                'find . -maxdepth 2 -type f | head -50 | while read f; do echo "$(file --brief "$f") :: $f"; done',
                cwd=challenge_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return stdout.decode(errors="replace") or "(no files)"
        except (TimeoutError, Exception):
            return "(could not list files)"

    def _format_tool_short(self, tool_name: str, arguments: dict) -> str:
        """One-line summary of a tool call for the status line."""
        if tool_name == "run_python":
            lines = arguments.get("code", "").strip().split("\n")
            desc = lines[0][:50] if lines else "?"
            return f"python: {desc}"
        elif tool_name == "run_shell":
            return f"shell: {arguments.get('command', '?')[:50]}"
        elif tool_name == "read_file":
            return f"read {arguments.get('path', '?')}"
        elif tool_name == "write_file":
            return f"write {arguments.get('path', '?')}"
        elif tool_name == "list_files":
            return f"ls {arguments.get('path', '.')}"
        return tool_name
