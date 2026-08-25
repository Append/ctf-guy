#!/usr/bin/env python3
"""Retry logic for CTFd API calls with backoff on 429/5xx."""

import asyncio
import logging
import random

import httpx

log = logging.getLogger(__name__)

MAX_RETRIES = 3
BASE_BACKOFF = 2  # seconds
MAX_BACKOFF = 30  # seconds


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter."""
    delay = min(BASE_BACKOFF * (2**attempt), MAX_BACKOFF)
    jitter = random.uniform(0, delay * 0.25)
    return delay + jitter


async def ctfd_request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """Make an HTTP request with retry on 429 and 5xx errors.

    Args:
        client: httpx.AsyncClient instance
        method: HTTP method (get, post, etc.)
        url: URL path
        **kwargs: passed to client.request()

    Returns:
        httpx.Response on success

    Raises:
        httpx.HTTPStatusError: if all retries exhausted
    """
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = await client.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 429 or status >= 500:
                last_error = e
                if attempt < MAX_RETRIES:
                    # Try to parse Retry-After header
                    retry_after = e.response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            delay = _backoff_delay(attempt)
                    else:
                        delay = _backoff_delay(attempt)

                    log.warning(
                        f"CTFd {status} on {method.upper()} {url} "
                        f"(attempt {attempt + 1}/{MAX_RETRIES + 1}) — retrying in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    log.error(f"CTFd {status} on {method.upper()} {url} — all retries exhausted")
                    raise
            else:
                # 4xx (not 429) — don't retry
                raise

    raise last_error  # safety net
