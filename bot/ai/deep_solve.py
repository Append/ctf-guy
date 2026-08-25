#!/usr/bin/env python3
"""Deep analysis mode — parallel teardown subagents that run before the main solver.

Two subagents analyse the challenge independently:
  - Source Analyst: reads all source files and maps the attack surface
  - Infra Analyst: inspects Docker/compose config and maps the runtime environment

Their findings are merged into _deep_analysis.md which the main solver ingests.
"""

import asyncio
import json
import logging
from pathlib import Path

from ai.claude_code import SolveResult, cleanup_docker_containers, solve_with_claude_code

log = logging.getLogger(__name__)

# Output filenames written by each subagent / merge step
_SOURCE_ANALYSIS = "_source_analysis.md"
_INFRA_ANALYSIS = "_infra_analysis.md"
_DEEP_ANALYSIS = "_deep_analysis.md"

# Timeout (seconds) for each teardown subagent
_TEARDOWN_TIMEOUT = 120


def _list_challenge_files(challenge_dir: Path) -> list[Path]:
    """Return all non-hidden, non-internal files in challenge_dir (recursive)."""
    files = []
    try:
        for p in sorted(challenge_dir.rglob("*")):
            if not p.is_file():
                continue
            # Skip hidden dirs/files and internal artefacts
            if any(part.startswith(".") or part.startswith("_") for part in p.relative_to(challenge_dir).parts):
                continue
            files.append(p)
    except Exception:
        pass
    return files[:50]  # Cap to avoid massive listings


def _build_source_analyst_prompt(challenge_dir: Path) -> str:
    """Build the prompt for the Source Analyst subagent.

    The subagent is expected to read all source files in challenge_dir and write
    a structured analysis to _source_analysis.md.
    """
    files = _list_challenge_files(challenge_dir)

    # Try to load challenge metadata for extra context
    meta_lines: list[str] = []
    challenge_json = challenge_dir / "challenge.json"
    if challenge_json.exists():
        try:
            meta = json.loads(challenge_json.read_text())
            if meta.get("name"):
                meta_lines.append(f"- **Name:** {meta['name']}")
            if meta.get("category"):
                meta_lines.append(f"- **Category:** {meta['category']}")
            if meta.get("description"):
                meta_lines.append(f"- **Description:** {meta['description']}")
            if meta.get("points"):
                meta_lines.append(f"- **Points:** {meta['points']}")
        except Exception:
            pass

    file_list = "\n".join(f"  - {p.relative_to(challenge_dir)}" for p in files) if files else "  (no files found)"

    meta_section = ""
    if meta_lines:
        meta_section = "\n## Challenge Metadata\n" + "\n".join(meta_lines) + "\n"

    return f"""You are the Source Analyst for a CTF challenge. Your job is to read all source \
files and produce a detailed technical analysis that will help a solver find the flag.

## Challenge Directory
`{challenge_dir}`
{meta_section}
## Files to Analyse
{file_list}

## Your Task

1. Read **every file** listed above.
2. Write a structured analysis to `{_SOURCE_ANALYSIS}` in the challenge directory covering:

   ### Endpoints & Routes
   List all HTTP endpoints, RPC methods, or entry points exposed by the application.

   ### Data Flow
   Trace how user-supplied input travels through the application — from entry point to \
storage, processing, or output.

   ### Security Constraints
   Identify all authentication checks, authorisation logic, input validation, and sanitisation \
routines. Note any that look incomplete or bypassable.

   ### Attack Surface
   Summarise the most promising attack vectors ranked by likelihood of yielding the flag.

   ### Interesting Patterns
   Call out any suspicious code: hardcoded secrets, debug backdoors, dangerous function calls \
(e.g. eval, system, sprintf without bounds), insecure deserialization, crypto misuse, etc.

Write the output as a Markdown document. Be concise but precise — the solver will rely on this \
analysis to pick an approach without re-reading all the source.
"""


