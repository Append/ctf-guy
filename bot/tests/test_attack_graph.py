#!/usr/bin/env python3
"""Tests for attack graph generation."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_collector_records_tool_calls():
    """ToolCallCollector accumulates tool call nodes with sequential IDs."""
    from ai.attack_graph import ToolCallCollector

    c = ToolCallCollector()
    c.record_tool_call("Bash", "strings binary", 1000.0)
    c.record_tool_call("Read", "flag.txt", 1001.0)

    assert len(c.nodes) == 2
    assert c.nodes[0]["id"] == "t0"
    assert c.nodes[0]["tool_name"] == "Bash"
    assert c.nodes[0]["args_preview"] == "strings binary"
    assert c.nodes[0]["timestamp"] == 1000.0
    assert c.nodes[1]["id"] == "t1"


def test_collector_records_tool_results():
    """record_tool_result updates output_len on the most recent node."""
    from ai.attack_graph import ToolCallCollector

    c = ToolCallCollector()
    c.record_tool_call("Bash", "ls", 1000.0)
    c.record_tool_result(512)

    assert c.nodes[0]["output_len"] == 512


def test_collector_result_on_empty_is_safe():
    """record_tool_result on empty collector doesn't crash."""
    from ai.attack_graph import ToolCallCollector

    c = ToolCallCollector()
    c.record_tool_result(100)  # no-op, no crash


def test_collector_to_tool_layer():
    """to_tool_layer produces nodes and sequential edges."""
    from ai.attack_graph import ToolCallCollector

    c = ToolCallCollector()
    c.record_tool_call("Bash", "strings binary", 1000.0)
    c.record_tool_call("Read", "output.txt", 1001.0)
    c.record_tool_call("Bash", "python solve.py", 1002.0)

    layer = c.to_tool_layer()
    assert len(layer["nodes"]) == 3
    assert len(layer["edges"]) == 2
    assert layer["edges"][0] == {"from": "t0", "to": "t1", "type": "sequence"}
    assert layer["edges"][1] == {"from": "t1", "to": "t2", "type": "sequence"}


def test_collector_caps_at_200_nodes():
    """to_tool_layer truncates to 200 nodes max."""
    from ai.attack_graph import ToolCallCollector

    c = ToolCallCollector()
    for i in range(250):
        c.record_tool_call("Bash", f"cmd_{i}", 1000.0 + i)

    layer = c.to_tool_layer()
    assert len(layer["nodes"]) == 200
    assert len(layer["edges"]) == 199


def test_collector_truncates_args_preview():
    """args_preview is capped at 200 chars."""
    from ai.attack_graph import ToolCallCollector

    c = ToolCallCollector()
    c.record_tool_call("Bash", "x" * 500, 1000.0)

    assert len(c.nodes[0]["args_preview"]) == 200


def test_render_mermaid_single_approach():
    """Renders a simple single-approach graph to valid Mermaid."""
    from ai.attack_graph import render_mermaid

    graph = {
        "meta": {"challenge": "test", "attempt": 1, "model": "haiku", "outcome": "failed"},
        "tool_layer": {
            "nodes": [
                {"id": "t0", "tool_name": "Bash", "args_preview": "strings bin"},
                {"id": "t1", "tool_name": "Bash", "args_preview": "python solve.py"},
            ],
            "edges": [{"from": "t0", "to": "t1", "type": "sequence"}],
        },
        "approach_layer": {
            "nodes": [
                {"id": "a0", "label": "String analysis", "status": "abandoned", "tools": ["t0", "t1"]},
            ],
            "edges": [],
        },
        "unexplored": [
            {"approach": "Binary patching", "rationale": "Might bypass check", "priority": "high"},
        ],
    }

    mermaid = render_mermaid(graph)
    assert "flowchart TD" in mermaid
    assert "String analysis" in mermaid
    assert "Binary patching" in mermaid
    assert "abandoned" in mermaid.lower() or "style" in mermaid.lower()


def test_render_mermaid_multiple_approaches_with_pivot():
    """Renders pivot edges between approaches."""
    from ai.attack_graph import render_mermaid

    graph = {
        "meta": {"challenge": "test", "attempt": 1, "model": "haiku", "outcome": "failed"},
        "tool_layer": {"nodes": [], "edges": []},
        "approach_layer": {
            "nodes": [
                {"id": "a0", "label": "RSA small-e", "status": "abandoned", "tools": []},
                {"id": "a1", "label": "Factor n", "status": "tried", "tools": []},
            ],
            "edges": [
                {"from": "a0", "to": "a1", "type": "pivot", "reason": "small-e failed"},
            ],
        },
        "unexplored": [],
    }

    mermaid = render_mermaid(graph)
    assert "RSA small-e" in mermaid
    assert "Factor n" in mermaid
    assert "small-e failed" in mermaid


