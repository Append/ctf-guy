#!/usr/bin/env python3
"""Shared OpenRouter API client with rate-limit retry and backoff.

All OpenRouter calls should go through this client instead of creating
raw openai.AsyncOpenAI instances. Handles:
- 429 retry with X-RateLimit-Reset header parsing
- Exponential backoff with jitter as fallback
- Per-model concurrency limiting
"""

import asyncio
import logging
import random
import time

import openai

log = logging.getLogger(__name__)

# Backoff config
MAX_BACKOFF = 60  # seconds
BASE_BACKOFF = 2  # seconds
MAX_RETRIES_DEFAULT = 3

# Per-model concurrency limit — prevents thundering herd on triage model
MODEL_CONCURRENCY = 5


def _parse_reset_delay(reset_value: str | None) -> float | None:
    """Parse X-RateLimit-Reset (epoch ms) into seconds to wait.

    Returns None if the value can't be parsed.
    """
    if reset_value is None:
        return None
    try:
        reset_epoch_s = int(reset_value) / 1000
        delay = reset_epoch_s - time.time()
        if delay <= 0:
            return 0.5  # Reset already passed, wait briefly
        return delay
    except (ValueError, TypeError):
        return None


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter. Returns seconds to wait."""
    delay = min(BASE_BACKOFF * (2**attempt), MAX_BACKOFF)
    jitter = random.uniform(0, delay * 0.25)
    return delay + jitter


class OpenRouterClient:
    """Shared OpenRouter client with retry logic."""

    def __init__(self, config, max_retries: int = MAX_RETRIES_DEFAULT):
        self.client = openai.AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=config.openrouter_api_key,
        )
        self.max_retries = max_retries
        self._model_semaphores: dict[str, asyncio.Semaphore] = {}

    def _get_semaphore(self, model: str) -> asyncio.Semaphore:
        """Get or create a per-model concurrency semaphore."""
        if model not in self._model_semaphores:
            self._model_semaphores[model] = asyncio.Semaphore(MODEL_CONCURRENCY)
        return self._model_semaphores[model]

    async def chat_completion(self, *, model: str, messages: list, max_tokens: int = 200, **kwargs):
        """Call chat completions with retry on rate limit.

        Args match openai.chat.completions.create(). Returns the response object.
        Raises the last error if all retries exhausted.
        """
        sem = self._get_semaphore(model)
        last_error = None

        for attempt in range(self.max_retries + 1):
            async with sem:
                try:
                    return await self.client.chat.completions.create(
                        model=model,
                        messages=messages,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                except openai.RateLimitError as e:
                    last_error = e
                    # Try to parse X-RateLimit-Reset from error metadata
                    delay = None
                    if hasattr(e, "response") and e.response is not None:
                        reset_header = e.response.headers.get("X-RateLimit-Reset")
                        delay = _parse_reset_delay(reset_header)

                    # Also check error body metadata (OpenRouter puts headers there)
                    if delay is None and hasattr(e, "body") and isinstance(e.body, dict):
                        metadata = e.body.get("error", {}).get("metadata", {})
                        headers = metadata.get("headers", {})
                        reset_val = headers.get("X-RateLimit-Reset")
                        delay = _parse_reset_delay(reset_val)

                    if delay is None:
                        delay = _backoff_delay(attempt)

                    if attempt < self.max_retries:
                        log.warning(
                            f"OpenRouter 429 on {model} (attempt {attempt + 1}/{self.max_retries + 1})"
                            f" — retrying in {delay:.1f}s"
                        )
                        from ai.telemetry import ship_log

                        ship_log(
                            "openrouter.rate_limited",
                            model=model,
                            attempt=attempt + 1,
                            delay_seconds=round(delay, 1),
                        )
                        await asyncio.sleep(delay)
                    else:
                        log.error(f"OpenRouter 429 on {model} — all {self.max_retries + 1} attempts exhausted")
                        from ai.telemetry import ship_log

                        ship_log(
                            "openrouter.rate_limit_exhausted",
                            model=model,
                            attempts=self.max_retries + 1,
                        )
                        raise

        raise last_error  # Should not reach here, but safety net
