#!/usr/bin/env python3
"""Sandboxed tool execution engine."""

import asyncio
import base64
import os
from dataclasses import dataclass
from pathlib import Path

from ai.sandbox import solver_env as _solver_env

MAX_OUTPUT = 10_000  # Truncate tool output to this many chars
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120


@dataclass
class ToolResult:
    success: bool
    output: str
    truncated: bool = False


async def execute_tool(
    tool_name: str,
    arguments: dict,
    challenge_dir: str,
) -> ToolResult:
    """Execute a tool call from the AI solver."""
    challenge_path = Path(challenge_dir).resolve()

    handlers = {
        "run_python": _run_python,
        "run_shell": _run_shell,
        "read_file": _read_file,
        "write_file": _write_file,
        "list_files": _list_files,
    }

    handler = handlers.get(tool_name)
    if not handler:
        return ToolResult(success=False, output=f"Unknown tool: {tool_name}")

    try:
        return await handler(arguments, challenge_path)
    except TimeoutError:
        return ToolResult(success=False, output="Execution timed out")
    except Exception as e:
        return ToolResult(success=False, output=f"Error: {type(e).__name__}: {e}")


async def _run_python(args: dict, cwd: Path) -> ToolResult:
    """Execute Python code in the challenge directory."""
    code = args["code"]
    timeout = min(args.get("timeout_seconds", DEFAULT_TIMEOUT), MAX_TIMEOUT)

    # Write code to temp file to avoid shell escaping issues
    tmp_file = cwd / "_solver_tmp.py"
    try:
        tmp_file.write_text(code)
        proc = await asyncio.create_subprocess_exec(
            "python3",
            str(tmp_file),
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**_solver_env(), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode(errors="replace")
        return _truncate_result(proc.returncode == 0, output)
    finally:
        tmp_file.unlink(missing_ok=True)


async def _run_shell(args: dict, cwd: Path) -> ToolResult:
    """Execute a shell command in the challenge directory."""
    command = args["command"]
    timeout = min(args.get("timeout_seconds", DEFAULT_TIMEOUT), MAX_TIMEOUT)

    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_solver_env(),
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    output = stdout.decode(errors="replace")
    return _truncate_result(proc.returncode == 0, output)


async def _read_file(args: dict, cwd: Path) -> ToolResult:
    """Read a file from the challenge directory."""
    rel_path = args["path"]
    encoding = args.get("encoding", "utf-8")

    target = _safe_resolve(cwd, rel_path)
    if target is None:
        return ToolResult(success=False, output="Path traversal blocked")

    if not target.exists():
        return ToolResult(success=False, output=f"File not found: {rel_path}")

    if encoding == "base64":
        content = base64.b64encode(target.read_bytes()).decode()
    else:
        try:
            content = target.read_text()
        except UnicodeDecodeError:
            content = base64.b64encode(target.read_bytes()).decode()
            content = f"[binary file, base64 encoded]\n{content}"

    return _truncate_result(True, content)


async def _write_file(args: dict, cwd: Path) -> ToolResult:
    """Write content to a file in the challenge directory."""
    rel_path = args["path"]
    content = args["content"]

    target = _safe_resolve(cwd, rel_path)
    if target is None:
        return ToolResult(success=False, output="Path traversal blocked")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return ToolResult(
        success=True, output=f"Written {len(content)} bytes to {rel_path}"
    )


async def _list_files(args: dict, cwd: Path) -> ToolResult:
    """List files in the challenge directory."""
    rel_path = args.get("path", ".")
    target = _safe_resolve(cwd, rel_path)
    if target is None:
        return ToolResult(success=False, output="Path traversal blocked")

    if not target.is_dir():
        return ToolResult(success=False, output=f"Not a directory: {rel_path}")

    lines = []
    for p in sorted(target.iterdir()):
        size = p.stat().st_size if p.is_file() else 0
        kind = "dir" if p.is_dir() else "file"
        lines.append(f"{kind:4s}  {size:>8d}  {p.name}")

    return ToolResult(success=True, output="\n".join(lines) or "(empty directory)")


def _safe_resolve(base: Path, rel_path: str) -> Path | None:
    """Resolve a path safely, preventing directory traversal."""
    try:
        target = (base / rel_path).resolve()
        # Ensure the resolved path is under the base directory
        if not str(target).startswith(str(base.resolve())):
            return None
        return target
    except (ValueError, OSError):
        return None


def _truncate_result(success: bool, output: str) -> ToolResult:
    """Truncate output if it exceeds the max length."""
    if len(output) > MAX_OUTPUT:
        return ToolResult(
            success=success,
            output=output[:MAX_OUTPUT]
            + f"\n... (truncated, {len(output)} total chars)",
            truncated=True,
        )
    return ToolResult(success=success, output=output)