def test_render_mermaid_empty_approaches():
    """Handles graph with tool layer but no approach layer gracefully."""
    from ai.attack_graph import render_mermaid

    graph = {
        "meta": {"challenge": "test", "attempt": 1, "model": "haiku", "outcome": "failed"},
        "tool_layer": {
            "nodes": [{"id": "t0", "tool_name": "Bash", "args_preview": "ls"}],
            "edges": [],
        },
        "approach_layer": {"nodes": [], "edges": []},
        "unexplored": [],
    }

    mermaid = render_mermaid(graph)
    assert "flowchart TD" in mermaid
    # Should still render the tool node
    assert "Bash" in mermaid


def test_extract_approaches_returns_structured_data():
    """extract_approaches calls LLM and parses JSON response."""
    from ai.attack_graph import extract_approaches

    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = json.dumps(
        {
            "approach_layer": {
                "nodes": [
                    {"id": "a0", "label": "XOR decode", "status": "succeeded", "tools": ["t0", "t1"]},
                ],
                "edges": [],
            },
            "unexplored": [
                {"approach": "Base64 chain", "rationale": "Multiple layers", "priority": "medium"},
            ],
        }
    )

    with patch("ai.attack_graph.OpenRouterClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.chat_completion = AsyncMock(return_value=fake_response)
        mock_client_cls.return_value = mock_client

        class FakeConfig:
            openrouter_api_key = "test"
            triage_model = "flash"

        tool_nodes = [
            {"id": "t0", "tool_name": "Bash", "args_preview": "python decode.py"},
            {"id": "t1", "tool_name": "Read", "args_preview": "flag.txt"},
        ]

        result = asyncio.run(
            extract_approaches(
                solver_output="Decoded XOR, found flag",
                tool_nodes=tool_nodes,
                challenge_name="xor-fun",
                category="crypto",
                points=100,
                description="Decode the message",
                config=FakeConfig(),
            )
        )

    assert result is not None
    assert len(result["approach_layer"]["nodes"]) == 1
    assert result["approach_layer"]["nodes"][0]["label"] == "XOR decode"
    assert len(result["unexplored"]) == 1


def test_extract_approaches_returns_none_on_llm_failure():
    """extract_approaches returns None when LLM call fails."""
    from ai.attack_graph import extract_approaches

    with patch("ai.attack_graph.OpenRouterClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.chat_completion = AsyncMock(side_effect=Exception("API down"))
        mock_client_cls.return_value = mock_client

        class FakeConfig:
            openrouter_api_key = "test"
            triage_model = "flash"

        result = asyncio.run(
            extract_approaches(
                solver_output="tried stuff",
                tool_nodes=[],
                challenge_name="test",
                category="misc",
                points=50,
                description="test",
                config=FakeConfig(),
            )
        )

    assert result is None


def test_build_graph_assembles_all_layers():
    """build_graph combines meta, tool layer, and approach data."""
    from ai.attack_graph import ToolCallCollector, build_graph

    collector = ToolCallCollector()
    collector.record_tool_call("Bash", "strings bin", 1000.0)
    collector.record_tool_result(256)

    approach_data = {
        "approach_layer": {
            "nodes": [{"id": "a0", "label": "Strings search", "status": "tried", "tools": ["t0"]}],
            "edges": [],
        },
        "unexplored": [],
    }

    graph = build_graph(
        collector=collector,
        approach_data=approach_data,
        challenge_name="test-chall",
        model="haiku",
        outcome="failed",
        cost_usd=0.05,
        num_turns=10,
        duration_ms=30000,
    )

    assert graph["meta"]["challenge"] == "test-chall"
    assert graph["meta"]["model"] == "haiku"
    assert graph["meta"]["outcome"] == "failed"
    assert len(graph["tool_layer"]["nodes"]) == 1
    assert graph["approach_layer"]["nodes"][0]["label"] == "Strings search"


def test_build_graph_without_approach_data():
    """build_graph produces valid graph even when approach extraction failed."""
    from ai.attack_graph import ToolCallCollector, build_graph

    collector = ToolCallCollector()
    collector.record_tool_call("Bash", "ls", 1000.0)

    graph = build_graph(
        collector=collector,
        approach_data=None,
        challenge_name="test",
        model="haiku",
        outcome="failed",
        cost_usd=0.01,
        num_turns=3,
        duration_ms=5000,
    )

    assert len(graph["tool_layer"]["nodes"]) == 1
    assert graph["approach_layer"] == {"nodes": [], "edges": []}
    assert graph["unexplored"] == []


def test_generate_attack_graph_writes_json(tmp_path):
    """generate_attack_graph writes _attack_graph.json with graph list."""
    from ai.attack_graph import ToolCallCollector, generate_attack_graph

    collector = ToolCallCollector()
    collector.record_tool_call("Bash", "ls", 1000.0)

    fake_approach = {
        "approach_layer": {
            "nodes": [{"id": "a0", "label": "Recon", "status": "tried", "tools": ["t0"]}],
            "edges": [],
        },
        "unexplored": [{"approach": "Stego check", "rationale": "Image present", "priority": "high"}],
    }

    with patch("ai.attack_graph.extract_approaches", new_callable=AsyncMock, return_value=fake_approach):
        asyncio.run(
            generate_attack_graph(
                challenge_dir=str(tmp_path),
                solver_output="tried ls, no flag",
                collector=collector,
                thread=None,
                config=MagicMock(),
                challenge_name="test-chall",
                category="misc",
                points=50,
                description="A test challenge",
                model="haiku",
                flag_found=False,
                cost_usd=0.05,
                num_turns=5,
                duration_ms=10000,
            )
        )

    json_path = tmp_path / "_attack_graph.json"
    assert json_path.exists()
    graphs = json.loads(json_path.read_text())
    assert isinstance(graphs, list)
    assert len(graphs) == 1
    assert graphs[0]["meta"]["challenge"] == "test-chall"
    assert graphs[0]["meta"]["attempt"] == 1


def test_generate_attack_graph_appends_to_existing(tmp_path):
    """Second call appends to existing _attack_graph.json."""
    from ai.attack_graph import ToolCallCollector, generate_attack_graph

    existing = [
        {
            "meta": {"attempt": 1, "challenge": "test"},
            "tool_layer": {"nodes": [], "edges": []},
            "approach_layer": {"nodes": [], "edges": []},
            "unexplored": [],
        }
    ]
    (tmp_path / "_attack_graph.json").write_text(json.dumps(existing))

    collector = ToolCallCollector()
    collector.record_tool_call("Bash", "strings", 2000.0)

    with patch("ai.attack_graph.extract_approaches", new_callable=AsyncMock, return_value=None):
        asyncio.run(
            generate_attack_graph(
                challenge_dir=str(tmp_path),
                solver_output="tried strings",
                collector=collector,
                thread=None,
                config=MagicMock(),
                challenge_name="test",
                category="misc",
                points=50,
                description="test",
                model="opus",
                flag_found=False,
                cost_usd=0.10,
                num_turns=8,
                duration_ms=20000,
            )
        )

    graphs = json.loads((tmp_path / "_attack_graph.json").read_text())
    assert len(graphs) == 2
    assert graphs[1]["meta"]["attempt"] == 2
    assert graphs[1]["meta"]["model"] == "opus"


def test_generate_attack_graph_writes_mermaid(tmp_path):
    """generate_attack_graph writes _attack_graph.md."""
    from ai.attack_graph import ToolCallCollector, generate_attack_graph

    collector = ToolCallCollector()
    collector.record_tool_call("Bash", "ls", 1000.0)

    fake_approach = {
        "approach_layer": {
            "nodes": [{"id": "a0", "label": "Recon", "status": "tried", "tools": ["t0"]}],
            "edges": [],
        },
        "unexplored": [],
    }

    with patch("ai.attack_graph.extract_approaches", new_callable=AsyncMock, return_value=fake_approach):
        asyncio.run(
            generate_attack_graph(
                challenge_dir=str(tmp_path),
                solver_output="output",
                collector=collector,
                thread=None,
                config=MagicMock(),
                challenge_name="test",
                category="misc",
                points=50,
                description="test",
                model="haiku",
                flag_found=False,
                cost_usd=0.01,
                num_turns=3,
                duration_ms=5000,
            )
        )

    md_path = tmp_path / "_attack_graph.md"
    assert md_path.exists()
    content = md_path.read_text()
    assert "flowchart TD" in content
    assert "Recon" in content


def test_generate_attack_graph_skips_empty_collector(tmp_path):
    """Skips generation when collector has no tool calls."""
    from ai.attack_graph import ToolCallCollector, generate_attack_graph

    collector = ToolCallCollector()

    asyncio.run(
        generate_attack_graph(
            challenge_dir=str(tmp_path),
            solver_output="",
            collector=collector,
            thread=None,
            config=MagicMock(),
            challenge_name="test",
            category="misc",
            points=50,
            description="test",
            model="haiku",
            flag_found=False,
            cost_usd=0,
            num_turns=0,
            duration_ms=0,
        )
    )

    assert not (tmp_path / "_attack_graph.json").exists()
