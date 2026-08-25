#!/usr/bin/env python3
"""Tests for flag-aware timeout bypass logic."""

import asyncio

import pytest

from ai.flag_events import FLAG_GRACE_PERIOD, register, notify, unregister


class TestTwoPhaseTimeout:
    """Test the two-phase timeout pattern used in _process_stream."""

    @pytest.mark.asyncio
    async def test_no_flag_respects_original_timeout(self):
        """Without flag event, solver is killed at original timeout."""
        flag_event = asyncio.Event()
        timed_out = False

        async def fake_solver():
            await asyncio.sleep(100)

        solver = asyncio.create_task(fake_solver())
        done, pending = await asyncio.wait(
            {solver},
            timeout=0.2,
        )

        if solver not in done:
            timed_out = True
            solver.cancel()
            try:
                await solver
            except asyncio.CancelledError:
                pass

        assert timed_out
        assert not flag_event.is_set()

    @pytest.mark.asyncio
    async def test_flag_event_extends_timeout(self):
        """When flag event fires before timeout, solver gets grace period."""
        flag_event = asyncio.Event()
        completed = False

        async def fake_solver():
            nonlocal completed
            await asyncio.sleep(0.4)
            completed = True

        solver = asyncio.create_task(fake_solver())

        # Simulate flag confirmed at 0.1s
        await asyncio.sleep(0.1)
        flag_event.set()

        # Original timeout 0.2s would kill solver. But flag is set,
        # so we grant grace period instead.
        done, pending = await asyncio.wait({solver}, timeout=0.2)
        if solver not in done and flag_event.is_set():
            # Grace period
            await asyncio.wait_for(solver, timeout=FLAG_GRACE_PERIOD)

        assert completed

    @pytest.mark.asyncio
    async def test_grace_period_still_enforced(self):
        """Even after flag, solver can't run forever."""
        flag_event = asyncio.Event()
        flag_event.set()

        async def endless_solver():
            await asyncio.sleep(1000)

        solver = asyncio.create_task(endless_solver())
        done, pending = await asyncio.wait({solver}, timeout=0.0)

        # Flag is set, grant grace period — but it's short for test
        timed_out = False
        try:
            await asyncio.wait_for(solver, timeout=0.1)
        except TimeoutError:
            timed_out = True

        assert timed_out


class TestRaceFlagSentinel:
    """Test the race loop's flag event sentinel pattern."""

    @pytest.mark.asyncio
    async def test_sentinel_wakes_race_loop_instantly(self):
        """Flag event sentinel in asyncio.wait wakes the loop immediately."""
        event = register(600)
        sentinel = asyncio.create_task(event.wait())

        # Simulate two "racer" tasks that take a long time
        async def slow_racer():
            await asyncio.sleep(100)

        racer1 = asyncio.create_task(slow_racer())
        racer2 = asyncio.create_task(slow_racer())

        # Fire notify after 0.05s — should wake the wait instantly
        async def fire():
            await asyncio.sleep(0.05)
            notify(600, flag="picoCTF{test}", solver_id="1")

        asyncio.create_task(fire())

        # Wait with 5s timeout (simulating the race poll interval)
        # Should return much faster than 5s because sentinel completes
        import time

        start = time.monotonic()
        done, pending = await asyncio.wait(
            {racer1, racer2, sentinel},
            return_when=asyncio.FIRST_COMPLETED,
            timeout=5,
        )
        elapsed = time.monotonic() - start

        assert sentinel in done
        assert event.is_set()
        assert elapsed < 1.0  # Way faster than 5s timeout

        # Cleanup
        racer1.cancel()
        racer2.cancel()
        for t in [racer1, racer2]:
            try:
                await t
            except asyncio.CancelledError:
                pass
        unregister(600)

    @pytest.mark.asyncio
    async def test_race_returns_without_blocking_on_winner(self):
        """Race loop should return result immediately, not wait for winner grace period."""
        event = register(700)
        completed = False

        async def winning_racer():
            nonlocal completed
            await asyncio.sleep(0.5)
            completed = True

        winner = asyncio.create_task(winning_racer())

        # Flag confirmed immediately
        notify(700, flag="picoCTF{test}", solver_id="1")

        # Race loop can return now without waiting for winner
        assert event.is_set()
        assert not winner.done()  # Winner still running — that's fine

        # Cleanup
        winner.cancel()
        try:
            await winner
        except asyncio.CancelledError:
            pass
        unregister(700)
        assert not completed  # Winner was still working when we returned
