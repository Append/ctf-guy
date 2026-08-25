#!/usr/bin/env python3
"""Standing invariants for the learned pattern corpus in solvers/patterns/.

These exist because normalize_category() once failed to fold hyphens, which
forked the corpus into parallel "web exploitation.json" / "web-exploitation.json"
files that drifted apart. The bug was invisible until someone counted records.
"""

import json
from pathlib import Path

import pytest

from ai.playbooks import normalize_category

PATTERNS_DIR = Path(__file__).resolve().parents[2] / "solvers" / "patterns"
PATTERN_FILES = sorted(PATTERNS_DIR.glob("*.json"))


def _entries(path):
    return json.loads(path.read_text())


@pytest.mark.skipif(not PATTERN_FILES, reason="no corpus checked out")
class TestCorpusIntegrity:
    @pytest.mark.parametrize("path", PATTERN_FILES, ids=lambda p: p.name)
    def test_file_is_valid_json_list(self, path):
        assert isinstance(_entries(path), list)

    @pytest.mark.parametrize("path", PATTERN_FILES, ids=lambda p: p.name)
    def test_filename_is_already_canonical(self, path):
        """Guards against reintroducing forked category files."""
        stem = path.stem
        assert normalize_category(stem) == stem, (
            f"{path.name} is not a canonical category name; learner.py writes "
            f"normalize_category(category).json, so this file would be orphaned"
        )

    @pytest.mark.parametrize("path", PATTERN_FILES, ids=lambda p: p.name)
    def test_no_duplicate_challenges_within_a_file(self, path):
        seen, dupes = set(), []
        for e in _entries(path):
            key = (e.get("slug") or e.get("challenge") or "").strip().lower()
            if not key:
                continue
            if key in seen:
                dupes.append(key)
            seen.add(key)
        assert not dupes, f"duplicate entries in {path.name}: {dupes}"

    @pytest.mark.parametrize("path", PATTERN_FILES, ids=lambda p: p.name)
    def test_flag_fields_hold_flags_not_prose(self, path):
        """A flag is a short single-line token. Writeup text in this field means
        a parser regression upstream in learner.parse_readme()."""
        bad = [
            (e.get("challenge", "?"), len(e["flag"]))
            for e in _entries(path)
            if (e.get("flag") or "") and (len(e["flag"]) > 80 or "\n" in e["flag"])
        ]
        assert not bad, f"{path.name} has prose in flag fields: {bad}"

    def test_no_challenge_appears_in_two_files(self):
        """The exact failure the hyphen bug produced."""
        owner, clashes = {}, []
        for path in PATTERN_FILES:
            for e in _entries(path):
                key = (e.get("slug") or e.get("challenge") or "").strip().lower()
                if not key:
                    continue
                if key in owner and owner[key] != path.name:
                    clashes.append((key, owner[key], path.name))
                owner.setdefault(key, path.name)
        assert not clashes, f"challenges duplicated across category files: {clashes[:5]}"
