#!/usr/bin/env python3
"""Attack graph generation — two-layer solve attempt visualization.

Generates a tool-call layer (deterministic, from stream events) and an
approach layer (LLM-extracted strategy clusters) after each solve attempt.
Outputs JSON, Mermaid, and PNG artifacts to the challenge directory.
"""

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path

from ai.openrouter import OpenRouterClient

log = logging.getLogger(__name__)


class ToolCallCollector:
    """Accumulates tool call events during Claude Code streaming.

    Instantiate per solve attempt and pass into _process_stream().
    After the solve, call to_tool_layer() to get the structured graph data.
    """

    def __init__(self):
        self.nodes: list[dict] = []
        self._counter: int = 0

    def record_tool_call(self, tool_name: str, args_preview: str, timestamp: float) -> str:
        node_id = f"t{self._counter}"
        self._counter += 1
        self.nodes.append(
            {
                "id": node_id,
                "type": "tool_call",
                "tool_name": tool_name,
                "args_preview": args_preview[:200],
                "output_len": 0,
                "timestamp": timestamp,
            }
        )
        return node_id

    def record_tool_result(self, output_len: int) -> None:
        if self.nodes:
            self.nodes[-1]["output_len"] = output_len

    def to_tool_layer(self) -> dict:
        nodes = self.nodes[:200]
        edges = [{"from": nodes[i]["id"], "to": nodes[i + 1]["id"], "type": "sequence"} for i in range(len(nodes) - 1)]
        return {"nodes": nodes, "edges": edges}


def _sanitize_mermaid(text: str) -> str:
    """Escape characters that break Mermaid syntax."""
    return text.replace('"', "'").replace("\n", " ").replace("[", "(").replace("]", ")")


def _truncate_label(text: str, max_len: int = 30) -> str:
    """Truncate edge labels so they don't crush the diagram."""
    text = _sanitize_mermaid(text)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


# Node shapes by status — different shapes communicate outcome at a glance
# succeeded: thick-bordered box, abandoned: hexagon, blocked: stadium, tried: box
_STATUS_SHAPE = {
    "succeeded": ('[["', '"]]'),  # double-bracket = subroutine (thick border)
    "abandoned": ('{{"', '"}}'),  # double-brace = hexagon
    "blocked": ('(["', '"])'),  # stadium shape
    "tried": ('["', '"]'),  # standard box
}


def render_mermaid(graph: dict) -> str:
    """Convert an attack graph dict to a Mermaid flowchart string."""
    lines = []
    meta = graph.get("meta", {})
    challenge = meta.get("challenge", "?")
    attempt = meta.get("attempt", "?")
    model = meta.get("model", "?")
    outcome = meta.get("outcome", "?")

    # Use top-down layout with config for thicker lines
    lines.append("---")
    lines.append("config:")
    lines.append("  theme: dark")
    lines.append("  themeVariables:")
    lines.append('    lineColor: "#ffffff"')
    lines.append("    lineWidth: 2")
    lines.append("---")
    lines.append("flowchart TD")

    approach_nodes = graph.get("approach_layer", {}).get("nodes", [])
    approach_edges = graph.get("approach_layer", {}).get("edges", [])
    tool_nodes = graph.get("tool_layer", {}).get("nodes", [])
    unexplored = graph.get("unexplored", [])

    # Title as a header node connected to first approach
    lines.append(f'    title["{challenge} | attempt {attempt} | {model}"]')
    lines.append("    style title fill:#1a1a2e,color:#e0e0e0,stroke:#e0e0e0,stroke-width:2px,font-size:16px")

    if approach_nodes:
        # Connect title to first approach
        lines.append(f"    title --> {approach_nodes[0]['id']}")

        for node in approach_nodes:
            nid = node["id"]
            label = _sanitize_mermaid(node["label"])
            status = node.get("status", "tried")
            tool_count = len(node.get("tools", []))
            open_shape, close_shape = _STATUS_SHAPE.get(status, _STATUS_SHAPE["tried"])
            status_icon = {
                "succeeded": "SOLVED",
                "abandoned": "DEAD END",
                "blocked": "BLOCKED",
                "tried": "EXPLORED",
            }.get(status, "EXPLORED")
            lines.append(f"    {nid}{open_shape}{label}<br/>{status_icon} · {tool_count} tools{close_shape}")

        # Style nodes by status
        for node in approach_nodes:
            nid = node["id"]
            status = node.get("status", "tried")
            if status == "succeeded":
                lines.append(f"    style {nid} fill:#1b5e20,color:#fff,stroke:#4caf50,stroke-width:3px,font-size:14px")
            elif status == "abandoned":
                lines.append(f"    style {nid} fill:#b71c1c,color:#fff,stroke:#ef5350,stroke-width:2px,font-size:14px")
            elif status == "blocked":
                lines.append(f"    style {nid} fill:#4a4a4a,color:#aaa,stroke:#777,stroke-width:2px,font-size:14px")
            else:
                lines.append(f"    style {nid} fill:#0d47a1,color:#fff,stroke:#42a5f5,stroke-width:2px,font-size:14px")

        # Edges with short labels and thick arrows
        for i, edge in enumerate(approach_edges):
            reason = _truncate_label(edge.get("reason", ""), 35)
            src, dst = edge["from"], edge["to"]
            if reason:
                lines.append(f'    {src} ==>|"{reason}"| {dst}')
            else:
                lines.append(f"    {src} ==> {dst}")
            lines.append(f"    linkStyle {i + 1} stroke:#ffffff,stroke-width:2px")

        # Unexplored paths branch off the last approach node
        if unexplored:
            last_approach = approach_nodes[-1]["id"]
            for j, ue in enumerate(unexplored):
                uid = f"u{j}"
                label = _truncate_label(ue["approach"], 25)
                priority = ue.get("priority", "medium")
                priority_icon = {"high": "!!!", "medium": "!!", "low": "!"}.get(priority, "!")
                lines.append(f'    {uid}("{priority_icon} {label}"):::unexplored')
                lines.append(f"    {last_approach} -.-> {uid}")
            lines.append(
                "    classDef unexplored fill:#e65100,color:#fff,stroke:#ff9800,"
                "stroke-width:2px,stroke-dasharray: 5 5,font-size:13px"
            )

    elif tool_nodes:
        # No approach layer — render raw tool nodes
        lines.append(f"    title --> {tool_nodes[0]['id']}")
        for node in tool_nodes[:20]:
            nid = node["id"]
            label = _truncate_label(f"{node['tool_name']}: {node['args_preview']}", 40)
            lines.append(f'    {nid}["{label}"]')
            lines.append(f"    style {nid} fill:#0d47a1,color:#fff,stroke:#42a5f5,stroke-width:2px")
        for i in range(min(len(tool_nodes), 20) - 1):
            lines.append(f"    {tool_nodes[i]['id']} ==> {tool_nodes[i + 1]['id']}")

    # Legend
    lines.append('    legend["Legend:<br/>Green = Solved | Red = Dead End | Blue = Explored | Orange = Unexplored"]')
    lines.append("    style legend fill:#1a1a2e,color:#aaa,stroke:#333,stroke-width:1px,font-size:11px")

    return "\n".join(lines)


