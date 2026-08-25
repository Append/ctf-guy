#!/usr/bin/env python3
"""Tests for ai/executor.py — path safety and tool execution."""

from ai.executor import _safe_resolve


class TestSafeResolve:
    def test_normal_file(self, tmp_path):
        result = _safe_resolve(tmp_path, "file.txt")
        assert result is not None
        assert result == tmp_path / "file.txt"

    def test_subdirectory(self, tmp_path):
        (tmp_path / "sub").mkdir()
        result = _safe_resolve(tmp_path, "sub/file.txt")
        assert result is not None
        assert str(result).startswith(str(tmp_path))

    def test_path_traversal_blocked(self, tmp_path):
        assert _safe_resolve(tmp_path, "../etc/passwd") is None

    def test_deep_traversal_blocked(self, tmp_path):
        assert _safe_resolve(tmp_path, "../../..") is None

    def test_dot_slash(self, tmp_path):
        result = _safe_resolve(tmp_path, "./file.txt")
        assert result is not None
        assert result == tmp_path / "file.txt"

    def test_absolute_path_outside(self, tmp_path):
        assert _safe_resolve(tmp_path, "/etc/passwd") is None