def _build_infra_analyst_prompt(challenge_dir: Path) -> str:
    """Build the prompt for the Infra Analyst subagent.

    If a Dockerfile or docker-compose.yml is present the subagent builds/starts
    the environment and maps services.  Otherwise it does static analysis of
    config files.
    """
    has_dockerfile = (challenge_dir / "Dockerfile").exists()
    has_compose = (challenge_dir / "docker-compose.yml").exists() or (challenge_dir / "docker-compose.yaml").exists()

    if has_dockerfile or has_compose:
        if has_compose:
            build_cmd = "docker compose up -d --build"
            cleanup_cmd = "docker compose down"
        else:
            build_cmd = "docker build -t challenge-local . && docker run -d --name challenge-local challenge-local"
            cleanup_cmd = "docker rm -f challenge-local"

        return f"""You are the Infra Analyst for a CTF challenge. Your job is to build and \
inspect the challenge's Docker environment, then document the running infrastructure.

## Challenge Directory
`{challenge_dir}`

## Your Task

1. Build and start the environment:
   ```
   {build_cmd}
   ```
2. Inspect the running environment and document the following in `{_INFRA_ANALYSIS}`:

   ### Services & Ports
   List every service, the port(s) it listens on, and the protocol (HTTP, TCP, UDP, etc.).

   ### Configuration
   Capture key environment variables, config files mounted into the container, and any \
secrets or flags baked into the image layers.

   ### Resource Limits & Constraints
   Note any CPU/memory limits, read-only mounts, dropped capabilities, or seccomp profiles.

   ### Network Topology
   Describe how services communicate with each other and which are reachable from outside.

   ### Interesting Observations
   Flag anything unusual: world-writable directories, SUID binaries, cron jobs, startup \
scripts that run as root, etc.

3. When done, stop and remove the containers:
   ```
   {cleanup_cmd}
   ```

Write the output as a Markdown document. Be precise — include actual port numbers, env var \
names, and file paths rather than generalities.
"""
    else:
        # No Docker — static analysis of config files
        config_extensions = {".conf", ".cfg", ".yml", ".yaml", ".ini", ".toml", ".env", ".json"}
        config_files = [
            p
            for p in sorted(challenge_dir.rglob("*"))
            if p.is_file()
            and p.suffix.lower() in config_extensions
            and not p.name.startswith(".")
            and not p.name.startswith("_")
        ]
        file_list = (
            "\n".join(f"  - {p.relative_to(challenge_dir)}" for p in config_files)
            if config_files
            else "  (no config files found)"
        )

        return f"""You are the Infra Analyst for a CTF challenge. No Dockerfile was found, \
so your job is to perform static analysis of any configuration files present.

## Challenge Directory
`{challenge_dir}`

## Config Files Found
{file_list}

## Your Task

1. Read every config file listed above (and any others you discover).
2. Write a structured analysis to `{_INFRA_ANALYSIS}` covering:

   ### Service Configuration
   Document any services, servers, or processes configured — hostnames, ports, protocols.

   ### Secrets & Credentials
   List any hardcoded passwords, API keys, tokens, or flag-shaped strings found in configs.

   ### Security Settings
   Note TLS settings, authentication modes, ACLs, firewall rules, or other security controls.

   ### Interesting Observations
   Anything unusual or exploitable in the configuration.

Write the output as a Markdown document. If no config files exist, write a brief note \
explaining that and any other environmental observations you can make.
"""


def _merge_analyses(
    challenge_dir: Path,
    source_path: Path | None,
    infra_path: Path | None,
) -> Path | None:
    """Merge source and infra analysis files into _deep_analysis.md.

    Returns the path to the merged file, or None if both inputs are missing/empty.
    """
    sections: list[str] = []

    if source_path is not None and source_path.exists():
        content = source_path.read_text().strip()
        if content:
            sections.append(f"# Source Analysis\n\n{content}")

    if infra_path is not None and infra_path.exists():
        content = infra_path.read_text().strip()
        if content:
            sections.append(f"# Infrastructure Analysis\n\n{content}")

    if not sections:
        return None

    merged = "\n\n---\n\n".join(sections) + "\n"
    out_path = challenge_dir / _DEEP_ANALYSIS
    out_path.write_text(merged)
    log.debug("Merged deep analysis written to %s (%d bytes)", out_path, len(merged))
    return out_path


# ---------------------------------------------------------------------------
# Teardown subagent dispatchers
# ---------------------------------------------------------------------------


