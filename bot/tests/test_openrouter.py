#!/usr/bin/env python3
"""Tests for ai/openrouter.py — OpenRouter client with rate-limit retry."""

import asyncio
import time

import pytest

from ai.openrouter import OpenRouterClient, _parse_reset_delay, _backoff_delay


class TestParseResetDelay:
    def test_parses_epoch_ms(self):
        """X-RateLimit-Reset is epoch milliseconds."""
        future_ms = int((time.time() + 5) * 1000)
        delay = _parse_reset_delay(str(future_ms))
        assert 4.0 < delay < 6.0

    def test_past_reset_returns_small_delay(self):
        """If reset is in the past, return a small default."""
        past_ms = int((time.time() - 10) * 1000)
        delay = _parse_reset_delay(str(past_ms))
        assert 0 < delay <= 1.0

    def test_none_returns_none(self):
        assert _parse_reset_delay(None) is None

    def test_garbage_returns_none(self):
        assert _parse_reset_delay("not-a-number") is None


class TestBackoffCalculation:
    def test_backoff_increases(self):
        d1 = _backoff_delay(0)
        d2 = _backoff_delay(1)
        d3 = _backoff_delay(2)
        # Base values are 2, 4, 8 — with jitter they should still be ordered on average
        # Use base values without jitter for deterministic check
        assert 2 <= d1 <= 2.5
        assert 4 <= d2 <= 5.0
        assert 8 <= d3 <= 10.0

    def test_backoff_capped(self):
        d = _backoff_delay(10)
        assert d <= 75  # 60s cap + up to 25% jitter


class TestClientCreation:
    def test_creates_with_config(self, mock_config):
        client = OpenRouterClient(mock_config)
        assert client is not None
        assert client.max_retries == 3

    def test_semaphore_per_model(self, mock_config):
        client = OpenRouterClient(mock_config)
        s1 = client._get_semaphore("model-a")
        s2 = client._get_semaphore("model-a")
        s3 = client._get_semaphore("model-b")
        assert s1 is s2  # Same model = same semaphore
        assert s1 is not s3  # Different model = different semaphore
