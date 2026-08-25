#!/usr/bin/env python3
"""Tests for fail-closed authorization and file-server defaults."""

from dataclasses import replace

import pytest

from config import Config


class TestIsUserAllowed:
    def test_allowlist_permits_listed_user(self, mock_config):
        cfg = replace(mock_config, allowed_user_ids={1, 2}, allow_all_users=False)
        assert cfg.is_user_allowed(1) is True

    def test_allowlist_rejects_unlisted_user(self, mock_config):
        cfg = replace(mock_config, allowed_user_ids={1, 2}, allow_all_users=False)
        assert cfg.is_user_allowed(999) is False

    def test_empty_allowlist_fails_closed(self, mock_config):
        """The bug this guards: an empty allowlist used to permit everyone."""
        cfg = replace(mock_config, allowed_user_ids=set(), allow_all_users=False)
        assert cfg.is_user_allowed(999) is False

    def test_explicit_opt_in_permits_everyone(self, mock_config):
        cfg = replace(mock_config, allowed_user_ids=set(), allow_all_users=True)
        assert cfg.is_user_allowed(999) is True


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """from_env() loads the developer's real bot/.env, which would leak
    ALLOWED_USER_IDS into these tests. Neutralize it so they test the code."""
    monkeypatch.setattr("config.load_dotenv", lambda *a, **k: False)


class TestFromEnvFailsClosed:
    def _base_env(self, **over):
        env = {
            "DISCORD_TOKEN": "x",
            "DISCORD_GUILD_ID": "1",
            "OPENROUTER_API_KEY": "x",
        }
        env.update(over)
        return env

    def test_raises_without_allowlist_or_opt_in(self, monkeypatch):
        for k in ("ALLOWED_USER_IDS", "ALLOW_ALL_USERS"):
            monkeypatch.delenv(k, raising=False)
        for k, v in self._base_env().items():
            monkeypatch.setenv(k, v)
        with pytest.raises(RuntimeError, match="ALLOWED_USER_IDS"):
            Config.from_env()

    def test_allowlist_satisfies_it(self, monkeypatch):
        monkeypatch.delenv("ALLOW_ALL_USERS", raising=False)
        for k, v in self._base_env(ALLOWED_USER_IDS="123").items():
            monkeypatch.setenv(k, v)
        assert Config.from_env().allowed_user_ids == {123}

    def test_explicit_opt_in_satisfies_it(self, monkeypatch):
        for k, v in self._base_env(ALLOWED_USER_IDS="", ALLOW_ALL_USERS="true").items():
            monkeypatch.setenv(k, v)
        assert Config.from_env().allow_all_users is True


class TestFileServerDefaults:
    def test_binds_loopback_by_default(self, monkeypatch):
        monkeypatch.delenv("FILE_SERVER_BIND", raising=False)
        for k, v in {
            "DISCORD_TOKEN": "x",
            "DISCORD_GUILD_ID": "1",
            "OPENROUTER_API_KEY": "x",
            "ALLOWED_USER_IDS": "1",
        }.items():
            monkeypatch.setenv(k, v)
        assert Config.from_env().file_server_bind == "127.0.0.1"
