#!/usr/bin/env python3
"""Tests for CTFd flag submission."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ctfd.client import CTFdClient
from ctfd.types import CTFdSubmissionResult


class TestCTFdClientSubmit:
    """CTFdClient.submit_flag works correctly."""

    @pytest.mark.asyncio
    async def test_correct_submission(self):
        with (
            patch.object(CTFdClient, "submit_flag", new_callable=AsyncMock) as mock_submit,
            patch.object(CTFdClient, "close", new_callable=AsyncMock),
        ):
            mock_submit.return_value = CTFdSubmissionResult(status="correct", message="Correct!")
            client = CTFdClient("https://ctf.example.com", "test-token")
            result = await client.submit_flag(42, "kernel{test}")
            await client.close()
            assert result.status == "correct"
            mock_submit.assert_called_once_with(42, "kernel{test}")

    @pytest.mark.asyncio
    async def test_incorrect_submission(self):
        with (
            patch.object(CTFdClient, "submit_flag", new_callable=AsyncMock) as mock_submit,
            patch.object(CTFdClient, "close", new_callable=AsyncMock),
        ):
            mock_submit.return_value = CTFdSubmissionResult(status="incorrect", message="Nope")
            client = CTFdClient("https://ctf.example.com", "test-token")
            result = await client.submit_flag(42, "kernel{wrong}")
            await client.close()
            assert result.status == "incorrect"

    @pytest.mark.asyncio
    async def test_already_solved(self):
        with (
            patch.object(CTFdClient, "submit_flag", new_callable=AsyncMock) as mock_submit,
            patch.object(CTFdClient, "close", new_callable=AsyncMock),
        ):
            mock_submit.return_value = CTFdSubmissionResult(status="already_solved", message="Already solved")
            client = CTFdClient("https://ctf.example.com", "test-token")
            result = await client.submit_flag(42, "kernel{test}")
            await client.close()
            assert result.status == "already_solved"


class TestTryAutoSubmitCTFd:
    """try_auto_submit CTFd branch calls CTFdClient.submit_flag."""

    def _make_challenge(self, challenge_dir: str):
        from db.challenges import ChallengeRecord

        return ChallengeRecord(
            id=1,
            ctf_id=1,
            ctfd_id=42,
            name="test-challenge",
            slug="test-challenge",
            category="misc",
            points=100,
            description="Test challenge",
            thread_id="123",
            challenge_dir=challenge_dir,
            solved=False,
            flag=None,
        )

    def _make_config(self, ctfd_url="https://ctf.example.com", ctfd_token="tok", ctfd_session=""):
        cfg = MagicMock()
        cfg.ctfd_url = ctfd_url
        cfg.ctfd_token = ctfd_token
        cfg.ctfd_session = ctfd_session
        return cfg

    @pytest.mark.asyncio
    async def test_ctfd_branch_calls_submit_flag(self, tmp_path):
        """CTFd branch creates CTFdClient and calls submit_flag."""
        (tmp_path / "challenge.json").write_text(json.dumps({"platform": "ctfd"}))
        (tmp_path / "flag.txt").write_text("kernel{test_flag}")

        challenge = self._make_challenge(str(tmp_path))
        config = self._make_config()
        thread = AsyncMock()
        db_conn = MagicMock()

        mock_result = CTFdSubmissionResult(status="correct", message="Correct!")

        with (
            patch("ctfd.client.CTFdClient") as MockClient,
            patch("ai.flag_tracker.flag_tracker") as mock_tracker,
            patch("ai.solve_utils.mark_solved") as mock_mark,
            patch("ai.solve_utils.update_thread_status", new_callable=AsyncMock),
            patch("ai.telemetry.ship_metric"),
            patch("ai.telemetry.ship_log"),
        ):
            mock_tracker.check_dedup.return_value = False
            mock_tracker.get_cooldown_remaining.return_value = 0
            mock_instance = AsyncMock()
            mock_instance.submit_flag.return_value = mock_result
            MockClient.return_value = mock_instance

            from ai.solve_utils import try_auto_submit

            result = await try_auto_submit(thread, challenge, db_conn, set(), config=config)

        assert result is True
        MockClient.assert_called_once_with("https://ctf.example.com", token="tok", session="")
        mock_instance.submit_flag.assert_called_once_with(42, "kernel{test_flag}")
        mock_instance.close.assert_called_once()
        mock_mark.assert_called_once()

    @pytest.mark.asyncio
    async def test_ctfd_branch_no_credentials(self, tmp_path):
        """CTFd branch returns False and warns when credentials are missing."""
        (tmp_path / "challenge.json").write_text(json.dumps({"platform": "ctfd"}))
        (tmp_path / "flag.txt").write_text("kernel{test_flag}")

        challenge = self._make_challenge(str(tmp_path))
        config = self._make_config(ctfd_url="", ctfd_token="")
        thread = AsyncMock()
        db_conn = MagicMock()

        with patch("ai.flag_tracker.flag_tracker") as mock_tracker:
            mock_tracker.check_dedup.return_value = False
            mock_tracker.get_cooldown_remaining.return_value = 0

            from ai.solve_utils import try_auto_submit

            result = await try_auto_submit(thread, challenge, db_conn, set(), config=config)

        assert result is False

    @pytest.mark.asyncio
    async def test_ctfd_branch_no_config(self, tmp_path):
        """CTFd branch returns False when config=None (backwards compat)."""
        (tmp_path / "challenge.json").write_text(json.dumps({"platform": "ctfd"}))
        (tmp_path / "flag.txt").write_text("kernel{test_flag}")

        challenge = self._make_challenge(str(tmp_path))
        thread = AsyncMock()
        db_conn = MagicMock()

        with patch("ai.flag_tracker.flag_tracker") as mock_tracker:
            mock_tracker.check_dedup.return_value = False
            mock_tracker.get_cooldown_remaining.return_value = 0

            from ai.solve_utils import try_auto_submit

            result = await try_auto_submit(thread, challenge, db_conn, set(), config=None)

        assert result is False
