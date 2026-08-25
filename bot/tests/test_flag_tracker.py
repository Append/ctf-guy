#!/usr/bin/env python3
"""Tests for ai/flag_tracker.py — submission throttling and deduplication."""

from ai.flag_tracker import FlagTracker


class TestDedup:
    def test_first_submission_not_deduped(self):
        tracker = FlagTracker()
        assert tracker.check_dedup(1, "picoCTF{test}") is False

    def test_same_flag_deduped(self):
        tracker = FlagTracker()
        tracker.record(1, "picoCTF{test}", "incorrect")
        assert tracker.check_dedup(1, "picoCTF{test}") is True

    def test_different_flag_not_deduped(self):
        tracker = FlagTracker()
        tracker.record(1, "picoCTF{test1}", "incorrect")
        assert tracker.check_dedup(1, "picoCTF{test2}") is False

    def test_different_challenge_not_deduped(self):
        tracker = FlagTracker()
        tracker.record(1, "picoCTF{test}", "incorrect")
        assert tracker.check_dedup(2, "picoCTF{test}") is False

    def test_correct_flag_still_deduped(self):
        tracker = FlagTracker()
        tracker.record(1, "picoCTF{correct}", "correct")
        assert tracker.check_dedup(1, "picoCTF{correct}") is True


class TestThrottling:
    def test_no_cooldown_initially(self):
        tracker = FlagTracker()
        assert tracker.get_cooldown_remaining(1) == 0

    def test_no_cooldown_after_correct(self):
        tracker = FlagTracker()
        tracker.record(1, "picoCTF{right}", "correct")
        assert tracker.get_cooldown_remaining(1) == 0

    def test_first_wrong_no_cooldown(self):
        tracker = FlagTracker()
        tracker.record(1, "picoCTF{wrong}", "incorrect")
        # First wrong → COOLDOWNS[0] = 0s
        assert tracker.get_cooldown_remaining(1) == 0

    def test_second_wrong_has_cooldown(self):
        tracker = FlagTracker()
        tracker.record(1, "picoCTF{wrong1}", "incorrect")
        tracker.record(1, "picoCTF{wrong2}", "incorrect")
        # Second wrong → COOLDOWNS[1] = 30s
        cooldown = tracker.get_cooldown_remaining(1)
        assert cooldown > 0
        assert cooldown <= 30

    def test_cooldown_after_correct_resets(self):
        tracker = FlagTracker()
        tracker.record(1, "picoCTF{wrong}", "incorrect")
        tracker.record(1, "picoCTF{wrong2}", "incorrect")
        tracker.record(1, "picoCTF{right}", "correct")
        # After a correct, consecutive wrong count resets
        assert tracker.get_cooldown_remaining(1) == 0


class TestAttemptCount:
    def test_empty(self):
        tracker = FlagTracker()
        assert tracker.get_attempt_count(1) == 0

    def test_after_submissions(self):
        tracker = FlagTracker()
        tracker.record(1, "a", "incorrect")
        tracker.record(1, "b", "incorrect")
        tracker.record(1, "c", "correct")
        assert tracker.get_attempt_count(1) == 3
