#!/usr/bin/env python3
"""Tests for ai/models.py — model routing logic."""

from ai.models import select_model


class TestSelectModel:
    def test_low_points_default(self, mock_config):
        assert select_model("crypto", 50, mock_config) == "sonnet"
        assert select_model("web", 100, mock_config) == "sonnet"

    def test_high_points_heavy(self, mock_config):
        assert select_model("crypto", 300, mock_config) == "opus"
        assert select_model("web", 500, mock_config) == "opus"

    def test_pwn_always_heavy(self, mock_config):
        assert select_model("pwn", 50, mock_config) == "opus"
        assert select_model("pwn", 100, mock_config) == "opus"

    def test_rev_always_heavy(self, mock_config):
        assert select_model("rev", 50, mock_config) == "opus"
        assert select_model("rev", 200, mock_config) == "opus"

    def test_case_insensitive_category(self, mock_config):
        assert select_model("PWN", 50, mock_config) == "opus"
        assert select_model("Rev", 100, mock_config) == "opus"

    def test_boundary_300(self, mock_config):
        assert select_model("crypto", 299, mock_config) == "sonnet"
        assert select_model("crypto", 300, mock_config) == "opus"
