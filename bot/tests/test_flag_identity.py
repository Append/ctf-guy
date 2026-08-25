#!/usr/bin/env python3
"""Tests for solver-identity-aware flag detection in race mode."""

import asyncio

import pytest

from ai.flag_events import register, notify, get_result, unregister, FlagResult


class TestRaceWinnerIdentification:
    def test_get_result_returns_solver_id(self):
        register(1000)
        notify(1000, flag="picoCTF{race_flag}", solver_id="2", model="opus")
        result = get_result(1000)
        assert result is not None
        assert result.solver_id == "2"
        assert result.model == "opus"
        assert result.flag == "picoCTF{race_flag}"
        unregister(1000)

    def test_solver_id_maps_to_racer_index(self):
        register(1001)
        solver_names = ["haiku", "opus", "codex"]
        notify(1001, flag="picoCTF{opus_wins}", solver_id="2")
        result = get_result(1001)
        winner_idx = int(result.solver_id) - 1
        assert solver_names[winner_idx] == "opus"
        unregister(1001)

    @pytest.mark.asyncio
    async def test_flag_event_carries_identity_through_await(self):
        event = register(1002)

        async def delayed_submit():
            await asyncio.sleep(0.05)
            notify(1002, flag="picoCTF{delayed}", solver_id="3", model="codex")

        asyncio.create_task(delayed_submit())
        await asyncio.wait_for(event.wait(), timeout=1.0)
        result = get_result(1002)
        assert result.solver_id == "3"
        assert result.model == "codex"
        unregister(1002)

    def test_first_submitter_wins_race(self):
        register(1003)
        notify(1003, flag="picoCTF{first}", solver_id="1", model="haiku")
        notify(1003, flag="picoCTF{second}", solver_id="2", model="opus")
        result = get_result(1003)
        assert result.solver_id == "1"
        assert result.flag == "picoCTF{first}"
        unregister(1003)
