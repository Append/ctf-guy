#!/usr/bin/env python3
"""picoCTF platform adapter using Playwright for auth + httpx for API.

Login requires manual interaction (reCAPTCHA), so the flow is:
1. Open browser, user logs in manually
2. Extract session cookies
3. Use httpx with those cookies for all API calls

Cookies are cached to pico_cookies.json so subsequent runs skip login.
"""

import asyncio
import json
import logging
import re
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

from .base import Challenge, CTFPlatform, SubmissionResult

log = logging.getLogger(__name__)

BASE_URL = "https://play.picoctf.org"
COOKIES_FILE = Path(__file__).parent.parent / "data" / "pico_cookies.json"


class PicoCTFPlatform(CTFPlatform):
    """Adapter for picoCTF's custom platform.

    picoCTF API shape:
    - GET /api/challenges/?page_size=50&page=N — paginated challenge list
    - GET /api/challenges/{id}/ — challenge metadata (no description)
    - GET /api/challenges/{id}/instance/ — description (HTML), files, hints
    - POST /api/challenges/{id}/attempt/ — flag submission

    Challenge list fields:
        id, name, author, difficulty (1-3), category {id, name},
        event {id, name}, tags [{id, name}], event_points,
        users_solved, solved_by_user, under_maintenance, retired

    Instance fields (from /instance/):
        id, status, description (HTML), expires_in, files (in description HTML)
    """

    def __init__(self, event: str | None = None):
        self._api_client: httpx.AsyncClient | None = None
        self._csrf_token: str = ""
        self._playwright = None
        self._browser = None
        self._event_filter = event

    async def _ensure_session(self):
        """Load cookies from cache or do manual browser login."""
        if self._api_client is not None:
            return

        cookies = await self._load_or_login()
        self._api_client = self._make_client(cookies)

        # Verify session is valid
        resp = await self._api_client.get("/api/_allauth/browser/v1/auth/session")
        if resp.status_code == 401:
            log.info("Cached cookies expired, re-authenticating...")
            await self._api_client.aclose()  # Close old client before replacing
            COOKIES_FILE.unlink(missing_ok=True)
            cookies = await self._load_or_login()
            self._api_client = self._make_client(cookies)

    def _make_client(self, cookies: dict[str, str]) -> httpx.AsyncClient:
        """Create an httpx client with cookies and CSRF header."""
        csrf = cookies.get("csrftoken", "")
        return httpx.AsyncClient(
            base_url=BASE_URL,
            cookies=cookies,  # csrftoken stays in cookies
            headers={
                "Referer": f"{BASE_URL}/practice",
                "X-CSRFToken": csrf,  # AND in the header
            },
            timeout=30.0,
        )

    async def _load_or_login(self) -> dict[str, str]:
        """Load cookies from file, or open browser for manual login."""
        if COOKIES_FILE.exists():
            try:
                raw = json.loads(COOKIES_FILE.read_text())
                # Filter to play.picoctf.org only to avoid duplicate cookie names
                cookies = {c["name"]: c["value"] for c in raw if "picoctf" in c.get("domain", "")}
                if "sessionid" in cookies:
                    log.info("Loaded cached picoCTF session")
                    return cookies
            except (json.JSONDecodeError, KeyError):
                pass

        # Need manual login
        log.info("Opening browser for picoCTF login (reCAPTCHA required)...")
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--ozone-platform=x11",  # Force X11 — Wayland under WSLg hides the window
                ],
            )
            context = await self._browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            await page.goto(f"{BASE_URL}/login")

            log.info(">>> Log in manually in the browser window <<<")

            # Wait for user to complete login
            for _ in range(180):
                await asyncio.sleep(1)
                if "/practice" in page.url or "/compete" in page.url:
                    break
            else:
                raise TimeoutError("Login timed out after 3 minutes")

            log.info("Login successful!")

            # Extract and cache cookies
            raw_cookies = await context.cookies()
            COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            COOKIES_FILE.write_text(json.dumps(raw_cookies, indent=2))

            cookies = {c["name"]: c["value"] for c in raw_cookies if "picoctf" in c.get("domain", "")}
        finally:
            if self._browser:
                await self._browser.close()
            await self._playwright.stop()
            self._browser = None
            self._playwright = None

        return cookies

    async def fetch_challenges(self) -> list[Challenge]:
        await self._ensure_session()

        # Resolve event name → id for server-side filtering
        event_id = None
        if self._event_filter:
            event_id = await self._resolve_event_id(self._event_filter)
            if event_id:
                log.info(f"Filtering by event id={event_id} for '{self._event_filter}'")
            else:
                log.info(f"Event '{self._event_filter}' not found via API, will filter client-side")

        all_challenges = []
        page_num = 1
        while True:
            url = f"/api/challenges/?page_size=50&page={page_num}"
            if event_id:
                url += f"&event={event_id}"
            resp = await self._api_client.get(url)
            resp.raise_for_status()
            data = resp.json()

            results = data.get("results", [])
            for c in results:
                all_challenges.append(self._list_to_challenge(c))

            if data.get("next") and results:
                page_num += 1
                await asyncio.sleep(0.3)
            else:
                break

        # Always filter client-side — API may silently ignore the event param
        if self._event_filter:
            filter_lower = self._event_filter.lower()
            before = len(all_challenges)
            all_challenges = [
                c for c in all_challenges if filter_lower in (c.extra.get("event") or {}).get("name", "").lower()
            ]
            log.info(f"Event filter '{self._event_filter}': {before} -> {len(all_challenges)} challenges")

        log.info(f"Fetched {len(all_challenges)} challenges from picoCTF")
        return all_challenges

    async def _resolve_event_id(self, event_name: str) -> int | None:
        """Try to resolve an event name to its API id."""
        try:
            resp = await self._api_client.get("/api/events/")
            if resp.status_code != 200:
                return None
            data = resp.json()
            events = data.get("results", data) if isinstance(data, dict) else data
            if not isinstance(events, list):
                return None
            name_lower = event_name.lower()
            for ev in events:
                if isinstance(ev, dict) and name_lower in ev.get("name", "").lower():
                    return ev["id"]
        except Exception as e:
            log.debug(f"Event lookup failed: {e}")
        return None

    async def fetch_challenge(self, challenge_id: int | str) -> Challenge:
        """Fetch challenge with full description, launching instance if needed.

        picoCTF challenges often require launching an instance to get the
        real description, file URLs, and service endpoints. The instance
        is time-gated (~30 min) and must be launched via POST.
        """
        await self._ensure_session()

        # Get base info
        resp = await self._api_client.get(f"/api/challenges/{challenge_id}/")
        resp.raise_for_status()
        base = resp.json()

        # Try getting instance — may need to launch it first
        instance = await self._get_or_launch_instance(challenge_id)

        return self._detail_to_challenge(base, instance)

    async def _get_or_launch_instance(self, challenge_id: int | str) -> dict:
        """Get an instance, launching one if not running."""
        resp = await self._api_client.get(f"/api/challenges/{challenge_id}/instance/")

        if resp.status_code == 200:
            instance = resp.json()
            # If already running, return it
            if instance.get("status") == "RUNNING":
                return instance

            # If not running (or no description), launch it
            if not instance.get("description") or instance.get("status") == "NOT_RUNNING":
                return await self._launch_instance(challenge_id)

            return instance

        # No instance endpoint (static challenge) — return empty
        if resp.status_code == 404:
            return {}

        # Try launching
        return await self._launch_instance(challenge_id)

    async def _launch_instance(self, challenge_id: int | str) -> dict:
        """Launch a challenge instance via POST."""
        resp = await self._api_client.post(
            f"/api/challenges/{challenge_id}/instance/",
        )

        if resp.status_code in (200, 201):
            instance = resp.json()
            log.info(
                f"Launched instance for challenge {challenge_id}: "
                f"status={instance.get('status')}, expires_in={instance.get('expires_in')}s"
            )
            return instance

        log.warning(f"Failed to launch instance for {challenge_id}: {resp.status_code}")
        # Fall back to GET in case it launched but returned weird status
        resp2 = await self._api_client.get(f"/api/challenges/{challenge_id}/instance/")
        if resp2.status_code == 200:
            return resp2.json()
        return {}

    async def download_file(self, file_url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # picoCTF files are on challenge-files.picoctf.net (public, no auth needed)
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as dl:
            resp = await dl.get(file_url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        return dest

    async def submit_flag(self, challenge_id: int | str, flag: str) -> SubmissionResult:
        """Submit a flag via POST /api/submissions/.

        Body: {"challenge": <id>, "flag": "<flag>"}
        Response: {"challenge": <id>, "flag": "...", "correct": bool, "historical": bool}
        """
        await self._ensure_session()

        resp = await self._api_client.post(
            "/api/submissions/",
            json={"challenge": int(challenge_id), "flag": flag},
        )

        if resp.status_code == 429:
            return SubmissionResult(status="rate_limited", message="Rate limited")

        resp.raise_for_status()
        data = resp.json()

        if data.get("correct"):
            if data.get("historical"):
                return SubmissionResult(status="already_solved", message="Already solved")
            return SubmissionResult(status="correct", message="Correct!")
        else:
            return SubmissionResult(status="incorrect", message="Incorrect flag")

    async def close(self):
        if self._api_client:
            await self._api_client.aclose()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    def _list_to_challenge(self, data: dict) -> Challenge:
        """Convert challenge list API response to Challenge."""
        category = data.get("category") or {}
        cat_name = category.get("name", "misc") if isinstance(category, dict) else str(category)

        return Challenge(
            id=data["id"],
            name=data["name"],
            description="",  # Not in list endpoint, fetched via /instance/
            category=cat_name,
            points=data.get("event_points", 0),
            files=[],  # Not in list endpoint
            hints=[],
            solves=data.get("users_solved", 0),
            solved_by_me=data.get("solved_by_user", False),
            tags=[t["name"] for t in data.get("tags", []) if isinstance(t, dict)],
            extra={
                "difficulty": data.get("difficulty", 0),
                "author": data.get("author", ""),
                "event": data.get("event") or {},
                "retired": data.get("retired", False),
                "under_maintenance": data.get("under_maintenance", False),
            },
        )

    def _detail_to_challenge(self, base: dict, instance: dict) -> Challenge:
        """Convert challenge detail + instance response to Challenge."""
        challenge = self._list_to_challenge(base)

        # Extract description (HTML → plain text)
        desc_html = instance.get("description", "")
        challenge.description = self._html_to_text(desc_html)

        # Extract file URLs from description HTML (download links)
        urls = re.findall(r"href=[\"']([^\"']+)[\"']", desc_html)
        challenge.files = [
            u
            for u in urls
            if "challenge-files" in u or u.endswith((".zip", ".gz", ".txt", ".py", ".c", ".png", ".jpg", ".pcap"))
        ]

        # Extract hints
        raw_hints = instance.get("hints", [])
        challenge.hints = [self._html_to_text(h) if isinstance(h, str) else str(h) for h in raw_hints]

        # Capture endpoints (for web/pwn challenges with running services)
        endpoints = instance.get("endpoints", [])
        if endpoints:
            challenge.extra["endpoints"] = endpoints

        # Capture instance status
        if instance.get("status"):
            challenge.extra["instance_status"] = instance["status"]
            challenge.extra["expires_in"] = instance.get("expires_in")

        return challenge

    def _html_to_text(self, html: str) -> str:
        """Simple HTML to text conversion."""
        if not html:
            return ""
        text = re.sub(r"<br\s*/?>", "\n", html)
        text = re.sub(r"<p>", "\n", text)
        text = re.sub(r"</p>", "", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&quot;", '"').replace("&#39;", "'")
        return text.strip()
