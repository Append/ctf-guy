#!/usr/bin/env python3
"""Shared solve utilities used by both /solve command and auto-solve queue."""

import asyncio
import contextlib
import json
import logging
import os
import re
from pathlib import Path

import discord

from ai.claude_code import _safe_send
from ai.playbooks import load_playbook, normalize_category
from db.challenges import ChallengeRecord, mark_solved
from discord_ui.threads import update_thread_status
from platforms.picoctf import PicoCTFPlatform

log = logging.getLogger(__name__)


async def launch_pico_instance(
    challenge_id: int,
    challenge_dir: str,
    include_hints: bool = False,
) -> str:
    """Launch a picoCTF instance and return the full description with connection info.

    Uses Playwright to click Launch Instance in the browser (httpx POST
    gets 405 on some challenges). Falls back to API-only if Playwright
    isn't available.
    """
    try:
        instance_data = await _launch_via_playwright(challenge_id)

        # Fallback: try API directly if Playwright didn't work
        if not instance_data or not instance_data.get("description"):
            instance_data = await _launch_via_api(challenge_id)

        if not instance_data:
            return ""

        desc_html = instance_data.get("description", "")
        endpoints = instance_data.get("endpoints", [])
        hints = instance_data.get("hints", [])
        status = instance_data.get("status", "unknown")
        expires = instance_data.get("expires_in", 0)

        parts = []
        if desc_html:
            text = re.sub(r"<[^>]+>", "", desc_html)
            text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            parts.append(text.strip())

        urls = re.findall(r'href=["\']([^"\']+)["\']', desc_html)
        if urls:
            parts.append(f"\nDownload/service URLs: {', '.join(urls)}")

        if endpoints:
            parts.append(f"\nService endpoints: {json.dumps(endpoints)}")

        if status:
            parts.append(f"\nInstance status: {status} (expires in {expires}s)")

        if hints and include_hints:
            for i, h in enumerate(hints):
                h_text = re.sub(r"<[^>]+>", "", h) if isinstance(h, str) else str(h)
                parts.append(f"Hint {i+1}: {h_text.strip()}")

        result = "\n".join(parts)

        # Update challenge.json
        meta_path = Path(challenge_dir) / "challenge.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                meta["description"] = result
                meta["instance"] = {
                    "status": status,
                    "expires_in": expires,
                    "endpoints": endpoints,
                    "urls": urls,
                }
                meta_path.write_text(json.dumps(meta, indent=2))
            except Exception:
                pass

        log.info(f"Instance ready: status={status}, endpoints={endpoints}, urls={urls}")
        return result

    except Exception as e:
        log.error(f"Failed to launch picoCTF instance: {e}", exc_info=True)
        return ""


