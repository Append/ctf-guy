#!/usr/bin/env python3
"""Retry advisor — generates approach advice from prior solve attempts.

When a challenge has been attempted before, reads progress.md, trace.jsonl,
and _attempts/ to understand what was tried. Calls Gemini Flash to produce
a concise tactical briefing for the next solver.
"""

import json
import logging
from pathlib import Path

from ai.openrouter import OpenRouterClient
from config import Config

log = logging.getLogger(__name__)

ADVISOR_PROMPT = """You are briefing a CTF solver about to retry a challenge that previous solvers failed on.

Given the prior attempt data, write a SHORT (under 150 words) tactical briefing:
1. What was already tried and why it failed (be specific)
2. What approach to try next (name specific tools, techniques, or attack vectors)
3. What to avoid (dead ends from prior attempts)

Do NOT repeat the challenge description. Do NOT be generic — reference what actually happened.
If the prior data shows the solver was close, say so and explain what was missing."""


async def generate_retry_advice(
    challenge_dir: str,
    config: Config,
) -> str | None:
    """Generate approach advice from prior solve attempts.

    Returns the advice string, or None if no prior attempts or generation fails.
    """
    chall_path = Path(challenge_dir)

    # Collect prior attempt data
    context_parts = []

    # 1. Progress.md — accumulated solver outputs and failure analyses
    progress_file = chall_path / "progress.md"
    if progress_file.exists():
        content = progress_file.read_text()[-3000:]
        context_parts.append(f"## Prior solver output:\n{content}")

    # 2. Trace.jsonl — structured lifecycle events
    trace_file = chall_path / "trace.jsonl"
    if trace_file.exists():
        try:
            lines = trace_file.read_text().strip().split("\n")
            events = [json.loads(l) for l in lines[-20:] if l.strip()]
            trace_summary = []
            for e in events:
                etype = e.get("type", "?")
                if etype == "solve_start":
                    trace_summary.append(f"- Attempt: model={e.get('model','?')}, effort={e.get('effort','?')}")
                elif etype == "solve_complete":
                    trace_summary.append(
                        f"- Result: flag_found={e.get('flag_found')}, "
                        f"turns={e.get('num_turns')}, cost=${e.get('cost_usd', 0):.2f}"
                    )
                elif etype == "failure_analysis":
                    trace_summary.append(f"- Analysis: {e.get('analysis', '')[:200]}")
            if trace_summary:
                context_parts.append(f"## Attempt history:\n" + "\n".join(trace_summary))
        except Exception:
            pass

    # 3. _attempts/ — failed solver scripts
    attempts_dir = chall_path / "_attempts"
    if attempts_dir.exists():
        attempt_dirs = sorted(attempts_dir.iterdir())
        for attempt in attempt_dirs[-3:]:  # Last 3 attempts
            solve_script = attempt / "solve.py"
            if solve_script.exists():
                script = solve_script.read_text()[:1000]
                context_parts.append(f"## Failed script from {attempt.name}:\n```python\n{script}\n```")

    # 4. Attack graph — unexplored paths from prior attempts
    graph_file = chall_path / "_attack_graph.json"
    if graph_file.exists():
        try:
            graphs = json.loads(graph_file.read_text())
            graph_summary = []
            for g in graphs[-3:]:  # Last 3 attempts
                approaches = g.get("approach_layer", {}).get("nodes", [])
                for a in approaches:
                    graph_summary.append(f"- Tried: {a['label']} ({a.get('status', '?')})")
                unexplored = g.get("unexplored", [])
                for u in unexplored:
                    graph_summary.append(
                        f"- UNEXPLORED ({u.get('priority', '?')}): {u['approach']} — {u.get('rationale', '')}"
                    )
            if graph_summary:
                context_parts.append(f"## Attack graph analysis:\n" + "\n".join(graph_summary))
        except Exception:
            pass

    if not context_parts:
        return None

    # 5. Challenge metadata
    challenge_meta = ""
    meta_file = chall_path / "challenge.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text())
            challenge_meta = (
                f"Challenge: {meta.get('name', '?')} ({meta.get('category', '?')}, "
                f"{meta.get('points', '?')}pt)\n"
                f"Description: {meta.get('description', '')[:300]}\n"
            )
        except Exception:
            pass

    # Generate advice via Gemini Flash
    try:
        client = OpenRouterClient(config)

        prior_data = "\n\n".join(context_parts)

        response = await client.chat_completion(
            model=config.triage_model,
            messages=[
                {"role": "system", "content": ADVISOR_PROMPT},
                {"role": "user", "content": f"{challenge_meta}\n{prior_data}"},
            ],
            max_tokens=300,
        )

        advice = response.choices[0].message.content
        if not advice or len(advice.strip()) < 20:
            return None

        # Save to file for prompt injection
        analysis_file = chall_path / "_prior_analysis.md"
        analysis_file.write_text(f"## Retry Advice\n\n{advice}\n")

        log.info(f"Generated retry advice for {chall_path.name}: {advice[:100]}...")
        return advice.strip()

    except Exception as e:
        log.warning(f"Retry advice generation failed: {e}")
        return None
