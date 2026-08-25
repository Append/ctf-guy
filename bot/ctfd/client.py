#!/usr/bin/env python3
"""CTFd REST API client with retry on 429/5xx."""

from pathlib import Path

import httpx

from .retry import ctfd_request
from .types import CTFdChallenge, CTFdSubmissionResult


class CTFdClient:
    def __init__(self, base_url: str, token: str = "", session: str = ""):
        """Create a CTFd client.

        Auth modes (in priority order):
        - token: API token (Authorization: Token <token>)
        - session: Session cookie from browser login (Cookie: session=<value>)
        """
        self.base_url = base_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        cookies = None
        if token:
            headers["Authorization"] = f"Token {token}"
        elif session:
            cookies = httpx.Cookies({"session": session})
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            cookies=cookies,
            timeout=30.0,
        )

    async def fetch_challenges(self) -> list[CTFdChallenge]:
        """Fetch all challenges from CTFd."""
        resp = await ctfd_request(self.client, "GET", "/api/v1/challenges")
        data = resp.json()["data"]
        return [self._parse_challenge(c) for c in data]

    async def fetch_challenge(self, challenge_id: int) -> CTFdChallenge:
        """Fetch a single challenge with full details."""
        resp = await ctfd_request(self.client, "GET", f"/api/v1/challenges/{challenge_id}")
        return self._parse_challenge(resp.json()["data"])

    async def download_file(self, file_url: str, dest: Path) -> Path:
        """Download a challenge file to the destination path."""
        # CTFd file URLs may be relative
        if file_url.startswith("/"):
            url = f"{self.base_url}{file_url}"
        elif not file_url.startswith("http"):
            url = f"{self.base_url}/files/{file_url}"
        else:
            url = file_url

        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=60.0) as dl_client:
            resp = await ctfd_request(dl_client, "GET", url)
            dest.write_bytes(resp.content)
        return dest

    async def submit_flag(self, challenge_id: int, flag: str) -> CTFdSubmissionResult:
        """Submit a flag for a challenge."""
        resp = await ctfd_request(
            self.client,
            "POST",
            "/api/v1/challenges/attempt",
            json={"challenge_id": challenge_id, "submission": flag},
        )
        data = resp.json()["data"]
        return CTFdSubmissionResult(
            status=data.get("status", "incorrect"),
            message=data.get("message", ""),
        )

    async def close(self):
        await self.client.aclose()

    def _parse_challenge(self, data: dict) -> CTFdChallenge:
        return CTFdChallenge(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            category=data.get("category", "misc"),
            value=data.get("value", 0),
            files=data.get("files", []),
            hints=data.get("hints", []),
            solved_by_me=data.get("solved_by_me", False),
            solves=data.get("solves", 0),
            tags=data.get("tags", []),
        )
