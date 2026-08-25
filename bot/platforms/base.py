#!/usr/bin/env python3
"""Base platform interface for CTF platforms (CTFd, picoCTF, etc.)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Challenge:
    """Unified challenge representation across platforms."""

    id: int | str
    name: str
    description: str
    category: str
    points: int
    files: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    solves: int = 0
    solved_by_me: bool = False
    tags: list[str] = field(default_factory=list)
    # Platform-specific metadata
    extra: dict = field(default_factory=dict)


@dataclass
class SubmissionResult:
    status: str  # 'correct', 'incorrect', 'already_solved', 'rate_limited'
    message: str


class CTFPlatform(ABC):
    """Abstract base for CTF platform integrations."""

    @abstractmethod
    async def fetch_challenges(self) -> list[Challenge]:
        """Fetch all available challenges."""
        ...

    @abstractmethod
    async def fetch_challenge(self, challenge_id: int | str) -> Challenge:
        """Fetch a single challenge with full details."""
        ...

    @abstractmethod
    async def download_file(self, file_url: str, dest: Path) -> Path:
        """Download a challenge file."""
        ...

    @abstractmethod
    async def submit_flag(self, challenge_id: int | str, flag: str) -> SubmissionResult:
        """Submit a flag for a challenge."""
        ...

    @abstractmethod
    async def close(self):
        """Clean up resources."""
        ...