async def try_auto_submit(
    thread: discord.abc.Messageable,
    challenge: ChallengeRecord,
    db_conn,
    allowed_user_ids: set[int],
    config=None,
) -> bool:
    """Check for flag.txt and auto-submit if found. Returns True if flag was correct."""
    if not challenge.challenge_dir:
        return False

    flag_path = Path(challenge.challenge_dir) / "flag.txt"
    if not flag_path.exists():
        return False

    flag = flag_path.read_text().strip()
    if not flag:
        return False

    # Check dedup + throttle
    from ai.flag_tracker import flag_tracker

    if flag_tracker.check_dedup(challenge.ctfd_id, flag):
        await _safe_send(thread, f"Flag `{flag}` already submitted — skipping.")
        return False
    cooldown = flag_tracker.get_cooldown_remaining(challenge.ctfd_id)
    if cooldown > 0:
        await _safe_send(
            thread,
            f"Submission throttled — {cooldown}s cooldown remaining.",
            silent=False,
        )
        return False

    # Detect platform
    meta_path = Path(challenge.challenge_dir) / "challenge.json"
    platform = "ctfd"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            platform = meta.get("platform", "ctfd")
        except Exception:
            pass

    # Validate flag format
    ping = " ".join(f"<@{uid}>" for uid in allowed_user_ids) if allowed_user_ids else ""

    if platform == "picoctf" and not re.match(r"picoCTF\{.+\}", flag):
        # Non-standard flag format — try submitting the raw value anyway
        # (some challenges explicitly say "flag does not include the standard wrapper")
        await _safe_send(
            thread,
            f"Flag `{flag}` doesn't match `picoCTF{{...}}` format — trying raw submission...",
        )
        try:
            pico = PicoCTFPlatform()
            result = await pico.submit_flag(challenge.ctfd_id, flag)
            await pico.close()
            flag_tracker.record(challenge.ctfd_id, flag, result.status)
            from ai.telemetry import ship_log, ship_metric

            ship_metric(
                "ctf_flag_submissions_total",
                1,
                result=result.status,
                challenge=challenge.name,
            )
            ship_log("flag.submit", challenge=challenge.name, result=result.status)

            if result.status == "correct":
                mark_solved(db_conn, challenge.id, flag)
                await _safe_send(thread, f"**CORRECT!** `{flag}` (non-standard format)")
                if isinstance(thread, discord.Thread):
                    await update_thread_status(thread, "solved")
                return True
            elif result.status == "already_solved":
                mark_solved(db_conn, challenge.id, flag)
                await _safe_send(thread, f"Already solved. `{flag}`")
                if isinstance(thread, discord.Thread):
                    await update_thread_status(thread, "solved")
                return True
        except Exception as e:
            log.warning(f"Raw flag submission failed: {e}")

        # Raw submit didn't work either — ping for help
        await _safe_send(
            thread,
            f"{ping} Flag `{flag}` doesn't match expected format and raw submission failed.\n"
            f"Submit manually with `/submit flag:{flag}`.",
            silent=False,
        )
        if isinstance(thread, discord.Thread):
            await update_thread_status(thread, "needs_help")
        return False

    await _safe_send(thread, f"Flag found: `{flag}` — submitting...")

    try:
        if platform == "picoctf":
            pico = PicoCTFPlatform()
            result = await pico.submit_flag(challenge.ctfd_id, flag)
            await pico.close()
        elif platform == "ctfd":
            if not config or not config.ctfd_url or not (config.ctfd_token or config.ctfd_session):
                await _safe_send(
                    thread, f"Flag: `{flag}` (CTFd auto-submit needs CTFD_URL + CTFD_TOKEN or CTFD_SESSION in .env)"
                )
                return False
            from ctfd.client import CTFdClient

            client = CTFdClient(config.ctfd_url, token=config.ctfd_token, session=config.ctfd_session)
            try:
                result = await client.submit_flag(challenge.ctfd_id, flag)
            finally:
                await client.close()
        else:
            await _safe_send(thread, f"Flag: `{flag}` (unsupported platform: {platform})")
            return False

        flag_tracker.record(challenge.ctfd_id, flag, result.status)
        from ai.telemetry import ship_log, ship_metric

        ship_metric(
            "ctf_flag_submissions_total",
            1,
            result=result.status,
            challenge=challenge.name,
        )
        ship_log("flag.submit", challenge=challenge.name, result=result.status)

        if result.status == "correct":
            mark_solved(db_conn, challenge.id, flag)
            await _safe_send(thread, f"**CORRECT!** `{flag}`")
            if isinstance(thread, discord.Thread):
                await update_thread_status(thread, "solved")
            return True
        elif result.status == "already_solved":
            mark_solved(db_conn, challenge.id, flag)
            await _safe_send(thread, f"Already solved. `{flag}`")
            if isinstance(thread, discord.Thread):
                await update_thread_status(thread, "solved")
            return True
        else:
            await _safe_send(thread, f"Submission failed: {result.status} — {result.message}")
            if isinstance(thread, discord.Thread):
                await update_thread_status(thread, "needs_help")
            return False

    except Exception as e:
        log.warning(f"Auto-submit failed: {e}")
        from ai.telemetry import ship_log

        ship_log(
            "flag.submit_error",
            challenge=challenge.name,
            error=str(e)[:300],
            flag_preview=flag[:30],
        )
        await _safe_send(
            thread,
            f"Auto-submit failed: {e}. Flag is in `flag.txt`: `{flag}`",
            silent=False,
        )
        if isinstance(thread, discord.Thread):
            await update_thread_status(thread, "needs_help")
        return False


