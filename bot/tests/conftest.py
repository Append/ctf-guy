#!/usr/bin/env python3
"""Shared test fixtures."""

import sys
from pathlib import Path

import pytest

# Ensure bot/ is on the path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def tmp_ctf_root(tmp_path):
    """Create a temporary CTF root with challenges/ and solvers/ dirs."""
    (tmp_path / "challenges").mkdir()
    (tmp_path / "solvers" / "agents").mkdir(parents=True)
    (tmp_path / "solvers" / "patterns").mkdir(parents=True)
    (tmp_path / "solvers" / "agents" / "misc.md").write_text("# Misc Agent\nSolve misc challenges.")
    (tmp_path / "solvers" / "agents" / "crypto.md").write_text("# Crypto Agent\nSolve crypto challenges.")
    return tmp_path


@pytest.fixture
def db_conn(tmp_path):
    """SQLite connection with schema initialized."""
    from db.schema import init_db

    return init_db(tmp_path / "test.db")


@pytest.fixture
def mock_config(tmp_ctf_root):
    """Config with test values."""
    from config import Config

    return Config(
        discord_token="test",
        discord_guild_id=123,
        openrouter_api_key="test",
        ctfd_url="",
        ctfd_token="",
        ctfd_session="",
        allowed_user_ids=set(),
        allow_all_users=True,
        ctf_root=tmp_ctf_root,
        default_model="sonnet",
        heavy_model="opus",
        triage_model="flash",
        autosolve_model="haiku",
        autosolve_effort="high",
        autosolve_subagent="haiku",
        autosolve_concurrency=2,
        autosolve_timeout_base=180,
        autosolve_timeout_per_point=3,
        autosolve_timeout_max=600,
        autosolve_max_budget=1.0,
        race_enabled=False,
        race_models=["haiku", "opus"],
        race_timeouts={"haiku": 120},
        manager_max_interventions=10,
        manager_corrections=True,
        manager_advice_model="",
        deep_analysis_model="haiku",
        fast_mode=False,
        codex_enabled=False,
        ghidra_mcp_enabled=False,
        file_server_port=0,
        victoria_logs_url="",
        victoria_metrics_url="",
        file_server_bind="127.0.0.1",
        telemetry_batch_size=50,
        telemetry_flush_interval=1.0,
    )
