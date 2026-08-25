#!/usr/bin/env python3
"""JSONL trace logging for solve attempts.

Append-only structured logs for post-competition analysis.
"""

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


class SolveTracer:
    """Append-only JSONL event logger per challenge."""

    def __init__(self, challenge_dir: str):
        self.log_path = Path(challenge_dir) / "trace.jsonl"

    def event(self, event_type: str, **data) -> None:
        """Append a trace event to JSONL and ship to telemetry."""
        entry = {"ts": time.time(), "type": event_type, **data}
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
                f.flush()
        except Exception as e:
            log.warning(f"Trace write failed: {e}")

        # Ship to VictoriaLogs (no-op if telemetry disabled)
        from ai.telemetry import ship_log

        ship_log(f"solve.{event_type}", **data)

    def solve_start(
        self,
        challenge_name: str,
        category: str,
        points: int,
        model: str = "",
        effort: str = "",
        budget: float = 0,
    ) -> None:
        self.event(
            "solve_start",
            challenge=challenge_name,
            category=category,
            points=points,
            model=model,
            effort=effort,
            budget=budget,
        )

    def instance_launch(self, status: str, duration_ms: int = 0) -> None:
        self.event("instance_launch", status=status, duration_ms=duration_ms)

    def solve_complete(
        self, cost_usd: float, num_turns: int, duration_ms: int, flag_found: bool
    ) -> None:
        self.event(
            "solve_complete",
            cost_usd=cost_usd,
            num_turns=num_turns,
            duration_ms=duration_ms,
            flag_found=flag_found,
        )

        from ai.telemetry import ship_metric

        result = "found" if flag_found else "failed"
        ship_metric("ctf_solve_cost_usd", cost_usd, result=result)
        ship_metric("ctf_solve_duration_seconds", duration_ms / 1000, result=result)
        ship_metric("ctf_solve_turns", num_turns, result=result)

    def flag_submit(self, flag: str, result: str, cooldown: int = 0) -> None:
        # Redact flag in trace (keep first/last 4 chars)
        redacted = flag[:8] + "..." + flag[-4:] if len(flag) > 16 else flag
        self.event("flag_submit", flag=redacted, result=result, cooldown=cooldown)

        from ai.telemetry import ship_metric

        ship_metric("ctf_flag_submissions_total", 1, result=result)

    def failure_analysis(self, analysis: str) -> None:
        self.event("failure_analysis", analysis=analysis[:500])

    def learn(self, pattern: str) -> None:
        self.event("learn", pattern=pattern[:200])

    def hint_received(self, hint: str) -> None:
        self.event("hint_received", hint=hint[:500])