def build_solve_prompt(
    challenge: ChallengeRecord,
    description: str,
    ctf_root: Path,
    flag_format: str = "kernel{...}",
    approach: str | None = None,
) -> str:
    """Build the full prompt for Claude Code."""
    playbook = load_playbook(challenge.category, ctf_root)
    chall_dir = challenge.challenge_dir or "."

    approach_section = ""
    if approach:
        approach_section = (
            f"\n⚠️ OPERATOR APPROACH TIP (follow this closely):\n"
            f"{approach}\n"
            f"This tip comes from your operator who has already analyzed this challenge. "
            f"Prioritize this approach over your own analysis.\n"
        )

    prompt = (
        f"{playbook}\n\n"
        f"Solve this challenge: {challenge.name} ({challenge.points}pts, {challenge.category})\n"
        f"Flag format: {flag_format} (content is typically an MD5 hash, but may vary — words, numbers, etc.)\n"
        f"Working directory: {chall_dir}\n\n"
        f"Description:\n{description}\n"
        f"\nIMPORTANT: The description above is authoritative and up-to-date. "
        f"Do NOT rely on challenge.json for the description — it may be stale.\n"
        f"{approach_section}"
        f"\n"
        f"FLAG SUBMISSION TOOL:\n"
        f"You can test a flag BEFORE writing flag.txt by running:\n"
        f"  python3 _submit_flag.py 'your_flag_here'\n"
        f"This will submit to the platform and print CORRECT or INCORRECT.\n"
        f"Use this to verify your flag before committing to flag.txt.\n"
        f"Only write flag.txt after getting CORRECT from the submission tool.\n"
        f"\n"
        f"INSTANCE RESTART TOOL:\n"
        f"If your remote instance expires mid-solve, run:\n"
        f"  python3 _restart_instance.py\n"
        f"This will relaunch the instance and print the new connection info.\n"
        f"\n"
        f"REQUIRED DELIVERABLES — save ALL of these in the working directory:\n"
        f"1. solve.py (or solve.sh) — your solve script that reproduces the solution\n"
        f"2. flag.txt — the flag, nothing else\n"
        f"3. README.md — post-mortem writeup written AFTER solving. Include:\n"
        f"   - Challenge name, category, points\n"
        f"   - One-line summary of the challenge\n"
        f"   - Approach taken and why\n"
        f"   - Key insight or trick\n"
        f"   - Tools used\n"
        f"   - Flag\n"
        f"   - Missing tools (if any tool/package was needed but not installed, list it here)\n"
        f"   Do NOT write the README until you have the flag.\n"
        f"\n"
        f"PYTHON LIBRARIES ALREADY INSTALLED (just `import` them, no install needed):\n"
        f"  pwntools, pycryptodome, requests, httpx, sympy, gmpy2, z3-solver,\n"
        f"  Pillow (PIL), numpy, scipy, scikit-learn, scapy, beautifulsoup4,\n"
        f"  capstone, keystone-engine, unicorn, rich, click\n"
        f"Do NOT use pip install, apt install, or nix-shell for these — they're already available.\n"
        f"\n"
        f"For non-Python tools: check with `which <tool>` first.\n"
        f"If a tool isn't installed, use `nix-shell -p <package>` to get it.\n"
        f"Example: `nix-shell -p samba --run 'smbclient //host/share'`\n"
        f"Search for Nix packages at https://search.nixos.org/packages.\n"
        f"Only list tools in README 'Missing tools' if you had to use nix-shell to get them.\n"
        f"\n"
        f"WEB SEARCH: A SearXNG instance is running at http://localhost:8888. Use it to search the web:\n"
        f"  curl -s 'http://localhost:8888/search?q=QUERY&format=json' | jq '.results[:5] | .[] | {{title, url, content}}'\n"
        f"Use it to:\n"
        f"- Search for the TECHNIQUE once you identify the vulnerability (e.g. 'tcache poisoning glibc 2.34')\n"
        f"- Look up tool documentation and examples (e.g. 'pwntools fmtstr_payload')\n"
        f"- Research CVEs, algorithms, or protocol specs relevant to the challenge\n"
        f"- Find similar challenge writeups from other CTFs for the same technique\n"
        f"Search for techniques, not answers. Understand the approach, then implement it.\n"
        f"\n"
        f"EXFILTRATION CALLBACK: Use webhook.site when you need to receive HTTP callbacks (XSS, SSRF, blind exfil):\n"
        f"  1. Create: TOKEN=$(curl -s https://webhook.site/token | jq -r '.uuid')\n"
        f"  2. Use https://webhook.site/$TOKEN as your callback URL in exploits\n"
        f"  3. Poll: curl -s https://webhook.site/token/$TOKEN/requests | jq '.[].query_strings'\n"
        f"\n"
        f"CRITICAL: NEVER download or place .so files (libc.so.6, etc.) in the working directory.\n"
        f"They WILL be picked up by the dynamic linker and BREAK ALL COMMANDS.\n"
        f"A ./libs/ directory already exists — download .so files there: `curl -o libs/libc.so.6 URL`\n"
        f"Reference them by path in exploits: `libc = ELF('./libs/libc.so.6')`\n"
        f"\n"
        f"Keep any downloaded challenge files in this directory.\n"
        f"\n"
        f"CODE SAFETY — mandatory, no exceptions:\n"
        f"- NEVER pipe remote code directly into a shell or interpreter (no `curl | bash`, `wget | python3`, `curl | sh`, etc.).\n"
        f"  Always download first, READ the code, then run it.\n"
        f"- NEVER execute downloaded binaries or scripts without reading them first.\n"
        f"- Only run executables that are: (a) challenge files from the working directory, (b) installed via a package manager (nix, pip, npm), or (c) standard system tools.\n"
        f"- Do NOT install or run random packages/tools from the internet that aren't in the Nix flake or pyproject.toml. Use `nix-shell -p <pkg>` for vetted Nix packages.\n"
        f"\n"
        f"{'=' * 60}\n"
        f"COMPETITION RULES — MANDATORY, VIOLATION = DISQUALIFICATION\n"
        f"{'=' * 60}\n"
        f"These rules are set by the KernelCon CTF organizers. Breaking ANY of them\n"
        f"risks getting our ENTIRE TEAM disqualified. Read carefully.\n"
        f"\n"
        f"1. NO ATTACKING EVENT SYSTEMS. Do not scan, fuzz, enumerate, or DoS the CTF\n"
        f"   infrastructure (scoreboard, API, other challenges, other teams' services).\n"
        f"2. NO BRUTE FORCING. Do not brute-force flags, passwords, or endpoints unless\n"
        f"   the challenge description EXPLICITLY says to. Rate-limited submissions will\n"
        f"   get us flagged.\n"
        f"3. DESIGNATED TARGETS ONLY. Only interact with the specific challenge service\n"
        f"   or files you are given. Do not pivot to other hosts or networks.\n"
        f"   APPROVED CHALLENGE DOMAINS: challenges.kernelcon.org, *.challenges.kernelcon.org\n"
        f"   These are the DESIGNATED challenge servers — you MUST interact with them to solve\n"
        f"   challenges. They are NOT the CTF infrastructure/scoreboard.\n"
        f"4. NO GARBAGE FLAG SUBMISSIONS. Only submit flags you are confident about.\n"
        f"   Never spray-and-pray.\n"
        f"5. INTENDED PATH ONLY. Solve through the intended vulnerability, not by\n"
        f"   breaking the platform or escaping sandboxes.\n"
        f"6. WHEN IN DOUBT, DON'T. If you're unsure whether an approach is allowed,\n"
        f"   choose the less aggressive option.\n"
        f"{'=' * 60}\n"
        f"- Work efficiently. Prioritize the most likely approach first.\n"
        f"- SPEED: When you find the flag, submit it IMMEDIATELY via `python3 _submit_flag.py 'FLAG'` BEFORE writing any deliverables. Writeups and cleanup come AFTER submission.\n"
        f"- If you've tried 3+ approaches without progress, summarize what you've learned and stop.\n"
        f"- You have a limited budget — don't waste turns on unlikely paths.\n"
        f"- Before finishing, clean up temp files (remove _solver_tmp.py, _prompt.txt, etc.).\n"
        f"- If findings.jsonl exists, check it for insights from other solvers before starting.\n"
        f"- If you discover something useful (open port, vulnerability, key insight), append it to findings.jsonl.\n"
        f"- IMPORTANT: If `_live_feedback.md` appears in your working directory, read it immediately and follow the instructions. It contains live corrections from your team lead.\n"
    )
    # approach tip is injected earlier, right after the description

    # Web challenge: instruct solver to use playwright-cli for browser interaction
    from ai.playbooks import WEB_CATEGORIES

    if normalize_category(challenge.category) in WEB_CATEGORIES:
        # Generate a unique session name for browser isolation
        # Includes random component so racers in bwrap (same paths) don't collide
        import hashlib

        session_id = hashlib.md5(f"{challenge.name}-{os.getpid()}-{os.urandom(4).hex()}".encode()).hexdigest()[:8]
        prompt += (
            f"\n\nWEB CHALLENGE — USE PLAYWRIGHT-CLI AS YOUR PRIMARY TOOL:\n"
            f"You MUST use `playwright-cli` for web interaction — it gives you a real browser.\n"
            f"Start every web challenge by opening the target in playwright-cli, NOT curl.\n"
            f"curl is only for simple one-off API requests. For everything else, use the browser.\n"
            f"\n"
            f"Your isolated browser session: `-s={session_id}`\n"
            f"\n"
            f"Standard workflow:\n"
            f"  playwright-cli -s={session_id} open http://target:port/    # open the site\n"
            f"  playwright-cli -s={session_id} snapshot                    # see page structure + ref IDs\n"
            f"  playwright-cli -s={session_id} click ref123                # click elements\n"
            f"  playwright-cli -s={session_id} fill ref456 'input text'    # fill forms\n"
            f"  playwright-cli -s={session_id} goto http://target:port/x   # navigate\n"
            f"  playwright-cli -s={session_id} close                       # when done\n"
            f"\n"
            f"playwright-cli lets you see rendered HTML, execute JS, manage cookies/sessions,\n"
            f"interact with SPAs, and inspect the DOM — things curl cannot do.\n"
        )

    # Rev/Pwn challenge: instruct solver about Ghidra MCP decompiler tools
    from ai.playbooks import GHIDRA_CATEGORIES

    norm_cat = normalize_category(challenge.category)
    if norm_cat in GHIDRA_CATEGORIES and os.environ.get("GHIDRA_MCP_ENABLED", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        prompt += (
            "\n\nGHIDRA DECOMPILATION — MCP TOOLS AVAILABLE:\n"
            "You have Ghidra MCP tools. Use them for decompilation:\n"
            "- `decompile_function` — C pseudocode by name or address\n"
            "- `search_symbols_by_name` — find functions/globals\n"
            "- `search_code` — semantic search across decompiled code\n"
            "- `list_imports` / `list_exports` — binary interfaces\n"
            "- `list_cross_references` — xrefs to/from functions\n"
            "\nPrefer Ghidra decompilation for understanding logic. Use gdb/r2 for dynamic analysis.\n"
        )

    # Inject previous attempt context if exists
    chall_path = Path(chall_dir)
    progress_file = chall_path / "progress.md"
    if progress_file.exists():
        progress = progress_file.read_text()[-3000:]  # Last 3000 chars
        prompt += (
            f"\n\nPREVIOUS ATTEMPT CONTEXT:\n"
            f"This challenge was attempted before. Here's what was tried:\n"
            f"{progress}\n"
            f"--- END PREVIOUS CONTEXT ---\n"
            f"Build on what worked. Avoid repeating what failed.\n"
        )

    # Inject operator hints if any
    hints_file = chall_path / "operator_hints.txt"
    if hints_file.exists():
        hints = hints_file.read_text()[-2000:]
        prompt += f"\n\nOPERATOR HINTS (from your team — follow these):\n" f"{hints}\n"

    # Inject prior analysis advice if available
    analysis_file = chall_path / "_prior_analysis.md"
    if analysis_file.exists():
        analysis = analysis_file.read_text()[-2000:]
        prompt += f"\n\nPRIOR ATTEMPT ANALYSIS (from your team lead — follow this guidance):\n" f"{analysis}\n"

    # Inject attack graph from prior attempts if available
    attack_graph_file = chall_path / "_attack_graph.md"
    if attack_graph_file.exists():
        attack_graph = attack_graph_file.read_text()[-3000:]
        prompt += (
            f"\n\nATTACK GRAPH FROM PRIOR ATTEMPTS (shows what was tried and unexplored paths):\n"
            f"```mermaid\n{attack_graph}\n```\n"
        )

    # Inject deep analysis teardown if available
    deep_analysis_file = chall_path / "_deep_analysis.md"
    if deep_analysis_file.exists():
        deep_analysis = deep_analysis_file.read_text()[:8000]
        prompt += (
            f"\n\nDEEP ANALYSIS — CHALLENGE TEARDOWN (read carefully before writing ANY exploit code):\n"
            f"A thorough analysis of this challenge's source code and infrastructure has been prepared.\n"
            f"Read it carefully. Understand the full system before writing any exploit code.\n"
            f"Generate hypotheses about the vulnerability class and exfiltration channels.\n"
            f"If Docker containers are running from the teardown, use them for local testing.\n\n"
            f"{deep_analysis}\n"
            f"--- END DEEP ANALYSIS ---\n"
        )

    # Inject learned patterns from same category
    from ai.learner import get_patterns_context

    patterns = get_patterns_context(challenge.category, ctf_root)
    if patterns:
        prompt += patterns

    # Inject context from earlier parts if this is a series challenge
    series_context = _get_series_context(challenge.name, ctf_root)
    if series_context:
        prompt += series_context

    return prompt


def detect_series(name: str) -> tuple[str, int] | None:
    """Detect if a challenge is part of a numbered series.

    Returns (base_name, part_number) or None.

    Examples:
        "DISKO 1" → ("DISKO", 1)
        "PIE TIME 2" → ("PIE TIME", 2)
        "Binary Gauntlet 0" → ("Binary Gauntlet", 0)
        "PW Crack 3" → ("PW Crack", 3)
        "Disk, disk, sleuth! II" → ("Disk, disk, sleuth!", 2)
    """
    # Trailing number: "DISKO 1", "buffer overflow 2", "PW Crack 3"
    m = re.match(r"^(.+?)\s+(\d+)$", name)
    if m:
        return (m.group(1).strip(), int(m.group(2)))

    # "Part N" suffix — with or without parentheses
    m = re.match(r"^(.+?)\s*\(?(?:Part|part|Pt\.?)\s*(\d+)\)?$", name)
    if m:
        return (m.group(1).strip().rstrip("(").strip(), int(m.group(2)))

    # Roman numerals: "Disk, disk, sleuth! II"
    roman_map = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
    m = re.match(r"^(.+?)\s+(I{1,3}V?|IV|V)$", name)
    if m and m.group(2) in roman_map:
        return (m.group(1).strip(), roman_map[m.group(2)])

    return None


def _get_series_context(challenge_name: str, ctf_root: Path) -> str:
    """If this challenge is part of a series, find earlier parts' READMEs and return context."""
    series = detect_series(challenge_name)
    if not series:
        return ""

    base_name, part_num = series
    if part_num <= 0:
        return ""

    context_parts = []
    challenges_dir = ctf_root / "challenges"

    # Search for earlier parts across all category directories
    for earlier_num in range(0, part_num):
        earlier_dir = _find_series_challenge_dir(base_name, earlier_num, challenges_dir)
        if not earlier_dir:
            continue

        readme_path = earlier_dir / "README.md"
        flag_path = earlier_dir / "flag.txt"
        if readme_path.exists():
            readme = readme_path.read_text()[:2000]
            context_parts.append(
                f"\n--- CONTEXT FROM PART {earlier_num} ({earlier_dir.name}) ---\n"
                f"{readme}\n"
                f"--- END PART {earlier_num} CONTEXT ---"
            )
        elif flag_path.exists():
            # No README but has flag — at least note it was solved
            flag = flag_path.read_text().strip()
            context_parts.append(
                f"\nPart {earlier_num} was solved (flag: {flag}). "
                f"Check {earlier_dir} for the solve script if you need the approach."
            )

    if not context_parts:
        return ""

    return (
        "\n\nSERIES CHALLENGE CONTEXT:\n"
        f'This is part {part_num} of the "{base_name}" series. '
        f"Earlier parts have been attempted/solved. Use their approach as a starting point — "
        f"later parts typically build on the same concept with increased difficulty.\n"
        + "\n".join(context_parts)
        + "\n"
    )


def _find_series_challenge_dir(base_name: str, part_num: int, challenges_dir: Path) -> Path | None:
    """Find the challenge directory for a series part by searching all category dirs."""
    if not challenges_dir.exists():
        return None

    from discord_ui.threads import slugify

    possible_names = [
        slugify(f"{base_name} {part_num}"),
        slugify(f"{base_name}{part_num}"),
        slugify(f"{base_name}-{part_num}"),
    ]
    # For part 1 (or 0), also check the unnumbered base name
    # e.g. "PIE TIME" might be in "pie-time/" not "pie-time-1/"
    if part_num <= 1:
        possible_names.append(slugify(base_name))

    for cat_dir in challenges_dir.iterdir():
        if not cat_dir.is_dir():
            continue
        for name in possible_names:
            candidate = cat_dir / name
            if candidate.is_dir():
                return candidate

    return None


async def _launch_via_playwright(challenge_id: int) -> dict | None:
    """Launch instance using Python Playwright with saved cookies.

    Each call creates its own browser context so launches can run in parallel.
    """
    try:
        from playwright.async_api import async_playwright


        cookies_file = Path(__file__).parent.parent / "data" / "pico_cookies.json"
        if not cookies_file.exists():
            log.warning("No picoCTF cookies — can't launch instance via browser")
            return None

        raw_cookies = json.loads(cookies_file.read_text())

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--ozone-platform=x11",  # Force X11 — Wayland under WSLg hides the window
                ],
            )
            try:
                context = await browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
                await context.add_cookies(raw_cookies)

                page = await context.new_page()
                await page.goto(f"https://play.picoctf.org/practice/challenge/{challenge_id}")

                await page.wait_for_selector("dialog, [role='dialog']", timeout=15000)
                await asyncio.sleep(1)

                launch_btn = await page.query_selector("button:has-text('Launch Instance')")
                if launch_btn:
                    log.info(f"Clicking Launch Instance for challenge {challenge_id}")
                    await launch_btn.click()
                    await asyncio.sleep(5)

                instance_data = await page.evaluate(
                    f"fetch('/api/challenges/{challenge_id}/instance/').then(r => r.ok ? r.json() : null)"
                )

                if instance_data and instance_data.get("status") != "RUNNING":
                    await asyncio.sleep(5)
                    instance_data = await page.evaluate(
                        f"fetch('/api/challenges/{challenge_id}/instance/').then(r => r.ok ? r.json() : null)"
                    )

                # Save fresh cookies
                fresh_cookies = await context.cookies()
                cookies_file.write_text(json.dumps(fresh_cookies, indent=2))

                if instance_data:
                    log.info(
                        f"Playwright launch: status={instance_data.get('status')}, "
                        f"desc_len={len(instance_data.get('description', ''))}"
                    )
                return instance_data
            finally:
                await browser.close()

    except Exception as e:
        log.warning(f"Playwright launch failed: {e}")
        return None