APPROACH_EXTRACTION_PROMPT = """You are analyzing a CTF solve attempt. Given the solver's output and tool calls, extract the high-level approaches that were tried.

Respond with JSON ONLY (no markdown fences, no explanation). Use this exact schema:

{
  "approach_layer": {
    "nodes": [
      {"id": "a0", "label": "short approach name", "status": "tried|succeeded|abandoned|blocked", "tools": ["t0", "t1"]}
    ],
    "edges": [
      {"from": "a0", "to": "a1", "type": "pivot|dependency|escalation", "reason": "why the solver switched"}
    ]
  },
  "unexplored": [
    {"approach": "name", "rationale": "why this might work", "priority": "high|medium|low"}
  ]
}

Rules:
- Each approach clusters related tool calls into a named strategy
- Status: "succeeded" if it led to the flag, "abandoned" if the solver moved on, "blocked" if it hit an error, "tried" if unclear
- Edges connect approaches in the order they were attempted, with the reason for pivoting
- Suggest 2-5 unexplored paths the solver didn't try, prioritized by likelihood of success
- Keep approach labels short (under 6 words)
- Reference tool node IDs (t0, t1, ...) in the tools arrays"""


async def extract_approaches(
    solver_output: str,
    tool_nodes: list[dict],
    challenge_name: str,
    category: str,
    points: int,
    description: str,
    config,
) -> dict | None:
    """Call triage model to extract approach layer from solver output.

    Returns the approach_layer + unexplored dict, or None on failure.
    """
    tool_summary = json.dumps(
        [{"id": n["id"], "tool_name": n["tool_name"], "args_preview": n["args_preview"]} for n in tool_nodes[:100]],
        indent=2,
    )

    truncated_output = solver_output[-6000:] if len(solver_output) > 6000 else solver_output

    user_content = (
        f"Challenge: {challenge_name} ({category}, {points}pts)\n"
        f"Description: {description[:500]}\n\n"
        f"Tool calls (chronological):\n{tool_summary}\n\n"
        f"Solver output (last portion):\n{truncated_output}"
    )

    try:
        client = OpenRouterClient(config)

        response = await client.chat_completion(
            model=config.triage_model,
            messages=[
                {"role": "system", "content": APPROACH_EXTRACTION_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=1000,
        )

        text = response.choices[0].message.content
        if not text:
            return None

        # Strip markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        data = json.loads(text)

        # Validate minimum structure
        if "approach_layer" not in data:
            return None

        return data

    except Exception as e:
        log.warning(f"Approach extraction failed: {e}")
        return None


def build_graph(
    collector: ToolCallCollector,
    approach_data: dict | None,
    challenge_name: str,
    model: str,
    outcome: str,
    cost_usd: float,
    num_turns: int,
    duration_ms: int,
) -> dict:
    """Assemble a complete attack graph from collected data."""
    from datetime import datetime, timezone

    graph = {
        "meta": {
            "challenge": challenge_name,
            "attempt": 0,  # set by caller based on existing graphs
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "outcome": outcome,
            "cost_usd": cost_usd,
            "num_turns": num_turns,
            "duration_ms": duration_ms,
        },
        "tool_layer": collector.to_tool_layer(),
        "approach_layer": (
            approach_data.get("approach_layer", {"nodes": [], "edges": []})
            if approach_data
            else {"nodes": [], "edges": []}
        ),
        "unexplored": (approach_data.get("unexplored", []) if approach_data else []),
    }

    return graph


async def _render_png(mermaid_text: str, output_path: Path) -> bool:
    """Render Mermaid text to PNG via mmdc. Returns True on success."""
    mmdc = shutil.which("mmdc")
    if not mmdc:
        log.info("mmdc not found — skipping PNG render")
        return False

    input_path = output_path.with_suffix(".mmd")
    try:
        input_path.write_text(mermaid_text)
        proc = await asyncio.create_subprocess_exec(
            mmdc,
            "-i",
            str(input_path),
            "-o",
            str(output_path),
            "-b",
            "#1a1a2e",
            "--scale",
            "3",
            "-w",
            "1200",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            log.warning(f"mmdc failed: {stderr.decode()[:200]}")
            return False
        return output_path.exists()
    except Exception as e:
        log.warning(f"PNG render failed: {e}")
        return False
    finally:
        input_path.unlink(missing_ok=True)


async def generate_attack_graph(
    challenge_dir: str,
    solver_output: str,
    collector: ToolCallCollector,
    thread,
    config,
    challenge_name: str,
    category: str,
    points: int,
    description: str,
    model: str,
    flag_found: bool,
    cost_usd: float,
    num_turns: int,
    duration_ms: int,
) -> None:
    """Generate attack graph artifacts after a solve attempt.

    Writes _attack_graph.json, _attack_graph.md, and optionally _attack_graph.png.
    Posts the graph to the Discord thread if provided.
    Safe to call as fire-and-forget (catches all exceptions).
    """
    try:
        if not collector.nodes:
            log.info("No tool calls recorded — skipping attack graph")
            return

        chall_path = Path(challenge_dir)
        outcome = "solved" if flag_found else "failed"

        # Extract approach layer via LLM
        approach_data = await extract_approaches(
            solver_output=solver_output,
            tool_nodes=collector.nodes,
            challenge_name=challenge_name,
            category=category,
            points=points,
            description=description,
            config=config,
        )

        # Load existing graphs to determine attempt number
        json_path = chall_path / "_attack_graph.json"
        existing_graphs = []
        if json_path.exists():
            try:
                existing_graphs = json.loads(json_path.read_text())
            except (json.JSONDecodeError, Exception):
                existing_graphs = []

        attempt_num = len(existing_graphs) + 1

        # Build the graph
        graph = build_graph(
            collector=collector,
            approach_data=approach_data,
            challenge_name=challenge_name,
            model=model,
            outcome=outcome,
            cost_usd=cost_usd,
            num_turns=num_turns,
            duration_ms=duration_ms,
        )
        graph["meta"]["attempt"] = attempt_num

        # Write JSON (append)
        existing_graphs.append(graph)
        json_path.write_text(json.dumps(existing_graphs, indent=2))

        # Write Mermaid (overwrite with latest)
        mermaid_text = render_mermaid(graph)
        md_path = chall_path / "_attack_graph.md"
        md_path.write_text(mermaid_text)

        # Render PNG (optional — mmdc may not be installed)
        png_path = chall_path / "_attack_graph.png"
        await _render_png(mermaid_text, png_path)

        # Post to Discord
        if thread is not None:
            import discord as _discord

            from ai.claude_code import _safe_send

            n_approaches = len(graph.get("approach_layer", {}).get("nodes", []))
            n_unexplored = len(graph.get("unexplored", []))
            summary = (
                f"**Attack Graph** (attempt {attempt_num}): "
                f"{n_approaches} approach(es) tried, {n_unexplored} unexplored path(s)"
            )

            if png_path.exists() and png_path.stat().st_size > 0:
                await _safe_send(thread, summary, file=_discord.File(str(png_path), filename="attack_graph.png"))
            else:
                # mmdc unavailable — post summary only, graph is in challenge dir
                await _safe_send(thread, f"{summary}\n(PNG render unavailable — see `_attack_graph.md`)")

        log.info(f"Attack graph generated for {challenge_name} (attempt {attempt_num})")

    except Exception as e:
        log.warning(f"Attack graph generation failed: {e}")
