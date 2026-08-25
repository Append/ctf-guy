#!/usr/bin/env python3
"""Tests for challenge dependency detection."""

import asyncio
from dataclasses import dataclass

import pytest

from ai.solve_utils import detect_series


@dataclass
class MockChallenge:
    id: int
    name: str
    category: str
    points: int
    slug: str = ""
    description: str = ""
    ctf_id: int = 1
    ctfd_id: int = 0
    thread_id: str = ""
    challenge_dir: str = ""
    solved: bool = False
    flag: str = ""


# --- detect_series ---


def test_trailing_number():
    assert detect_series("DISKO 1") == ("DISKO", 1)
    assert detect_series("DISKO 2") == ("DISKO", 2)
    assert detect_series("buffer overflow 0") == ("buffer overflow", 0)


def test_part_n():
    assert detect_series("challenge Part 3") is not None
    result = detect_series("challenge Part 3")
    assert result[1] == 3


def test_parenthesized_part():
    result = detect_series("Guess My Cheese (Part 1)")
    assert result is not None
    assert result[1] == 1

    result2 = detect_series("Guess My Cheese (Part 2)")
    assert result2 is not None
    assert result2[1] == 2
    assert result[0] == result2[0]  # Same base name


def test_roman_numerals():
    result = detect_series("Disk, disk, sleuth! II")
    assert result is not None
    assert result[1] == 2


def test_no_series():
    assert detect_series("Web Gauntlet") is None
    assert detect_series("unrelated challenge") is None
    assert detect_series("picoCTF") is None


# --- detect_dependencies ---


def test_dependencies_basic():
    challenges = [
        MockChallenge(1, "DISKO 1", "forensics", 100),
        MockChallenge(2, "DISKO 2", "forensics", 200),
        MockChallenge(3, "DISKO 3", "forensics", 300),
        MockChallenge(4, "unrelated", "web", 100),
    ]

    class FakeConfig:
        openrouter_api_key = ""
        triage_model = ""

    from ai.dependency import detect_dependencies

    deps = asyncio.run(detect_dependencies(challenges, FakeConfig()))

    # DISKO 2 depends on DISKO 1
    assert 2 in deps
    assert 1 in deps[2]

    # DISKO 3 depends on DISKO 1 and 2
    assert 3 in deps
    assert 1 in deps[3]
    assert 2 in deps[3]

    # Unrelated has no deps
    assert 4 not in deps


def test_dependencies_empty():
    class FakeConfig:
        openrouter_api_key = ""
        triage_model = ""

    from ai.dependency import detect_dependencies

    deps = asyncio.run(detect_dependencies([], FakeConfig()))
    assert deps == {}


def test_dependencies_single_challenge():
    challenges = [MockChallenge(1, "solo", "web", 100)]

    class FakeConfig:
        openrouter_api_key = ""
        triage_model = ""

    from ai.dependency import detect_dependencies

    deps = asyncio.run(detect_dependencies(challenges, FakeConfig()))
    assert deps == {}


def test_dependencies_parenthesized_parts():
    challenges = [
        MockChallenge(10, "Guess My Cheese (Part 1)", "crypto", 200),
        MockChallenge(11, "Guess My Cheese (Part 2)", "crypto", 300),
    ]

    class FakeConfig:
        openrouter_api_key = ""
        triage_model = ""

    from ai.dependency import detect_dependencies

    deps = asyncio.run(detect_dependencies(challenges, FakeConfig()))

    assert 11 in deps
    assert 10 in deps[11]
