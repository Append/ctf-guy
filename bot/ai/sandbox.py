#!/usr/bin/env python3
"""Bwrap sandbox helpers shared between Claude Code and Codex solvers."""

import contextlib
import logging
import os
import shutil
import signal
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def kill_process_tree(proc) -> None:
    """Kill a subprocess and all its children via process group.

    Falls back to proc.kill() if pgid lookup fails.
    """
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


# Env vars that must NEVER reach solver subprocesses
_SENSITIVE_VARS = {
    "DISCORD_TOKEN",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    # Note: OPENAI_API_KEY intentionally NOT stripped — Codex CLI needs it to authenticate
    "CTFD_TOKEN",
    "CTFD_SESSION",
    "CTFD_URL",
    "DISCORD_ALERT_WEBHOOK",
    "PICO_USERNAME",
    "PICO_PASSWORD",
    "ALLOWED_USER_IDS",
    "DISCORD_GUILD_ID",
    "VICTORIA_LOGS_URL",
    "VICTORIA_METRICS_URL",
}


def solver_env() -> dict[str, str]:
    """Build a sanitized environment for solver subprocesses.

    Strips secrets (Discord token, API keys, credentials) while keeping
    PATH, HOME, nix store paths, and tool configuration.
    """
    env = {k: v for k, v in os.environ.items() if k not in _SENSITIVE_VARS}
    return env


