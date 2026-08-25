#!/usr/bin/env python3
"""Tests for retry advisor."""

import asyncio
import json


def test_no_advice_without_progress(tmp_path):
    """Should return None when no prior attempts exist."""
    from ai.advisor import generate_retry_advice

    class FakeConfig:
        openrouter_api_key = ""
        triage_model = ""

    result = asyncio.run(generate_retry_advice(str(tmp_path), FakeConfig()))
    assert result is None


def test_reads_progress_md(tmp_path):
    """Should read progress.md when it exists (even if LLM fails)."""
    (tmp_path / "progress.md").write_text("## Attempt 1\nTried XOR decode, failed.")

    from ai.advisor import generate_retry_advice

    class FakeConfig:
        openrouter_api_key = ""  # No API key → LLM will fail
        triage_model = ""

    # LLM fails but function shouldn't crash
    result = asyncio.run(generate_retry_advice(str(tmp_path), FakeConfig()))
    # Returns None because LLM call fails (no API key)
    assert result is None


def test_reads_trace_jsonl(tmp_path):
    """Should parse trace.jsonl for attempt history."""
    (tmp_path / "progress.md").write_text("attempt")
    (tmp_path / "trace.jsonl").write_text(
        json.dumps({"type": "solve_start", "model": "haiku", "effort": "high"})
        + "\n"
        + json.dumps(
            {
                "type": "solve_complete",
                "flag_found": False,
                "num_turns": 15,
                "cost_usd": 0.5,
            }
        )
        + "\n"
    )

    from ai.advisor import generate_retry_advice

    class FakeConfig:
        openrouter_api_key = ""
        triage_model = ""

    # Won't generate advice (no API key) but shouldn't crash
    result = asyncio.run(generate_retry_advice(str(tmp_path), FakeConfig()))
    assert result is None


def test_reads_attempts_dir(tmp_path):
    """Should find failed solve scripts in _attempts/."""
    (tmp_path / "progress.md").write_text("attempt")
    attempt_dir = tmp_path / "_attempts" / "haiku-1"
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "solve.py").write_text("#!/usr/bin/env python3\n# failed exploit")

    from ai.advisor import generate_retry_advice

    class FakeConfig:
        openrouter_api_key = ""
        triage_model = ""

    result = asyncio.run(generate_retry_advice(str(tmp_path), FakeConfig()))
    assert result is None  # LLM fails but no crash


def test_reads_attack_graph_unexplored(tmp_path):
    """Should include unexplored paths from _attack_graph.json in context."""
    (tmp_path / "progress.md").write_text("attempt")
    (tmp_path / "_attack_graph.json").write_text(
        json.dumps(
            [
                {
                    "meta": {"attempt": 1, "challenge": "test"},
                    "tool_layer": {"nodes": [], "edges": []},
                    "approach_layer": {
                        "nodes": [{"id": "a0", "label": "XOR brute", "status": "abandoned", "tools": []}],
                        "edges": [],
                    },
                    "unexplored": [
                        {"approach": "Base64 chain", "rationale": "Multiple encoding layers", "priority": "high"},
                    ],
                }
            ]
        )
    )

    from ai.advisor import generate_retry_advice

    class FakeConfig:
        openrouter_api_key = ""
        triage_model = ""

    # LLM fails but shouldn't crash; the important thing is it didn't error on reading the graph
    result = asyncio.run(generate_retry_advice(str(tmp_path), FakeConfig()))
    assert result is None  # LLM fails, no crash
