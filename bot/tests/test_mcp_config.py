#!/usr/bin/env python3
"""Tests for binary detection, MCP config generation, and cleanup."""

import json
import os
import stat
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def challenge_dir(tmp_path):
    """Create a temp challenge directory."""
    return tmp_path


@pytest.fixture
def elf_binary(challenge_dir):
    """Create a fake ELF binary in the challenge dir."""
    binary = challenge_dir / "challenge_bin"
    # ELF magic bytes + padding
    binary.write_bytes(b"\x7fELF" + b"\x00" * 100)
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return binary


def test_detect_binary_finds_elf(challenge_dir, elf_binary):
    from ai.claude_code import detect_challenge_binary

    result = detect_challenge_binary(str(challenge_dir))
    assert result is not None
    assert result == str(elf_binary.resolve())


def test_detect_binary_skips_non_binary(challenge_dir):
    (challenge_dir / "solve.py").write_text("print('hello')")
    (challenge_dir / "notes.txt").write_text("some notes")

    from ai.claude_code import detect_challenge_binary

    result = detect_challenge_binary(str(challenge_dir))
    assert result is None


def test_detect_binary_skips_shared_libs(challenge_dir):
    """Shared libraries (.so) should not be detected as challenge binaries."""
    lib = challenge_dir / "libc.so"
    lib.write_bytes(b"\x7fELF" + b"\x00" * 100)

    lib2 = challenge_dir / "libpthread.so.0"
    lib2.write_bytes(b"\x7fELF" + b"\x00" * 50)

    from ai.claude_code import detect_challenge_binary

    result = detect_challenge_binary(str(challenge_dir))
    assert result is None


def test_detect_binary_prefers_dir_name_match(challenge_dir):
    """Binary matching challenge dir name should be preferred."""
    # Create two ELF binaries
    other = challenge_dir / "helper"
    other.write_bytes(b"\x7fELF" + b"\x00" * 200)

    # Binary matching dir name (stem)
    match = challenge_dir / challenge_dir.name
    match.write_bytes(b"\x7fELF" + b"\x00" * 50)

    from ai.claude_code import detect_challenge_binary

    result = detect_challenge_binary(str(challenge_dir))
    assert result == str(match.resolve())


def test_detect_binary_returns_none_for_missing_dir():
    from ai.claude_code import detect_challenge_binary

    result = detect_challenge_binary("/nonexistent/path")
    assert result is None


def test_build_mcp_config_non_rev_category(challenge_dir, elf_binary, monkeypatch):
    """Non-rev/pwn categories should return the static MCP config."""
    monkeypatch.setenv("GHIDRA_MCP_ENABLED", "true")

    from ai.claude_code import _build_mcp_config, MCP_CONFIG

    result = _build_mcp_config("crypto", str(challenge_dir))
    assert result == MCP_CONFIG


def test_build_mcp_config_rev_with_binary(challenge_dir, elf_binary, monkeypatch):
    """Rev category with binary and Ghidra enabled should return temp config with ghidra entry."""
    monkeypatch.setenv("GHIDRA_MCP_ENABLED", "true")

    from ai.claude_code import _build_mcp_config, MCP_CONFIG, _cleanup_mcp_config

    result = _build_mcp_config("reverse engineering", str(challenge_dir))
    assert result != MCP_CONFIG
    assert Path(result).exists()

    config = json.loads(Path(result).read_text())
    assert "ghidra" in config["mcpServers"]
    assert config["mcpServers"]["ghidra"]["command"] == "env"
    assert "pyghidra-mcp" in config["mcpServers"]["ghidra"]["args"]
    assert str(elf_binary.resolve()) in config["mcpServers"]["ghidra"]["args"]

    _cleanup_mcp_config(result)
    assert not Path(result).exists()


def test_build_mcp_config_pwn_category(challenge_dir, elf_binary, monkeypatch):
    """Pwn category should also get Ghidra MCP."""
    monkeypatch.setenv("GHIDRA_MCP_ENABLED", "true")

    from ai.claude_code import _build_mcp_config, MCP_CONFIG, _cleanup_mcp_config

    result = _build_mcp_config("binary exploitation", str(challenge_dir))
    assert result != MCP_CONFIG

    config = json.loads(Path(result).read_text())
    assert "ghidra" in config["mcpServers"]

    _cleanup_mcp_config(result)


def test_build_mcp_config_rev_no_binary(challenge_dir, monkeypatch):
    """Rev category without a binary should return static config."""
    monkeypatch.setenv("GHIDRA_MCP_ENABLED", "true")
    (challenge_dir / "solve.py").write_text("print('hello')")

    from ai.claude_code import _build_mcp_config, MCP_CONFIG

    result = _build_mcp_config("rev", str(challenge_dir))
    assert result == MCP_CONFIG


def test_build_mcp_config_disabled(challenge_dir, elf_binary, monkeypatch):
    """Ghidra disabled should return static config even for rev."""
    monkeypatch.delenv("GHIDRA_MCP_ENABLED", raising=False)

    from ai.claude_code import _build_mcp_config, MCP_CONFIG

    result = _build_mcp_config("rev", str(challenge_dir))
    assert result == MCP_CONFIG


def test_build_mcp_config_no_category():
    """None category should return static config."""
    from ai.claude_code import _build_mcp_config, MCP_CONFIG

    result = _build_mcp_config(None, "/some/dir")
    assert result == MCP_CONFIG


def test_cleanup_deletes_temp():
    """Temp MCP config should be deleted by cleanup."""
    tmp = tempfile.NamedTemporaryFile(suffix="-mcp.json", delete=False)
    tmp.write(b"{}")
    tmp.close()

    from ai.claude_code import _cleanup_mcp_config

    _cleanup_mcp_config(tmp.name)
    assert not Path(tmp.name).exists()


def test_cleanup_preserves_static():
    """Static MCP config should NOT be deleted by cleanup."""
    from ai.claude_code import _cleanup_mcp_config, MCP_CONFIG

    # Should be a no-op (doesn't try to delete the static config)
    _cleanup_mcp_config(MCP_CONFIG)
    # Static config should still exist (if it exists at all)
    # This test just verifies no exception is raised
