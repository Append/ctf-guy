#!/usr/bin/env python3
"""CTFd API response types."""

from dataclasses import dataclass, field


@dataclass
class CTFdChallenge:
    id: int
    name: str
    description: str
    category: str
    value: int
    files: list[str] = field(default_factory=list)
    hints: list[dict] = field(default_factory=list)
    solved_by_me: bool = False
    solves: int = 0
    tags: list[str] = field(default_factory=list)


@dataclass
class CTFdSubmissionResult:
    status: str  # 'correct', 'incorrect', 'already_solved'
    message: str
