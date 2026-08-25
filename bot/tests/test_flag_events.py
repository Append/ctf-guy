#!/usr/bin/env python3
"""Tests for ai/flag_events.py — flag confirmation event registry."""

import asyncio

import pytest

from ai.flag_events import (
    register,
    notify,
    unregister,
    get_result,
    FlagResult,
    FLAG_GRACE_PERIOD,
)


class TestRegistry:
    def test_register_returns_event(self):
        event = register(100)
        assert isinstance(event, asyncio.Event)
        assert not event.is_set()
        unregister(100)

    def test_register_same_id_returns_same_event(self):
        e1 = register(200)
        e2 = register(200)
        assert e1 is e2
        unregister(200)

    def test_unregister_cleans_up_event_and_result(self):
        e_old = register(300)
        notify(300, flag="kernel{abc}", solver_id="solver-1", model="haiku")
        assert e_old.is_set()
        assert get_result(300) is not None
        unregister(300)
        # Re-register should give a fresh event, not the stale set one
        e_new = register(300)
        assert not e_new.is_set()
        assert e_old is not e_new
        # Result should also be cleared
        assert get_result(300) is None
        unregister(300)

    def test_unregister_nonexistent_is_safe(self):
        unregister(999)  # Should not raise


class TestNotify:
    def test_notify_sets_event(self):
        event = register(400)
        notify(400)
        assert event.is_set()
        unregister(400)

    def test_notify_stores_flag_result(self):
        register(401)
        notify(401, flag="kernel{test}", solver_id="solver-a", model="opus")
        result = get_result(401)
        assert result is not None
        assert result.flag == "kernel{test}"
        assert result.solver_id == "solver-a"
        assert result.model == "opus"
        unregister(401)

    def test_notify_defaults_to_empty_strings(self):
        register(402)
        notify(402)
        result = get_result(402)
        assert result is not None
        assert result.flag == ""
        assert result.solver_id == ""
        assert result.model == ""
        unregister(402)

    def test_notify_unregistered_is_safe(self):
        notify(888)  # Should not raise

    def test_first_notify_wins(self):
        register(403)
        notify(403, flag="kernel{first}", solver_id="solver-1", model="haiku")
        notify(403, flag="kernel{second}", solver_id="solver-2", model="opus")
        result = get_result(403)
        assert result is not None
        assert result.flag == "kernel{first}"
        assert result.solver_id == "solver-1"
        assert result.model == "haiku"
        unregister(403)

    @pytest.mark.asyncio
    async def test_awaiting_event_unblocks_on_notify(self):
        event = register(500)

        async def delayed_notify():
            await asyncio.sleep(0.05)
            notify(500, flag="kernel{async}", solver_id="solver-x", model="sonnet")

        asyncio.create_task(delayed_notify())
        await asyncio.wait_for(event.wait(), timeout=1.0)
        assert event.is_set()
        result = get_result(500)
        assert result is not None
        assert result.flag == "kernel{async}"
        unregister(500)


class TestGetResult:
    def test_get_result_returns_none_for_unregistered(self):
        assert get_result(9999) is None

    def test_get_result_returns_none_before_notify(self):
        register(600)
        assert get_result(600) is None
        unregister(600)

    def test_get_result_returns_flag_result_after_notify(self):
        register(601)
        notify(601, flag="kernel{result}", solver_id="s", model="m")
        result = get_result(601)
        assert isinstance(result, FlagResult)
        assert result.flag == "kernel{result}"
        unregister(601)


class TestFlagResult:
    def test_flag_result_is_frozen(self):
        result = FlagResult(flag="kernel{x}", solver_id="s", model="m")
        with pytest.raises(Exception):
            result.flag = "kernel{y}"  # type: ignore[misc]

    def test_flag_result_defaults(self):
        result = FlagResult(flag="kernel{z}")
        assert result.solver_id == ""
        assert result.model == ""


class TestGracePeriod:
    def test_grace_period_is_positive(self):
        assert FLAG_GRACE_PERIOD > 0