async def _launch_via_api(challenge_id: int) -> dict | None:
    """Fallback: try launching via httpx API calls."""
    try:
        platform = PicoCTFPlatform()
        await platform._ensure_session()

        log.info(f"API launch for challenge {challenge_id}...")
        instance_data = await platform._launch_instance(challenge_id)

        # Poll with backoff
        for attempt, delay in enumerate([3, 6, 12], 1):
            desc = instance_data.get("description", "") if instance_data else ""

            if desc:  # Accept any description, even if NOT_RUNNING
                break

            log.info(f"API: waiting {delay}s (attempt {attempt}/3)...")
            await asyncio.sleep(delay)

            resp = await platform._api_client.get(f"/api/challenges/{challenge_id}/instance/")
            if resp.status_code == 200:
                instance_data = resp.json()

        await platform.close()

        if instance_data:
            log.info(
                f"API launch: status={instance_data.get('status')}, "
                f"desc_len={len(instance_data.get('description', ''))}"
            )
        return instance_data

    except Exception as e:
        log.warning(f"API launch failed: {e}")
        return None


async def check_missing_tools(
    challenge_dir: str,
    thread: discord.abc.Messageable | None = None,
    allowed_user_ids: set[int] | None = None,
) -> list[str]:
    """Check README.md for missing tools after a solve attempt.

    Returns list of missing tool names. Posts to thread if any found.
    Also appends to a central missing_tools.json for flake/devcontainer updates.
    """
    readme_path = Path(challenge_dir) / "README.md"
    if not readme_path.exists():
        return []

    readme = readme_path.read_text()

    # Look for "Missing tools" section
    missing = []
    in_section = False
    for line in readme.split("\n"):
        lower = line.lower().strip()
        if "missing tool" in lower:
            in_section = True
            continue
        if in_section:
            if line.startswith("#") or (line.strip() == "" and missing):
                break
            cleaned = line.strip().lstrip("- *").strip().rstrip(".")
            skip_words = (
                "none",
                "n/a",
                "no",
                "all tools available",
                "no missing tools",
                "all available",
                "nothing",
                "none needed",
                "none required",
            )
            if cleaned and cleaned.lower() not in skip_words and not cleaned.lower().startswith("none"):
                missing.append(cleaned)

    if not missing:
        return []

    # Verify tools are actually missing from PATH (solvers hallucinate)
    import shutil

    actually_missing = []
    for tool in missing:
        # Extract the tool name (first word, strip backticks/parens)
        tool_name = tool.split()[0].strip("`'\"()").rstrip(",;:")
        if tool_name and not shutil.which(tool_name):
            actually_missing.append(tool_name)

    if not actually_missing:
        log.info(f"Solver claimed missing tools in {challenge_dir} but all found in PATH: {missing}")
        return []

    log.info(f"Missing tools in {challenge_dir}: {actually_missing}")

    # Append to central tracking file
    tools_file = Path(challenge_dir).parent.parent / "missing_tools.json"
    existing = []
    if tools_file.exists():
        with contextlib.suppress(Exception):
            existing = json.loads(tools_file.read_text())
    existing.append(
        {
            "challenge_dir": str(challenge_dir),
            "tools": actually_missing,
        }
    )
    tools_file.write_text(json.dumps(existing, indent=2))

    # Ping users in thread
    if thread and actually_missing:
        ping = ""
        if allowed_user_ids:
            ping = " ".join(f"<@{uid}>" for uid in allowed_user_ids) + " "
        tools_list = ", ".join(f"`{t}`" for t in actually_missing)
        await _safe_send(
            thread,
            f"{ping}Solver needs tools not currently installed: {tools_list}\n"
            f"Add to `flake.nix` or `.devcontainer/Dockerfile` and re-run.",
            silent=False,
        )

    return actually_missing


