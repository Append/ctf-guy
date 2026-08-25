#!/usr/bin/env python3
"""Trace analysis for failed solve attempts.

Uses the triage model (Gemini Flash via OpenRouter) to analyze
what went wrong and suggest next steps.
"""

import logging
from pathlib import Path

from ai.openrouter import OpenRouterClient
from config import Config

log = logging.getLogger(__name__)

ANALYSIS_PROMPT = """You are a CTF solve attempt analyzer. A solver tried to solve a challenge but failed to find the flag.

Analyze the solve attempt output and provide a BRIEF failure analysis. Format as markdown bullets:

- **Tried:** What approaches/tools were used (1-2 bullets max)
- **Stuck on:** Why it failed or where it got stuck (1 bullet)
- **Next step:** What to try next, formatted as a suggested command: `/solve approach:your suggestion here`

Keep the total response under 200 words. Be specific and actionable."""


async def analyze_failure(
    output: str,
    challenge_name: str,
    category: str,
    points: int,
    description: str,
    config: Config,
) -> str | None:
    """Analyze a failed solve attempt and return actionable feedback.

    Returns a markdown string with failure analysis, or None on error.
    """
    if not output or not output.strip():
        return None

    # Truncate output to last 4000 chars (most relevant info is at the end)
    truncated = output[-4000:] if len(output) > 4000 else output

    try:
        client = OpenRouterClient(config)

        response = await client.chat_completion(
            model=config.triage_model,
            messages=[
                {"role": "system", "content": ANALYSIS_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Challenge: {challenge_name} ({points}pts, {category})\n"
                        f"Description: {description[:500]}\n\n"
                        f"Solve attempt output (last portion):\n{truncated}"
                    ),
                },
            ],
            max_tokens=500,
        )

        analysis = response.choices[0].message.content
        if analysis:
            log.info(f"Trace analysis for {challenge_name}: {analysis[:100]}...")
        return analysis

    except Exception as e:
        log.warning(f"Trace analysis failed for {challenge_name}: {e}")
        return None


async def analyze_and_post(
    thread,
    output: str,
    challenge_name: str,
    category: str,
    points: int,
    description: str,
    challenge_dir: str,
    config: Config,
) -> None:
    """Analyze a failure and post results to Discord thread + progress.md.

    Safe to call as fire-and-forget (catches all exceptions).
    """
    try:
        analysis = await analyze_failure(
            output,
            challenge_name,
            category,
            points,
            description,
            config,
        )
        if not analysis:
            return

        # Post to Discord
        from ai.claude_code import _safe_send

        await _safe_send(thread, f"**Failure Analysis:**\n{analysis}")

        # Append to progress.md
        progress_file = Path(challenge_dir) / "progress.md"
        try:
            with open(progress_file, "a") as f:
                f.write(f"\n### Failure Analysis\n{analysis}\n")
        except Exception:
            pass

    except Exception as e:
        log.warning(f"analyze_and_post failed: {e}")
