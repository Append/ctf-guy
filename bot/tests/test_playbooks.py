#!/usr/bin/env python3
"""Tests for ai/playbooks.py — playbook loading and category mapping."""

import pytest

from ai.playbooks import CATEGORY_MAP, load_playbook, normalize_category


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


class TestNormalizeCategory:
    """The dict was always right; the lookup was not.

    normalize_category() previously did a bare CATEGORY_MAP[category.lower()],
    so any hyphen-slugified name ("web-exploitation" — the form challenge
    directories actually use) missed the map and fell through to "misc". Web
    solvers were served misc patterns, and the learned corpus forked into
    parallel spaced/hyphenated files. These tests pin the separator folding.
    """

    @pytest.mark.parametrize("alias,expected", sorted(CATEGORY_MAP.items()))
    def test_every_alias_maps_to_its_canonical_form(self, alias, expected):
        assert normalize_category(alias) == expected

    @pytest.mark.parametrize("alias,expected", sorted(CATEGORY_MAP.items()))
    def test_separator_and_case_variants_agree(self, alias, expected):
        """Hyphen, underscore, title case, and padding must not change meaning."""
        for variant in (
            alias.replace(" ", "-"),
            alias.replace(" ", "_"),
            alias.upper(),
            alias.title(),
            f"  {alias}  ",
            alias.replace(" ", "  "),
        ):
            assert normalize_category(variant) == expected, f"{variant!r} != {expected}"

    def test_the_regression_case(self):
        assert normalize_category("web-exploitation") == "web"
        assert normalize_category("binary-exploitation") == "pwn"
        assert normalize_category("reverse-engineering") == "rev"

    def test_unknown_category_falls_back_to_misc(self):
        assert normalize_category("definitely-not-a-category") == "misc"

    def test_normalization_is_idempotent(self):
        for canonical in set(CATEGORY_MAP.values()):
            assert normalize_category(normalize_category(canonical)) == normalize_category(canonical)


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
