#!/usr/bin/env python3
"""Tests for bot/ai/deep_solve.py — prompt builders and merge logic."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: patch ai.claude_code (and its transitive discord deps) only for
# the duration of importing ai.deep_solve.  We remove the stubs from
# sys.modules immediately afterwards so other test modules that import the
# *real* ai.claude_code are not affected.
# ---------------------------------------------------------------------------
_STUB_KEYS = ("discord", "discord.player", "discord_ui", "discord_ui.chunker", "ai.claude_code")

_pre_existing = {k: sys.modules[k] for k in _STUB_KEYS if k in sys.modules}

_cc_stub = MagicMock()
_cc_stub.SolveResult = MagicMock
_cc_stub.solve_with_claude_code = MagicMock()

for _k in _STUB_KEYS:
    if _k not in sys.modules:
        sys.modules[_k] = MagicMock() if _k != "ai.claude_code" else _cc_stub

sys.modules["ai.claude_code"] = _cc_stub  # ensure our stub wins

import ai.deep_solve as _ds  # noqa: E402 — import after stubs are in place

# Restore sys.modules: remove stubs that weren't there before so later imports
# of the real ai.claude_code (e.g. test_mcp_config) work correctly.
for _k in _STUB_KEYS:
    if _k not in _pre_existing:
        sys.modules.pop(_k, None)
    else:
        sys.modules[_k] = _pre_existing[_k]

from ai.deep_solve import (  # noqa: E402
    _DEEP_ANALYSIS,
    _INFRA_ANALYSIS,
    _SOURCE_ANALYSIS,
    _TEARDOWN_TIMEOUT,
    _build_infra_analyst_prompt,
    _build_source_analyst_prompt,
    _merge_analyses,
    _run_infra_analyst,
    _run_source_analyst,
    _run_teardown,
    deep_solve,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_challenge(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """Create a minimal challenge directory, optionally populating files."""
    cdir = tmp_path / "test_challenge"
    cdir.mkdir()
    if files:
        for name, content in files.items():
            (cdir / name).write_text(content)
    return cdir


# ---------------------------------------------------------------------------
# Source analyst prompt tests
# ---------------------------------------------------------------------------


class TestBuildSourceAnalystPrompt:
    def test_includes_file_listing(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"app.py": "print('hello')", "README.md": "# CTF"})
        prompt = _build_source_analyst_prompt(cdir)
        assert "app.py" in prompt
        assert "README.md" in prompt

    def test_includes_output_filename(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"app.py": "x=1"})
        prompt = _build_source_analyst_prompt(cdir)
        assert _SOURCE_ANALYSIS in prompt

    def test_empty_directory_shows_no_files_message(self, tmp_path):
        cdir = _make_challenge(tmp_path)
        prompt = _build_source_analyst_prompt(cdir)
        assert "(no files found)" in prompt

    def test_skips_underscore_prefixed_files(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"app.py": "x=1", "_source_analysis.md": "internal"})
        prompt = _build_source_analyst_prompt(cdir)
        assert "app.py" in prompt
        # Extract only the file-listing block to avoid false-positive from the output
        # filename reference elsewhere in the prompt.
        listing_block = prompt.split("## Files to Analyse")[1].split("## Your Task")[0]
        assert "_source_analysis.md" not in listing_block

    def test_skips_hidden_files(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"app.py": "x=1", ".env": "SECRET=abc"})
        prompt = _build_source_analyst_prompt(cdir)
        assert "app.py" in prompt
        listing_block = prompt.split("## Files to Analyse")[1].split("## Your Task")[0]
        assert ".env" not in listing_block

    def test_includes_challenge_json_metadata(self, tmp_path):
        cdir = _make_challenge(tmp_path)
        meta = {"name": "Super Challenge", "category": "web", "description": "exploit it", "points": 200}
        (cdir / "challenge.json").write_text(json.dumps(meta))
        prompt = _build_source_analyst_prompt(cdir)
        assert "Super Challenge" in prompt
        assert "web" in prompt
        assert "exploit it" in prompt
        assert "200" in prompt

    def test_challenge_json_missing_is_handled(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"server.py": "pass"})
        # No challenge.json — should not raise
        prompt = _build_source_analyst_prompt(cdir)
        assert "server.py" in prompt

    def test_prompt_covers_required_sections(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"main.c": "int main(){}"})
        prompt = _build_source_analyst_prompt(cdir)
        for section in ("Endpoints", "Data Flow", "Security Constraints", "Attack Surface", "Interesting Patterns"):
            assert section in prompt, f"Missing section: {section}"

    def test_challenge_directory_path_in_prompt(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"app.py": "pass"})
        prompt = _build_source_analyst_prompt(cdir)
        assert str(cdir) in prompt


# ---------------------------------------------------------------------------
# Infra analyst prompt tests
# ---------------------------------------------------------------------------


class TestBuildInfraAnalystPrompt:
    def test_dockerfile_triggers_build_instructions(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"Dockerfile": "FROM ubuntu"})
        prompt = _build_infra_analyst_prompt(cdir)
        assert "docker build" in prompt.lower()
        assert "challenge-local" in prompt

    def test_dockerfile_prompt_mentions_infra_output_file(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"Dockerfile": "FROM ubuntu"})
        prompt = _build_infra_analyst_prompt(cdir)
        assert _INFRA_ANALYSIS in prompt

    def test_compose_yml_mentions_docker_compose(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"docker-compose.yml": "version: '3'"})
        prompt = _build_infra_analyst_prompt(cdir)
        assert "compose" in prompt.lower()

    def test_compose_yaml_extension_also_detected(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"docker-compose.yaml": "version: '3'"})
        prompt = _build_infra_analyst_prompt(cdir)
        assert "compose" in prompt.lower()

    def test_no_dockerfile_does_static_analysis(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"nginx.conf": "server {}"})
        prompt = _build_infra_analyst_prompt(cdir)
        # Should mention static analysis, not Docker build
        assert "static" in prompt.lower() or "No Dockerfile" in prompt
        assert "Dockerfile" not in prompt or "No Dockerfile" in prompt

    def test_static_analysis_lists_config_files(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"nginx.conf": "server {}", "settings.ini": "[db]\nhost=localhost"})
        prompt = _build_infra_analyst_prompt(cdir)
        assert "nginx.conf" in prompt
        assert "settings.ini" in prompt

    def test_static_analysis_no_config_files_shows_message(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"binary": "\x7fELF"})
        prompt = _build_infra_analyst_prompt(cdir)
        assert "(no config files found)" in prompt

    def test_infra_prompt_covers_required_sections_docker(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"Dockerfile": "FROM alpine"})
        prompt = _build_infra_analyst_prompt(cdir)
        for section in ("Services", "Ports", "Configuration", "Network"):
            assert section in prompt, f"Missing section: {section}"

    def test_infra_prompt_covers_required_sections_static(self, tmp_path):
        cdir = _make_challenge(tmp_path, {"app.conf": "port=8080"})
        prompt = _build_infra_analyst_prompt(cdir)
        for section in ("Service Configuration", "Secrets", "Security Settings"):
            assert section in prompt, f"Missing section: {section}"


# ---------------------------------------------------------------------------
# Merge tests
# ---------------------------------------------------------------------------


class TestMergeAnalyses:
    def test_merge_combines_both_analyses(self, tmp_path):
        cdir = _make_challenge(tmp_path)
        src = cdir / _SOURCE_ANALYSIS
        inf = cdir / _INFRA_ANALYSIS
        src.write_text("Source findings here.")
        inf.write_text("Infra findings here.")

        out = _merge_analyses(cdir, src, inf)

        assert out is not None
        assert out == cdir / _DEEP_ANALYSIS
        text = out.read_text()
        assert "Source Analysis" in text
        assert "Source findings here." in text
        assert "Infrastructure Analysis" in text
        assert "Infra findings here." in text

    def test_merge_source_only(self, tmp_path):
        cdir = _make_challenge(tmp_path)
        src = cdir / _SOURCE_ANALYSIS
        src.write_text("Only source.")

        out = _merge_analyses(cdir, src, None)

        assert out is not None
        text = out.read_text()
        assert "Source Analysis" in text
        assert "Only source." in text
        assert "Infrastructure Analysis" not in text

    def test_merge_infra_only(self, tmp_path):
        cdir = _make_challenge(tmp_path)
        inf = cdir / _INFRA_ANALYSIS
        inf.write_text("Only infra.")

        out = _merge_analyses(cdir, None, inf)

        assert out is not None
        text = out.read_text()
        assert "Infrastructure Analysis" in text
        assert "Only infra." in text
        assert "Source Analysis" not in text

    def test_merge_returns_none_when_both_missing(self, tmp_path):
        cdir = _make_challenge(tmp_path)
        out = _merge_analyses(cdir, None, None)
        assert out is None

    def test_merge_returns_none_when_paths_dont_exist(self, tmp_path):
        cdir = _make_challenge(tmp_path)
        ghost_src = cdir / "nonexistent_source.md"
        ghost_inf = cdir / "nonexistent_infra.md"
        out = _merge_analyses(cdir, ghost_src, ghost_inf)
        assert out is None

    def test_merge_returns_none_when_files_empty(self, tmp_path):
        cdir = _make_challenge(tmp_path)
        src = cdir / _SOURCE_ANALYSIS
        inf = cdir / _INFRA_ANALYSIS
        src.write_text("")
        inf.write_text("   ")

        out = _merge_analyses(cdir, src, inf)
        assert out is None

    def test_merge_separator_between_sections(self, tmp_path):
        cdir = _make_challenge(tmp_path)
        src = cdir / _SOURCE_ANALYSIS
        inf = cdir / _INFRA_ANALYSIS
        src.write_text("Source.")
        inf.write_text("Infra.")

        out = _merge_analyses(cdir, src, inf)
        assert out is not None
        text = out.read_text()
        assert "---" in text

    def test_merge_writes_to_correct_path(self, tmp_path):
        cdir = _make_challenge(tmp_path)
        src = cdir / _SOURCE_ANALYSIS
        src.write_text("Some analysis.")

        out = _merge_analyses(cdir, src, None)

        assert out == cdir / _DEEP_ANALYSIS
        assert (cdir / _DEEP_ANALYSIS).exists()


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------


def test_constants_have_expected_values():
    assert _SOURCE_ANALYSIS == "_source_analysis.md"
    assert _INFRA_ANALYSIS == "_infra_analysis.md"
    assert _DEEP_ANALYSIS == "_deep_analysis.md"
    assert _TEARDOWN_TIMEOUT == 120


# ---------------------------------------------------------------------------
# Async orchestration tests
# ---------------------------------------------------------------------------


def _make_fake_config(model="claude-haiku-4-5"):
    cfg = MagicMock()
    cfg.deep_analysis_model = model
    return cfg


@pytest.mark.asyncio
async def test_run_teardown_dispatches_both_subagents(tmp_path):
    """Both analyst subagents are called and their outputs are merged."""
    cdir = _make_challenge(tmp_path, {"app.py": "secret = 'flag'"})
    cfg = _make_fake_config()

    call_count = 0

    async def fake_solve(thread, challenge_dir, prompt, timeout, model, effort, category=None, **kwargs):
        nonlocal call_count
        call_count += 1
        # Write the output file the subagent is supposed to produce
        if "_source_analysis" in prompt or "Source Analyst" in prompt:
            (Path(challenge_dir) / _SOURCE_ANALYSIS).write_text("Source findings.")
        if "_infra_analysis" in prompt or "Infra Analyst" in prompt:
            (Path(challenge_dir) / _INFRA_ANALYSIS).write_text("Infra findings.")

    with patch("ai.deep_solve.solve_with_claude_code", side_effect=fake_solve):
        result = await _run_teardown(cdir, cfg)

    assert call_count == 2
    assert result is not None
    assert result == cdir / _DEEP_ANALYSIS
    merged = result.read_text()
    assert "Source findings." in merged
    assert "Infra findings." in merged


@pytest.mark.asyncio
async def test_run_teardown_partial_failure(tmp_path):
    """One analyst fails (no output), the other succeeds — merged file contains the successful output."""
    cdir = _make_challenge(tmp_path, {"app.py": "x=1"})
    cfg = _make_fake_config()

    async def fake_solve(thread, challenge_dir, prompt, timeout, model, effort, category=None, **kwargs):
        # Only write infra output; source analyst produces nothing
        if "Infra Analyst" in prompt:
            (Path(challenge_dir) / _INFRA_ANALYSIS).write_text("Infra only.")

    with patch("ai.deep_solve.solve_with_claude_code", side_effect=fake_solve):
        result = await _run_teardown(cdir, cfg)

    assert result is not None
    text = result.read_text()
    assert "Infra only." in text
    assert "Source Analysis" not in text


@pytest.mark.asyncio
async def test_run_teardown_both_fail(tmp_path):
    """Both analysts produce no output — _run_teardown returns None."""
    cdir = _make_challenge(tmp_path, {"app.py": "x=1"})
    cfg = _make_fake_config()

    async def fake_solve(thread, challenge_dir, prompt, timeout, model, effort, category=None, **kwargs):
        # Write nothing — simulates failed/silent subagent
        pass

    with patch("ai.deep_solve.solve_with_claude_code", side_effect=fake_solve):
        result = await _run_teardown(cdir, cfg)

    assert result is None
    assert not (cdir / _DEEP_ANALYSIS).exists()


@pytest.mark.asyncio
async def test_deep_solve_runs_teardown_then_solver(tmp_path):
    """Teardown subagents (timeout=120) run before the main solver (different timeout)."""
    cdir = _make_challenge(tmp_path, {"app.py": "flag = 'kernel{test}'"})
    cfg = _make_fake_config()

    calls = []  # (timeout, prompt_snippet)

    async def fake_solve(
        thread,
        challenge_dir,
        prompt,
        timeout=600,
        model=None,
        effort=None,
        subagent_model=None,
        max_budget=None,
        event_callback=None,
        category=None,
        **kwargs,
    ):
        calls.append({"timeout": timeout, "prompt": prompt[:60]})
        # Teardown subagents write their files
        if timeout == _TEARDOWN_TIMEOUT:
            if "Source Analyst" in prompt:
                (Path(challenge_dir) / _SOURCE_ANALYSIS).write_text("Source analysis content.")
            elif "Infra Analyst" in prompt:
                (Path(challenge_dir) / _INFRA_ANALYSIS).write_text("Infra analysis content.")
        return MagicMock()  # SolveResult-like

    solver_timeout = 300
    with patch("ai.deep_solve.solve_with_claude_code", side_effect=fake_solve):
        await deep_solve(
            thread=None,
            challenge_dir=cdir,
            prompt="Solve this challenge.",
            config=cfg,
            solver_timeout=solver_timeout,
        )

    # Two teardown calls (timeout=120) then one main solver call (timeout=300)
    teardown_calls = [c for c in calls if c["timeout"] == _TEARDOWN_TIMEOUT]
    solver_calls = [c for c in calls if c["timeout"] == solver_timeout]
    assert len(teardown_calls) == 2
    assert len(solver_calls) == 1

    # Merged analysis file should exist
    assert (cdir / _DEEP_ANALYSIS).exists()


@pytest.mark.asyncio
async def test_deep_solve_fallback_on_teardown_failure(tmp_path):
    """Main solver still runs even when both teardown subagents fail."""
    cdir = _make_challenge(tmp_path, {"app.py": "x=1"})
    cfg = _make_fake_config()

    solver_called = False

    async def fake_solve(
        thread,
        challenge_dir,
        prompt,
        timeout=600,
        model=None,
        effort=None,
        subagent_model=None,
        max_budget=None,
        event_callback=None,
        category=None,
        **kwargs,
    ):
        nonlocal solver_called
        if timeout != _TEARDOWN_TIMEOUT:
            solver_called = True
        # Teardown subagents produce nothing
        return MagicMock()

    with patch("ai.deep_solve.solve_with_claude_code", side_effect=fake_solve):
        result = await deep_solve(
            thread=None,
            challenge_dir=cdir,
            prompt="Solve this.",
            config=cfg,
            solver_timeout=600,
        )

    assert solver_called, "Main solver should run even when teardown fails"
    assert result is not None


# ---------------------------------------------------------------------------
# build_solve_prompt deep analysis injection tests
# ---------------------------------------------------------------------------


def test_build_solve_prompt_injects_deep_analysis(tmp_path, tmp_ctf_root):
    """When _deep_analysis.md exists, its content appears in the solver prompt."""
    from db.challenges import ChallengeRecord

    chall_dir = tmp_path / "web" / "test-challenge"
    chall_dir.mkdir(parents=True)
    (chall_dir / "_deep_analysis.md").write_text("SQL injection in /login")

    challenge = ChallengeRecord(
        id=1,
        ctf_id=1,
        ctfd_id=1,
        name="Test",
        slug="test",
        category="web",
        points=300,
        description="A web challenge",
        solved=False,
        flag=None,
        thread_id=None,
        challenge_dir=str(chall_dir),
    )

    from ai.solve_utils import build_solve_prompt

    prompt = build_solve_prompt(challenge, description="A web challenge", ctf_root=tmp_ctf_root)

    assert "SQL injection in /login" in prompt
    assert "DEEP ANALYSIS" in prompt


def test_build_solve_prompt_no_deep_analysis(tmp_path, tmp_ctf_root):
    """When _deep_analysis.md is absent, DEEP ANALYSIS does NOT appear in the prompt."""
    from db.challenges import ChallengeRecord

    chall_dir = tmp_path / "web" / "test-challenge-no-deep"
    chall_dir.mkdir(parents=True)

    challenge = ChallengeRecord(
        id=2,
        ctf_id=1,
        ctfd_id=2,
        name="Test2",
        slug="test2",
        category="web",
        points=300,
        description="A web challenge",
        solved=False,
        flag=None,
        thread_id=None,
        challenge_dir=str(chall_dir),
    )

    from ai.solve_utils import build_solve_prompt

    prompt = build_solve_prompt(challenge, description="A web challenge", ctf_root=tmp_ctf_root)

    assert "DEEP ANALYSIS" not in prompt