async def _run_source_analyst(challenge_dir: Path, config) -> Path | None:
    """Launch the Source Analyst subagent and return the output path if non-empty."""
    prompt = _build_source_analyst_prompt(challenge_dir)
    try:
        await solve_with_claude_code(
            thread=None,
            challenge_dir=str(challenge_dir),
            prompt=prompt,
            timeout=_TEARDOWN_TIMEOUT,
            model=config.deep_analysis_model,
            effort="high",
        )
    except Exception:
        log.exception("Source Analyst subagent failed for %s", challenge_dir)
        return None

    out = challenge_dir / _SOURCE_ANALYSIS
    if out.exists() and out.read_text().strip():
        return out
    return None


async def _run_infra_analyst(challenge_dir: Path, config, category: str | None = None) -> Path | None:
    """Launch the Infra Analyst subagent and return the output path if non-empty."""
    prompt = _build_infra_analyst_prompt(challenge_dir)
    try:
        result = await solve_with_claude_code(
            thread=None,
            challenge_dir=str(challenge_dir),
            prompt=prompt,
            timeout=_TEARDOWN_TIMEOUT,
            model=config.deep_analysis_model,
            effort="high",
            category=category,
        )
        # Clean up any Docker containers the subagent started
        if result and result.docker_containers:
            await cleanup_docker_containers(result.docker_containers)
    except Exception:
        log.exception("Infra Analyst subagent failed for %s", challenge_dir)
        return None

    out = challenge_dir / _INFRA_ANALYSIS
    if out.exists() and out.read_text().strip():
        return out
    return None


async def _run_teardown(challenge_dir: Path, config, category: str | None = None) -> Path | None:
    """Dispatch both analyst subagents in parallel and merge their outputs.

    Returns the path to the merged _deep_analysis.md, or None if both analysts
    produce no output.
    """
    source_task = asyncio.create_task(_run_source_analyst(challenge_dir, config))
    infra_task = asyncio.create_task(_run_infra_analyst(challenge_dir, config, category=category))

    source_path, infra_path = await asyncio.gather(source_task, infra_task)

    return _merge_analyses(challenge_dir, source_path, infra_path)


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


async def deep_solve(
    thread,
    challenge_dir: Path,
    prompt: str,
    config,
    solver_timeout: int = 600,
    model=None,
    effort=None,
    subagent_model=None,
    max_budget=None,
    event_callback=None,
    category: str | None = None,
    challenge_id: int | None = None,
    fast: bool = False,
) -> "SolveResult | None":
    """Run deep analysis teardown then launch the main solver.

    1. Posts a status message to the Discord thread (if provided).
    2. Runs both teardown subagents in parallel and merges findings.
    3. Posts the teardown result summary to the thread.
    4. Launches the main solver with the full solver_timeout.
    """
    if thread is not None:
        try:
            await thread.send("Deep Analysis Mode — running teardown subagents...")
        except Exception:
            log.warning("Failed to post teardown start message to thread")

    deep_analysis_path = await _run_teardown(challenge_dir, config, category=category)

    if thread is not None:
        try:
            if deep_analysis_path is not None:
                await thread.send(f"Teardown complete — deep analysis written to `{deep_analysis_path.name}`.")
            else:
                await thread.send("Teardown subagents produced no output — proceeding with direct solve.")
        except Exception:
            log.warning("Failed to post teardown result message to thread")

    # Inject deep analysis into the prompt now (build_solve_prompt ran before
    # teardown, so _deep_analysis.md didn't exist when the prompt was built).
    if deep_analysis_path is not None:
        deep_analysis = deep_analysis_path.read_text()[:8000]
        prompt += (
            "\n\nDEEP ANALYSIS — CHALLENGE TEARDOWN (read carefully before writing ANY exploit code):\n"
            "A thorough analysis of this challenge's source code and infrastructure has been prepared.\n"
            "Read it carefully. Understand the full system before writing any exploit code.\n"
            "Generate hypotheses about the vulnerability class and exfiltration channels.\n"
            "If Docker containers are running from the teardown, use them for local testing.\n\n"
            f"{deep_analysis}\n"
            "--- END DEEP ANALYSIS ---\n"
        )

    return await solve_with_claude_code(
        thread=thread,
        challenge_dir=str(challenge_dir),
        prompt=prompt,
        timeout=solver_timeout,
        model=model,
        effort=effort,
        subagent_model=subagent_model,
        max_budget=max_budget,
        event_callback=event_callback,
        category=category,
        challenge_id=challenge_id,
        fast=fast,
    )
