#!/usr/bin/env python3
"""System performance monitor — ships CPU, memory, process metrics to VictoriaMetrics."""

import asyncio
import logging
import os

log = logging.getLogger(__name__)


async def start_sysmon(interval: int = 15):
    """Background task that ships system metrics every `interval` seconds."""
    from ai.telemetry import ship_metric

    try:
        while True:
            await asyncio.sleep(interval)
            try:
                _ship_metrics(ship_metric)
            except Exception as e:
                log.debug(f"Sysmon error: {e}")
    except asyncio.CancelledError:
        pass


def _ship_metrics(ship_metric):
    """Read /proc stats and ship them."""
    # --- Memory ---
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])

        total_mb = meminfo.get("MemTotal", 0) / 1024
        avail_mb = meminfo.get("MemAvailable", 0) / 1024
        used_mb = total_mb - avail_mb
        swap_total_mb = meminfo.get("SwapTotal", 0) / 1024
        swap_free_mb = meminfo.get("SwapFree", 0) / 1024
        swap_used_mb = swap_total_mb - swap_free_mb

        ship_metric("sys_memory_total_mb", total_mb)
        ship_metric("sys_memory_used_mb", used_mb)
        ship_metric("sys_memory_available_mb", avail_mb)
        ship_metric(
            "sys_memory_usage_pct", (used_mb / total_mb * 100) if total_mb > 0 else 0
        )
        if swap_total_mb > 0:
            ship_metric("sys_swap_used_mb", swap_used_mb)
    except Exception:
        pass

    # --- CPU ---
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            ship_metric("sys_load_1m", float(parts[0]))
            ship_metric("sys_load_5m", float(parts[1]))
            ship_metric("sys_load_15m", float(parts[2]))
    except Exception:
        pass

    # --- Process counts ---
    try:
        # Count our solver subprocesses
        os.getpid()
        bwrap_count = 0
        claude_count = 0
        codex_count = 0

        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                cmdline_path = f"/proc/{pid_dir}/cmdline"
                with open(cmdline_path, "rb") as f:
                    cmdline = (
                        f.read().decode(errors="replace").replace("\x00", " ").lower()
                    )
                if "bwrap" in cmdline:
                    bwrap_count += 1
                if "claude" in cmdline and (
                    "--print" in cmdline or "stream-json" in cmdline
                ):
                    claude_count += 1
                if "codex" in cmdline and "exec" in cmdline:
                    codex_count += 1
            except (PermissionError, FileNotFoundError, ProcessLookupError):
                continue

        ship_metric("sys_solver_bwrap_count", float(bwrap_count))
        ship_metric("sys_solver_claude_count", float(claude_count))
        ship_metric("sys_solver_codex_count", float(codex_count))
    except Exception:
        pass

    # --- Disk (challenge dir) ---
    try:
        stat = os.statvfs("/home")
        total_gb = (stat.f_blocks * stat.f_frsize) / (1024**3)
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
        ship_metric("sys_disk_total_gb", total_gb)
        ship_metric("sys_disk_free_gb", free_gb)
    except Exception:
        pass