def write_submit_script(
    challenge_dir: str,
    challenge_id: int,
    platform: str,
    submit_url: str = "http://localhost:8080",
    solver_id: str = "",
    token: str = "",
) -> None:
    """Write a _submit_flag.py script to the challenge directory.

    Claude Code can call this to test flag candidates mid-solve.
    Uses the bot's HTTP /submit endpoint — no direct cookie access needed.
    """
    chall_path = Path(challenge_dir)
    script_path = chall_path / "_submit_flag.py"

    script = f'''#!/usr/bin/env python3
"""Submit a flag. Usage: python3 _submit_flag.py 'FLAG_HERE'"""
import sys, json
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 _submit_flag.py 'FLAG_HERE'")
        sys.exit(1)

    flag = sys.argv[1]
    challenge_id = {challenge_id}
    platform = "{platform}"
    submit_url = "{submit_url}/submit"

    import urllib.request
    body = json.dumps({{"challenge_id": challenge_id, "flag": flag, "platform": platform, "solver_id": "{solver_id}"}}).encode()
    headers = {{"Content-Type": "application/json"}}
    if "{token}":
        headers["Authorization"] = "Bearer " + "{token}"
    req = urllib.request.Request(submit_url, data=body, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"Submit error: {{e}}")
        sys.exit(1)

    status = data.get("status", "unknown")
    if status in ("correct", "already_solved"):
        print(f"CORRECT: {{flag}}")
        (Path(__file__).parent / "flag.txt").write_text(flag + "\\n")
    else:
        print(f"INCORRECT: {{flag}} ({{data.get('message', status)}})")
    sys.exit(0 if status in ("correct", "already_solved") else 1)

if __name__ == "__main__":
    main()
'''

    script_path.write_text(script)
    script_path.chmod(0o755)


