#!/usr/bin/env python3
"""Flag submission throttling and deduplication.

Prevents submitting the same wrong flag twice and enforces escalating
cooldowns after incorrect submissions to avoid rate limits/bans.
"""

import logging
import time

log = logging.getLogger(__name__)

# Escalating cooldowns in seconds after consecutive incorrect submissions
COOLDOWNS = [0, 30, 120, 300, 600]  # 0s, 30s, 2m, 5m, 10m


class FlagTracker:
    """Track flag submissions per challenge for throttling and dedup."""

    def __init__(self):
        # challenge_id -> list of {flag, time, result}
        self.submissions: dict[int, list[dict]] = {}

    def check_dedup(self, challenge_id: int, flag: str) -> bool:
        """Returns True if this exact flag was already submitted for this challenge."""
        history = self.submissions.get(challenge_id, [])
        return any(s["flag"] == flag for s in history)

    def get_cooldown_remaining(self, challenge_id: int) -> int:
        """Returns seconds remaining until next submission is allowed.

        Based on consecutive incorrect submissions.
        """
        history = self.submissions.get(challenge_id, [])
        if not history:
            return 0

        # Count consecutive incorrect submissions from the end
        consecutive_wrong = 0
        for s in reversed(history):
            if s["result"] == "incorrect":
                consecutive_wrong += 1
            else:
                break

        if consecutive_wrong == 0:
            return 0

        # Get cooldown for this number of wrong attempts
        cooldown_idx = min(consecutive_wrong - 1, len(COOLDOWNS) - 1)
        cooldown_seconds = COOLDOWNS[cooldown_idx]

        if cooldown_seconds == 0:
            return 0

        # Check if cooldown has elapsed since last submission
        last_time = history[-1]["time"]
        elapsed = time.time() - last_time
        remaining = cooldown_seconds - elapsed

        return max(0, int(remaining))

    def record(self, challenge_id: int, flag: str, result: str) -> None:
        """Record a submission result."""
        if challenge_id not in self.submissions:
            self.submissions[challenge_id] = []

        self.submissions[challenge_id].append(
            {
                "flag": flag,
                "time": time.time(),
                "result": result,
            }
        )

        log.info(
            f"Flag tracked: challenge={challenge_id} result={result} "
            f"total_attempts={len(self.submissions[challenge_id])}"
        )

    def get_attempt_count(self, challenge_id: int) -> int:
        """Get total submission attempts for a challenge."""
        return len(self.submissions.get(challenge_id, []))


# Singleton instance — shared across all submission paths
flag_tracker = FlagTracker()
