#!/usr/bin/env python3
"""Tests for ai/learner.py — series detection, README parsing, pattern management."""

import json

from ai.learner import (
    _extract_field,
    _extract_flag,
    _extract_heading,
    _extract_section,
    _parse_list,
    _safe_int,
    get_patterns_context,
    learn_from_challenge,
    parse_readme,
    scan_and_build_patterns,
)
from ai.solve_utils import detect_series


class TestDetectSeries:
    def test_trailing_number(self):
        assert detect_series("DISKO 1") == ("DISKO", 1)
        assert detect_series("DISKO 2") == ("DISKO", 2)

    def test_multi_word_name(self):
        assert detect_series("PIE TIME 2") == ("PIE TIME", 2)
        assert detect_series("Binary Gauntlet 0") == ("Binary Gauntlet", 0)
        assert detect_series("PW Crack 3") == ("PW Crack", 3)

    def test_roman_numerals(self):
        assert detect_series("Disk, disk, sleuth! II") == ("Disk, disk, sleuth!", 2)
        assert detect_series("Some Challenge III") == ("Some Challenge", 3)

    def test_not_a_series(self):
        assert detect_series("hashcrack") is None
        assert detect_series("FANTASY CTF") is None
        assert detect_series("head-dump") is None

    def test_part_suffix(self):
        # "Challenge Part 2" — trailing number regex matches first: ("Challenge Part", 2)
        assert detect_series("Challenge Part 2") == ("Challenge Part", 2)


class TestExtractors:
    def test_extract_flag_picoctf(self):
        assert _extract_flag("Flag: picoCTF{test_123}") == "picoCTF{test_123}"

    def test_extract_flag_kernel(self):
        assert _extract_flag("kernel{abc-def}") == "kernel{abc-def}"

    def test_extract_flag_none(self):
        assert _extract_flag("no flag here") == ""

    def test_extract_heading(self):
        assert _extract_heading("# My Challenge") == "My Challenge"
        assert _extract_heading("# Challenge — 50pts") == "Challenge"
        assert _extract_heading("no heading") == ""

    def test_extract_section(self):
        content = "## Summary\nThis is a test.\n## Approach\nDo the thing."
        assert "This is a test" in _extract_section(content, "summary")
        assert "Do the thing" in _extract_section(content, "approach")
        assert _extract_section(content, "nonexistent") == ""

    def test_extract_field(self):
        # _extract_field strips * and ` but may have leading space
        result = _extract_field("**Points:** 50", "points")
        assert result.strip() == "50"
        assert _extract_field("Category: Crypto", "category") == "Crypto"
        assert _extract_field("nothing here", "points") == ""

    def test_parse_list(self):
        assert _parse_list("- tool1\n- tool2") == ["tool1", "tool2"]
        assert _parse_list("- None") == []
        assert _parse_list("- n/a") == []
        assert _parse_list("") == []
        assert _parse_list("- pwntools\n- None.\n- r2") == ["pwntools", "r2"]

    def test_safe_int(self):
        assert _safe_int("50") == 50
        assert _safe_int("50pts") == 50
        assert _safe_int("abc") == 0
        assert _safe_int("") == 0


class TestParseReadme:
    def test_realistic_readme(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text(
            "# Test Challenge\n\n"
            "**Category:** Crypto | **Points:** 100\n\n"
            "## Summary\nDecode the base64 message.\n\n"
            "## Approach\nUsed CyberChef to decode.\n\n"
            "## Key Insight\nDouble base64 encoding.\n\n"
            "## Tools\n- CyberChef\n- Python\n\n"
            "## Flag\npicoCTF{decoded_flag}\n"
        )
        result = parse_readme(readme, "test-challenge", True)
        assert result is not None
        assert result["challenge"] == "Test Challenge"
        assert (
            "base64" in result["pattern"].lower()
            or "base64" in result["key_insight"].lower()
        )
        assert result["solved"] is True

    def test_empty_readme(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text("")
        assert parse_readme(readme, "empty", False) is None

    def test_sparse_readme(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text("# Title\nNo sections here.")
        assert parse_readme(readme, "sparse", False) is None


class TestPatternsContext:
    def test_no_patterns_file(self, tmp_ctf_root):
        assert get_patterns_context("nonexistent", tmp_ctf_root) == ""

    def test_empty_patterns(self, tmp_ctf_root):
        patterns_file = tmp_ctf_root / "solvers" / "patterns" / "crypto.json"
        patterns_file.write_text("[]")
        assert get_patterns_context("crypto", tmp_ctf_root) == ""

    def test_with_patterns(self, tmp_ctf_root):
        patterns_file = tmp_ctf_root / "solvers" / "patterns" / "crypto.json"
        patterns_file.write_text(
            json.dumps(
                [
                    {
                        "challenge": "RSA 101",
                        "points": 100,
                        "solved": True,
                        "key_insight": "Small e, use Coppersmith",
                        "approach": "Wiener's attack",
                    }
                ]
            )
        )
        ctx = get_patterns_context("crypto", tmp_ctf_root)
        assert "RSA 101" in ctx
        assert "Coppersmith" in ctx


class TestLearnFromChallenge:
    def test_learn_creates_pattern(self, tmp_ctf_root):
        chall_dir = tmp_ctf_root / "challenges" / "crypto" / "rsa-101"
        chall_dir.mkdir(parents=True)
        (chall_dir / "challenge.json").write_text(json.dumps({"category": "crypto"}))
        (chall_dir / "flag.txt").write_text("picoCTF{test}\n")
        (chall_dir / "README.md").write_text(
            "# RSA 101\n## Summary\nBasic RSA.\n## Key Insight\nSmall e.\n"
        )

        result = learn_from_challenge(str(chall_dir), tmp_ctf_root)
        assert result is not None
        assert result["challenge"] == "RSA 101"

        # Check pattern file was created
        pattern_file = tmp_ctf_root / "solvers" / "patterns" / "crypto.json"
        assert pattern_file.exists()
        patterns = json.loads(pattern_file.read_text())
        assert len(patterns) == 1


class TestScanAndBuildPatterns:
    def test_scan_empty(self, tmp_ctf_root):
        counts = scan_and_build_patterns(tmp_ctf_root)
        assert counts == {}

    def test_scan_with_challenges(self, tmp_ctf_root):
        for name in ["chall-1", "chall-2"]:
            d = tmp_ctf_root / "challenges" / "crypto" / name
            d.mkdir(parents=True)
            (d / "README.md").write_text(
                f"# {name}\n## Summary\nTest.\n## Key Insight\nTrick.\n"
            )
            (d / "flag.txt").write_text("picoCTF{test}\n")

        counts = scan_and_build_patterns(tmp_ctf_root)
        assert counts.get("crypto") == 2
