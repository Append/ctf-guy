#!/usr/bin/env python3
"""Tests for ai/solve_utils.py — flag/platform detection, series finding."""

import json

from ai.solve_utils import (
    _find_series_challenge_dir,
    detect_flag_format,
    detect_platform,
    write_progress_file,
)


class TestDetectFlagFormat:
    def test_none_dir(self):
        assert detect_flag_format(None) == "kernel{...}"

    def test_nonexistent_dir(self):
        assert detect_flag_format("/nonexistent/path") == "kernel{...}"

    def test_picoctf_platform(self, tmp_path):
        meta = tmp_path / "challenge.json"
        meta.write_text(json.dumps({"platform": "picoctf"}))
        assert detect_flag_format(str(tmp_path)) == "picoCTF{...}"

    def test_ctfd_platform(self, tmp_path):
        meta = tmp_path / "challenge.json"
        meta.write_text(json.dumps({"platform": "ctfd"}))
        assert detect_flag_format(str(tmp_path)) == "kernel{...}"


class TestDetectPlatform:
    def test_none_dir(self):
        assert detect_platform(None) == "ctfd"

    def test_nonexistent_dir(self):
        assert detect_platform("/nonexistent") == "ctfd"

    def test_picoctf(self, tmp_path):
        meta = tmp_path / "challenge.json"
        meta.write_text(json.dumps({"platform": "picoctf"}))
        assert detect_platform(str(tmp_path)) == "picoctf"

    def test_ctfd(self, tmp_path):
        meta = tmp_path / "challenge.json"
        meta.write_text(json.dumps({"platform": "ctfd"}))
        assert detect_platform(str(tmp_path)) == "ctfd"


class TestFindSeriesChallengeDir:
    def test_finds_existing(self, tmp_path):
        challenges = tmp_path / "challenges"
        crypto = challenges / "crypto" / "disko-1"
        crypto.mkdir(parents=True)

        result = _find_series_challenge_dir("DISKO", 1, challenges)
        assert result is not None
        assert result.name == "disko-1"

    def test_finds_unnumbered_base(self, tmp_path):
        challenges = tmp_path / "challenges"
        pwn = challenges / "pwn" / "pie-time"
        pwn.mkdir(parents=True)

        result = _find_series_challenge_dir("PIE TIME", 1, challenges)
        assert result is not None
        assert result.name == "pie-time"

    def test_returns_none_for_missing(self, tmp_path):
        challenges = tmp_path / "challenges"
        challenges.mkdir(parents=True)
        assert _find_series_challenge_dir("DISKO", 5, challenges) is None

    def test_nonexistent_challenges_dir(self, tmp_path):
        assert _find_series_challenge_dir("test", 1, tmp_path / "nope") is None


class TestWriteProgressFile:
    def test_creates_new(self, tmp_path):
        write_progress_file(str(tmp_path), "Test Challenge", "Output text", 0.05, 3, 5000, True)
        progress = tmp_path / "progress.md"
        assert progress.exists()
        content = progress.read_text()
        assert "Test Challenge" in content
        assert "$0.0500" in content
        assert "FLAG FOUND" in content

    def test_appends_to_existing(self, tmp_path):
        progress = tmp_path / "progress.md"
        progress.write_text("# Progress: Test\n## Attempt 1\nFirst try.\n")

        write_progress_file(str(tmp_path), "Test", "Second output", 0.1, 5, 10000, False)
        content = progress.read_text()
        assert "First try" in content  # Original preserved
        assert "NO FLAG" in content  # New attempt appended
        assert content.count("## Attempt") == 2


def test_build_solve_prompt_injects_attack_graph(tmp_path, tmp_ctf_root):
    """build_solve_prompt includes _attack_graph.md content when present."""
    from db.challenges import ChallengeRecord

    from ai.solve_utils import build_solve_prompt

    chall_dir = tmp_path / "misc" / "test-chall"
    chall_dir.mkdir(parents=True)
    (chall_dir / "_attack_graph.md").write_text('flowchart TD\n    a0["Recon"]\n')

    challenge = ChallengeRecord(
        id=1,
        ctf_id=1,
        ctfd_id=1,
        name="test-chall",
        slug="test-chall",
        category="misc",
        points=50,
        description="Test",
        solved=False,
        flag=None,
        thread_id=None,
        challenge_dir=str(chall_dir),
    )

    prompt = build_solve_prompt(challenge, description="Test", ctf_root=tmp_ctf_root)
    assert "ATTACK GRAPH FROM PRIOR ATTEMPTS" in prompt
    assert "flowchart TD" in prompt