def create_bwrap_workspace(challenge_path: Path) -> tuple[Path, Path]:
    """Create a bwrap overlay workspace for a challenge.

    Returns (tmpdir, upperdir). Caller must clean up tmpdir when done.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="ctf_bwrap_"))
    workdir = tmpdir / "work"
    upperdir = tmpdir / "upper"
    workdir.mkdir()
    upperdir.mkdir()

    # Copy challenge files — .so files go into libs/ to prevent linker pickup
    (upperdir / "libs").mkdir(exist_ok=True)
    skip_dirs = {"_attempts", ".git", "__pycache__"}
    for f in challenge_path.iterdir():
        if f.name == "_prompt.txt":
            continue
        if f.is_file():
            if f.suffix == ".so" or ".so." in f.name:
                shutil.copy2(f, upperdir / "libs" / f.name)
            else:
                shutil.copy2(f, upperdir / f.name)
        elif f.is_dir() and f.name not in skip_dirs:
            shutil.copytree(f, upperdir / f.name, dirs_exist_ok=True)

    return tmpdir, upperdir


def _get_memory_limit(max_solvers: int | None = None) -> str:
    """Calculate per-solver memory limit based on total RAM and concurrent solver count.

    Formula: (total_ram - 2GB overhead) / max_solvers
    Minimum 1GB per solver. Reads CTF_MAX_SOLVERS env var if max_solvers not specified.
    Also caps based on available memory — if system is already under pressure,
    gives less per solver.
    """
    if max_solvers is None:
        max_solvers = int(os.environ.get("CTF_MAX_SOLVERS", "9"))
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                    divisor = max(1, max_solvers)
                    limit_mb = max(1024, (total_kb // 1024 - 2048) // divisor)
                    return f"{limit_mb}M"
    except Exception:
        pass
    return "4G"  # Fallback


def build_bwrap_cmd(
    challenge_path: Path,
    upperdir: Path,
    inner_cmd: list[str],
    max_solvers: int = 3,
    mcp_config_path: str | None = None,
) -> list[str]:
    """Build a bwrap command that sandboxes inner_cmd with a tmpfs overlay.

    Wraps in systemd-run for cgroup memory limits based on max_solvers.
    """
    # Whitelist filesystem access — solvers get tools but not secrets.
    # Order matters: bwrap applies mounts top-to-bottom, later mounts overlay earlier.
    home = str(Path.home())
    bwrap = [
        "bwrap",
        "--ro-bind",
        "/nix",
        "/nix",  # Nix store (all tools, Python, libs)
        "--ro-bind",
        "/usr",
        "/usr",  # System tools
        "--ro-bind",
        "/bin",
        "/bin",  # Basic binaries
        "--ro-bind",
        "/etc",
        "/etc",  # System config (resolv.conf, etc.)
    ]
    # Bind lib dirs that exist (varies by distro)
    for lib_dir in ["/lib", "/lib64", "/lib32"]:
        if Path(lib_dir).exists():
            bwrap.extend(["--ro-bind", lib_dir, lib_dir])

    # Block home dir FIRST (hides .env, .ssh, .claude, bot source, cookies)
    bwrap.extend(["--tmpfs", home])

    # Then overlay specific home subdirs/files that solvers need (AFTER tmpfs)
    # ~/.local — claude CLI, codex CLI, playwright-cli + their deps
    # ~/.claude — settings, session auth (needed for claude CLI to work)
    # ~/.claude.json — claude CLI config file (in home root, not in .claude/)
    # ~/.nvm — node/npm
    # Read-only: tool binaries and configs
    for subdir in [".local", ".nvm"]:
        path = Path.home() / subdir
        if path.exists():
            bwrap.extend(["--ro-bind", str(path), str(path)])
    # Writable: Claude Code and Codex need to write session state/cache
    for subdir in [".claude", ".codex"]:
        path = Path.home() / subdir
        if path.exists():
            bwrap.extend(["--bind", str(path), str(path)])
    # ~/.claude.json is a file in the home root
    claude_json = Path.home() / ".claude.json"
    if claude_json.exists():
        bwrap.extend(["--ro-bind", str(claude_json), str(claude_json)])
    # MCP config needs to be accessible inside the sandbox
    mcp_config = Path(__file__).parent.parent / "mcp-config.json"
    if mcp_config.exists():
        bwrap.extend(["--ro-bind", str(mcp_config.resolve()), str(mcp_config.resolve())])
    # Docker — solvers need it for challenges that ship Dockerfiles
    docker_sock = Path("/var/run/docker.sock")
    if docker_sock.exists():
        bwrap.extend(["--bind", str(docker_sock), str(docker_sock)])
    # WSL2: docker CLI lives under /mnt/wsl/docker-desktop
    docker_mnt = Path("/mnt/wsl/docker-desktop")
    if docker_mnt.exists():
        bwrap.extend(["--ro-bind", str(docker_mnt), str(docker_mnt)])

    # Challenge dir (writable, overlays home tmpfs)
    bwrap.extend(
        [
            "--bind",
            str(upperdir),
            str(challenge_path),
            "--tmpfs",
            "/tmp",
        ]
    )
    # Temp MCP config must be bound AFTER --tmpfs /tmp (which hides host /tmp)
    if mcp_config_path and Path(mcp_config_path).exists():
        resolved = str(Path(mcp_config_path).resolve())
        if resolved != str(mcp_config.resolve()):
            bwrap.extend(["--ro-bind", resolved, resolved])
    bwrap.extend(
        [
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--chdir",
            str(challenge_path),
            "--die-with-parent",
            "--",
            *inner_cmd,
        ]
    )

    # systemd-run cgroup limits disabled — the resource leak fixes (awaiting
    # cancelled tasks, CancelledError handling) address the root cause.
    # Re-enable if needed on high-memory machines:
    # mem_limit = _get_memory_limit(max_solvers)
    # return [
    #     "systemd-run", "--user", "--scope", "--quiet",
    #     "--expand-environment=no",
    #     "-p", f"MemoryMax={mem_limit}",
    #     "-p", "MemorySwapMax=0",
    #     "--",
    #     *bwrap,
    # ]
    return bwrap


def sync_back_artifacts(
    upperdir: Path,
    challenge_path: Path,
    model_label: str,
) -> bool:
    """Sync bwrap overlay artifacts back.

    If flag found: copies to real challenge dir, returns True.
    If no flag: copies to _attempts/<model>-<N>/, returns False.
    """
    flag_path = upperdir / "flag.txt"
    flag_found = flag_path.exists() and bool(flag_path.read_text().strip())

    skip_names = {"_prompt.txt", "challenge.json", "libs"}

    if flag_found:
        copied = []
        for f in upperdir.iterdir():
            if f.name == "_prompt.txt":
                continue
            dst = challenge_path / f.name
            if f.is_file():
                shutil.copy2(f, dst)
                copied.append(f.name)
            elif f.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(f, dst)
                copied.append(f"{f.name}/")
        if copied:
            log.info(f"bwrap [{model_label}]: flag found, synced back {len(copied)} items")
        return True
    else:
        attempt_num = 1
        while (challenge_path / f"_attempts/{model_label}-{attempt_num}").exists():
            attempt_num += 1
        attempt_dir = challenge_path / f"_attempts/{model_label}-{attempt_num}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for f in upperdir.iterdir():
            if f.name in skip_names:
                continue
            dst = attempt_dir / f.name
            if f.is_file():
                shutil.copy2(f, dst)
                copied.append(f.name)
            elif f.is_dir():
                shutil.copytree(f, dst, dirs_exist_ok=True)
                copied.append(f"{f.name}/")
        if copied:
            log.info(f"bwrap [{model_label}]: no flag, saved {len(copied)} items to {attempt_dir.name}")
        return False