def write_restart_script(
    challenge_dir: str,
    challenge_id: int,
    restart_url: str = "http://localhost:8080",
    token: str = "",
) -> None:
    """Write a _restart_instance.py script to the challenge directory.

    Solvers call this when a picoCTF instance expires mid-solve.
    """
    chall_path = Path(challenge_dir)
    script_path = chall_path / "_restart_instance.py"

    script = f'''#!/usr/bin/env python3
"""Restart a picoCTF instance. Usage: python3 _restart_instance.py"""
import json, sys

def main():
    challenge_id = {challenge_id}
    challenge_dir = "{challenge_dir}"
    url = "{restart_url}/restart-instance"

    import urllib.request
    body = json.dumps({{"challenge_id": challenge_id, "challenge_dir": challenge_dir}}).encode()
    headers = {{"Content-Type": "application/json"}}
    if "{token}":
        headers["Authorization"] = "Bearer " + "{token}"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"Restart error: {{e}}")
        sys.exit(1)

    if data.get("status") == "ok":
        print("Instance restarted successfully.")
        desc = data.get("description", "")
        if desc:
            print(f"New description:\\n{{desc}}")
    else:
        print(f"Restart failed: {{data.get('message', data.get('error', 'unknown'))}}")
        sys.exit(1)

if __name__ == "__main__":
    main()
'''

    script_path.write_text(script)
    script_path.chmod(0o755)


