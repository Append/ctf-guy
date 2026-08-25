#!/usr/bin/env python3
"""CTFd platform adapter with retry on 429/5xx."""

from pathlib import Path

import httpx

from ctfd.retry import ctfd_request

from .base import Challenge, CTFPlatform, SubmissionResult


class CTFdPlatform(CTFPlatform):
    """Adapter for CTFd-based competitions."""

    def __init__(self, base_url: str, token: str = "", session: str = ""):
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

    async def fetch_challenges(self) -> list[Challenge]:
        resp = await ctfd_request(self.client, "GET", "/api/v1/challenges")
        return [self._to_challenge(c) for c in resp.json()["data"]]

    async def fetch_challenge(self, challenge_id: int | str) -> Challenge:
        resp = await ctfd_request(self.client, "GET", f"/api/v1/challenges/{challenge_id}")
        return self._to_challenge(resp.json()["data"])

    async def download_file(self, file_url: str, dest: Path) -> Path:
        if file_url.startswith("/"):
            url = f"{self.base_url}{file_url}"
        elif not file_url.startswith("http"):
            url = f"{self.base_url}/files/{file_url}"
        else:
            url = file_url

        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=60.0) as dl:
            resp = await ctfd_request(dl, "GET", url)
            dest.write_bytes(resp.content)
        return dest

    async def submit_flag(self, challenge_id: int | str, flag: str) -> SubmissionResult:
        resp = await ctfd_request(
            self.client,
            "POST",
            "/api/v1/challenges/attempt",
            json={"challenge_id": int(challenge_id), "submission": flag},
        )
        data = resp.json()["data"]
        return SubmissionResult(
            status=data.get("status", "incorrect"),
            message=data.get("message", ""),
        )

    async def close(self):
        await self.client.aclose()

    def _to_challenge(self, data: dict) -> Challenge:
        description = data.get("description", "")
        # CTFd stores service URLs in connection_info — append to description
        connection_info = data.get("connection_info")
        if connection_info:
            description = f"{description}\n\nConnection: {connection_info}"
        return Challenge(
            id=data["id"],
            name=data["name"],
            description=description,
            category=data.get("category", "misc"),
            points=data.get("value", 0),
            files=data.get("files", []),
            hints=[h.get("hint", "") for h in data.get("hints", []) if isinstance(h, dict)],
            solved_by_me=data.get("solved_by_me", False),
            solves=data.get("solves", 0),
            tags=data.get("tags", []),
        )
