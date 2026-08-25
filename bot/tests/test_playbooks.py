#!/usr/bin/env python3
"""Tests for ai/playbooks.py — playbook loading and category mapping."""

from ai.playbooks import CATEGORY_MAP, load_playbook


class TestCategoryMap:
    def test_canonical_categories(self):
        for canonical in [
            "crypto",
            "rev",
            "pwn",
            "web",
            "forensics",
            "misc",
            "osint",
            "ai",
        ]:
            assert canonical in CATEGORY_MAP.values()

    def test_common_aliases(self):
        assert CATEGORY_MAP["cryptography"] == "crypto"
        assert CATEGORY_MAP["reverse engineering"] == "rev"
        assert CATEGORY_MAP["binary exploitation"] == "pwn"
        assert CATEGORY_MAP["prompt engineering"] == "ai"

    def test_all_lowercase_keys(self):
        for key in CATEGORY_MAP:
            assert key == key.lower()


class TestLoadPlaybook:
    def test_load_existing(self, tmp_ctf_root):
        content = load_playbook("crypto", tmp_ctf_root)
        assert "Crypto Agent" in content

    def test_load_misc_fallback(self, tmp_ctf_root):
        content = load_playbook("unknown_category", tmp_ctf_root)
        assert "Misc Agent" in content

    def test_load_alias(self, tmp_ctf_root):
        content = load_playbook("Cryptography", tmp_ctf_root)
        assert "Crypto Agent" in content

    def test_hardcoded_fallback(self, tmp_path):
        # Neither crypto.md nor misc.md exist
        content = load_playbook("crypto", tmp_path)
        assert "CTF challenge solver" in content