def write_progress_file(
    challenge_dir: str,
    challenge_name: str,
    output: str,
    cost_usd: float = 0.0,
    num_turns: int = 0,
    duration_ms: int = 0,
    flag_found: bool = False,
) -> None:
    """Write/append a progress.md with solve attempt details.

    Accumulates across retries so subsequent attempts have full context.
    """
    from datetime import datetime

    chall_path = Path(challenge_dir)
    progress_file = chall_path / "progress.md"

    entry = (
        f"\n## Attempt — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"- **Cost:** ${cost_usd:.4f}\n"
        f"- **Turns:** {num_turns}\n"
        f"- **Duration:** {duration_ms / 1000:.0f}s\n"
        f"- **Status:** {'FLAG FOUND' if flag_found else 'NO FLAG'}\n"
    )

    # Include output summary (first 1500 chars)
    if output:
        summary = output[:1500]
        if len(output) > 1500:
            summary += "\n... (truncated)"
        entry += f"\n### Output\n```\n{summary}\n```\n"

    # Append to existing file
    if progress_file.exists():
        existing = progress_file.read_text()
        progress_file.write_text(existing + entry)
    else:
        header = f"# Progress: {challenge_name}\n"
        progress_file.write_text(header + entry)

    log.info(f"Progress file updated: {progress_file}")


def detect_flag_format(challenge_dir: str | None) -> str:
    """Detect flag format from challenge.json."""
    if not challenge_dir:
        return "kernel{...}"
    meta_path = Path(challenge_dir) / "challenge.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            if meta.get("platform") == "picoctf":
                return "picoCTF{...}"
        except Exception:
            pass
    return "kernel{...}"


def detect_platform(challenge_dir: str | None) -> str:
    """Detect platform from challenge.json."""
    if not challenge_dir:
        return "ctfd"
    meta_path = Path(challenge_dir) / "challenge.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            return meta.get("platform", "ctfd")
        except Exception:
            pass
    return "ctfd"
