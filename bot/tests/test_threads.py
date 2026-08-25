#!/usr/bin/env python3
"""Tests for discord_ui/threads.py — slugify and thread naming."""

from discord_ui.threads import (
    challenge_thread_name,
    slugify,
)


class TestSlugify:
    def test_basic(self):
        assert slugify("RSA 101 Challenge!") == "rsa-101-challenge"

    def test_spaces_to_dashes(self):
        assert slugify("hello world") == "hello-world"

    def test_special_chars_removed(self):
        assert slugify("test@#$%^&*()") == "test"

    def test_multiple_dashes_collapsed(self):
        assert slugify("a---b") == "a-b"

    def test_leading_trailing_stripped(self):
        assert slugify("  --hello--  ") == "hello"

    def test_empty_string(self):
        assert slugify("") == ""

    def test_underscores_to_dashes(self):
        assert slugify("hello_world") == "hello-world"

    def test_unicode(self):
        result = slugify("café")
        assert "caf" in result


class TestChallengeThreadName:
    def test_basic_format(self):
        name = challenge_thread_name("crypto", "RSA 101", 100)
        assert "rsa-101" in name
        assert "100pt" in name

    def test_truncation(self):
        long_name = "A" * 200
        name = challenge_thread_name("web", long_name, 500)
        assert len(name) <= 100

    def test_contains_points(self):
        name = challenge_thread_name("pwn", "Stack Smash", 300)
        assert "300pt" in name
