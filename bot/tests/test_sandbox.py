#!/usr/bin/env python3
"""Tests for sandbox utilities: env sanitization, bwrap cmd, artifact sync."""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_solver_env_strips_secrets(monkeypatch):
    """Sensitive env vars must be removed from solver environment."""
    monkeypatch.setenv("DISCORD_TOKEN", "secret123")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret")
    monkeypatch.setenv("CTFD_TOKEN", "token456")
    monkeypatch.setenv("PICO_USERNAME", "user")
    monkeypatch.setenv("PICO_PASSWORD", "pass")
    monkeypatch.setenv("PATH", "/usr/bin")

    from ai.sandbox import solver_env

    env = solver_env()

    assert "DISCORD_TOKEN" not in env
    assert "OPENROUTER_API_KEY" not in env
    assert "CTFD_TOKEN" not in env
    assert "PICO_USERNAME" not in env
    assert "PICO_PASSWORD" not in env


def test_solver_env_preserves_path(monkeypatch):
    """PATH, HOME, and tool paths must survive sanitization."""
    monkeypatch.setenv("PATH", "/nix/store/bin:/usr/bin")
    monkeypatch.setenv("HOME", "/home/test")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/usr/lib")

    from ai.sandbox import solver_env

    env = solver_env()

    assert env["PATH"] == "/nix/store/bin:/usr/bin"
    assert env["HOME"] == "/home/test"
    assert env["LD_LIBRARY_PATH"] == "/usr/lib"


def test_create_bwrap_workspace(tmp_path):
    """Workspace should copy files and put .so files in libs/."""
    challenge = tmp_path / "challenge"
    challenge.mkdir()
    (challenge / "binary").write_bytes(b"\x7fELF")
    (challenge / "libc.so.6").write_bytes(b"fake_libc")
    (challenge / "source.c").write_text("int main() {}")
    (challenge / "subdir").mkdir()
    (challenge / "subdir" / "data.txt").write_text("data")

    from ai.sandbox import create_bwrap_workspace

    tmpdir, upperdir = create_bwrap_workspace(challenge)

    assert (upperdir / "binary").exists()
    assert (upperdir / "source.c").exists()
    assert (upperdir / "libs" / "libc.so.6").exists()
    assert not (upperdir / "libc.so.6").exists()  # .so moved to libs/
    assert (upperdir / "subdir" / "data.txt").exists()

    import shutil

    shutil.rmtree(tmpdir)


def test_build_bwrap_cmd():
    """bwrap command should have correct structure."""
    from ai.sandbox import build_bwrap_cmd

    challenge = Path("/tmp/test_challenge")
    upper = Path("/tmp/test_upper")

    cmd = build_bwrap_cmd(challenge, upper, ["claude", "--print", "-p", "test"])

    assert cmd[0] == "bwrap"
    assert "--bind" in cmd
    assert "--tmpfs" in cmd
    assert "--die-with-parent" in cmd
    assert "--" in cmd
    # Inner command should be at the end
    assert cmd[-4:] == ["claude", "--print", "-p", "test"]


def test_sync_back_artifacts_flag_found(tmp_path):
    """On flag found, artifacts sync to real challenge dir."""
    upperdir = tmp_path / "upper"
    upperdir.mkdir()
    challenge = tmp_path / "challenge"
    challenge.mkdir()

    (upperdir / "flag.txt").write_text("picoCTF{test_flag}")
    (upperdir / "solve.py").write_text("#!/usr/bin/env python3\nprint('solve')")
    (upperdir / "README.md").write_text("# Solved")

    from ai.sandbox import sync_back_artifacts

    result = sync_back_artifacts(upperdir, challenge, "haiku")

    assert result is True
    assert (challenge / "flag.txt").read_text() == "picoCTF{test_flag}"
    assert (challenge / "solve.py").exists()
    assert (challenge / "README.md").exists()


def test_sync_back_artifacts_no_flag(tmp_path):
    """Without flag, artifacts go to _attempts/<model>-N/."""
    upperdir = tmp_path / "upper"
    upperdir.mkdir()
    challenge = tmp_path / "challenge"
    challenge.mkdir()

    (upperdir / "solve.py").write_text("#!/usr/bin/env python3\n# failed")
    # No flag.txt

    from ai.sandbox import sync_back_artifacts

    result = sync_back_artifacts(upperdir, challenge, "opus")

    assert result is False
    assert (challenge / "_attempts" / "opus-1" / "solve.py").exists()
    assert not (challenge / "solve.py").exists()  # NOT in main dir


def test_sync_back_artifacts_increments(tmp_path):
    """Attempt numbers should increment."""
    upperdir = tmp_path / "upper"
    upperdir.mkdir()
    challenge = tmp_path / "challenge"
    challenge.mkdir()
    (challenge / "_attempts" / "haiku-1").mkdir(parents=True)
    (challenge / "_attempts" / "haiku-2").mkdir(parents=True)

    (upperdir / "solve.py").write_text("attempt 3")

    from ai.sandbox import sync_back_artifacts

    sync_back_artifacts(upperdir, challenge, "haiku")

    assert (challenge / "_attempts" / "haiku-3" / "solve.py").exists()


def test_kill_process_tree_dead_process():
    """kill_process_tree shouldn't crash on already-dead process."""
    from ai.sandbox import kill_process_tree

    mock_proc = MagicMock()
    mock_proc.pid = 999999  # Non-existent PID

    # Should not raise
    kill_process_tree(mock_proc)
