#!/usr/bin/env python3
"""Bot configuration loaded from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _parse_race_timeouts(raw: str) -> dict[str, int]:
    """Parse RACE_TIMEOUTS env var: 'haiku=120,codex=180' → {'haiku': 120, 'codex': 180}"""
    result = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            model, secs = pair.split("=", 1)
            result[model.strip()] = int(secs.strip())
    return result


@dataclass
class Config:
    discord_token: str
    discord_guild_id: int
    openrouter_api_key: str
    ctfd_url: str
    ctfd_token: str
    ctfd_session: str  # Session cookie (fallback if API tokens not available)
    allowed_user_ids: set[int]
    ctf_root: Path
    default_model: str
    heavy_model: str
    triage_model: str
    autosolve_model: str  # Claude model for auto-solve (haiku/sonnet/opus)
    autosolve_effort: str  # Effort level for auto-solve (low/medium/high/max)
    autosolve_subagent: str  # Subagent model for auto-solve (overrides global setting)
    autosolve_concurrency: int  # Number of concurrent solvers
    autosolve_timeout_base: int  # Base timeout in seconds
    autosolve_timeout_per_point: int  # Extra seconds per point
    autosolve_timeout_max: int  # Max timeout cap in seconds
    autosolve_max_budget: float  # Max USD per solve attempt
    race_enabled: bool  # Enable multi-model racing
    race_models: list[str]  # Models to race (e.g. ["haiku", "opus"])
    race_timeouts: dict[str, int]  # Per-model timeout overrides (e.g. {"haiku": 120})
    codex_enabled: bool  # Enable Codex as a racer
    ghidra_mcp_enabled: bool  # Enable Ghidra MCP decompiler for rev/pwn challenges
    manager_max_interventions: int  # Max manager corrections per solve (0 = disabled)
    manager_corrections: bool  # Enable non-security manager corrections (True = all detectors)
    manager_advice_model: str  # Model for generating corrections (empty = use triage_model)
    deep_analysis_model: str  # Model for deep analysis teardown subagents
    fast_mode: bool  # Enable Claude Code fast mode (faster output, higher cost)
    file_server_port: int  # Port for challenge file server (0 = disabled)
    victoria_logs_url: str  # VictoriaLogs URL (empty = disabled)
    victoria_metrics_url: str  # VictoriaMetrics URL (empty = disabled)
    telemetry_batch_size: int  # Events per flush batch
    telemetry_flush_interval: float  # Seconds between flushes

    @classmethod
    def from_env(cls) -> "Config":
        """Load config from .env file and environment variables."""
        # Load .env from bot directory, then repo root
        bot_dir = Path(__file__).parent
        load_dotenv(bot_dir / ".env")
        load_dotenv(bot_dir.parent / ".env")

        allowed = os.environ.get("ALLOWED_USER_IDS", "")
        allowed_ids = {int(uid.strip()) for uid in allowed.split(",") if uid.strip()}

        return cls(
            discord_token=os.environ["DISCORD_TOKEN"],
            discord_guild_id=int(os.environ["DISCORD_GUILD_ID"]),
            openrouter_api_key=os.environ["OPENROUTER_API_KEY"],
            ctfd_url=os.environ.get("CTFD_URL", ""),
            ctfd_token=os.environ.get("CTFD_TOKEN", ""),
            ctfd_session=os.environ.get("CTFD_SESSION", ""),
            allowed_user_ids=allowed_ids,
            ctf_root=Path(os.environ.get("CTF_ROOT", str(bot_dir.parent))),
            default_model=os.environ.get("DEFAULT_MODEL", "anthropic/claude-sonnet-4.6"),
            heavy_model=os.environ.get("HEAVY_MODEL", "anthropic/claude-opus-4.6"),
            triage_model=os.environ.get("TRIAGE_MODEL", "google/gemini-3-flash-preview"),
            autosolve_model=os.environ.get("AUTOSOLVE_MODEL", "haiku"),
            autosolve_effort=os.environ.get("AUTOSOLVE_EFFORT", "medium"),
            autosolve_subagent=os.environ.get("AUTOSOLVE_SUBAGENT", "haiku"),
            autosolve_concurrency=int(os.environ.get("AUTOSOLVE_CONCURRENCY", "10")),
            autosolve_timeout_base=int(os.environ.get("AUTOSOLVE_TIMEOUT_BASE", "180")),
            autosolve_timeout_per_point=int(os.environ.get("AUTOSOLVE_TIMEOUT_PER_POINT", "3")),
            autosolve_timeout_max=int(os.environ.get("AUTOSOLVE_TIMEOUT_MAX", "600")),
            autosolve_max_budget=float(os.environ.get("AUTOSOLVE_MAX_BUDGET", "0")),
            race_enabled=os.environ.get("RACE_ENABLED", "").lower() in ("1", "true", "yes"),
            race_models=os.environ.get("RACE_MODELS", "haiku,opus").split(","),
            race_timeouts=_parse_race_timeouts(os.environ.get("RACE_TIMEOUTS", "")),
            codex_enabled=os.environ.get("CODEX_ENABLED", "").lower() in ("1", "true", "yes"),
            ghidra_mcp_enabled=os.environ.get("GHIDRA_MCP_ENABLED", "").lower() in ("1", "true", "yes"),
            manager_max_interventions=int(os.environ.get("MANAGER_MAX_INTERVENTIONS", "10")),
            manager_corrections=os.environ.get("MANAGER_CORRECTIONS", "true").lower() in ("1", "true", "yes"),
            manager_advice_model=os.environ.get("MANAGER_ADVICE_MODEL", ""),
            deep_analysis_model=os.environ.get("DEEP_ANALYSIS_MODEL", "haiku"),
            fast_mode=os.environ.get("FAST_MODE", "").lower() in ("1", "true", "yes"),
            file_server_port=int(os.environ.get("FILE_SERVER_PORT", "8080")),
            victoria_logs_url=os.environ.get("VICTORIA_LOGS_URL", ""),
            victoria_metrics_url=os.environ.get("VICTORIA_METRICS_URL", ""),
            telemetry_batch_size=int(os.environ.get("TELEMETRY_BATCH_SIZE", "50")),
            telemetry_flush_interval=float(os.environ.get("TELEMETRY_FLUSH_INTERVAL", "1.0")),
        )
