# CTF Guy

Semi-automated CTF challenge solver built for speed. Discord bot that orchestrates AI agents (Claude Code, Codex) to solve Jeopardy-style CTF challenges in parallel.

## Project Structure

```
ctf-guy/
├── flake.nix                          # Nix dev environment with all CTF tools
├── .envrc                             # direnv auto-loads the flake
├── docker-compose.telemetry.yml       # VictoriaMetrics + VictoriaLogs + Grafana
├── deploy/grafana/                    # Provisioned dashboards + alerting
├── bot/                               # Discord bot
│   ├── run.py                         # Entry point, file server, telemetry init
│   ├── config.py                      # All env var configuration
│   ├── commands/                      # Slash commands
│   │   ├── scout.py, solve.py, submit.py  # Core workflow
│   │   ├── autosolve.py               # Queue-based batch solving
│   │   ├── ask.py, hint.py            # LLM-assisted help
│   │   ├── interact.py                # Interactive challenge sessions
│   │   ├── learn.py, status.py        # Pattern learning, queue status
│   ├── ai/                            # Solver orchestration
│   │   ├── solver.py                  # Base solver abstraction
│   │   ├── claude_code.py             # Claude Code CLI wrapper + streaming
│   │   ├── codex_solver.py            # Codex CLI wrapper + streaming
│   │   ├── models.py                  # Model configuration + selection
│   │   ├── race.py                    # Multi-model racing coordinator
│   │   ├── queue.py                   # Concurrent auto-solve queue + dashboard
│   │   ├── manager.py                 # Intelligent solve manager (heuristic detectors)
│   │   ├── manager_feed.py            # Event ring buffer for manager
│   │   ├── tools.py                   # Tool definitions for solvers
│   │   ├── telemetry.py               # VictoriaMetrics/Logs exporter
│   │   ├── tracer.py                  # Per-challenge JSONL trace logging
│   │   ├── learner.py                 # Pattern extraction from solved challenges
│   │   ├── advisor.py                 # Retry advice from prior solve attempts
│   │   ├── dependency.py              # Challenge series dependency detection
│   │   ├── sandbox.py                 # bwrap isolation, env sanitization, artifact sync
│   │   ├── sysmon.py                  # System performance monitoring (CPU/mem/processes)
│   │   ├── openrouter.py               # Shared OpenRouter client (rate-limit retry + backoff)
│   │   ├── flag_events.py             # Flag confirmation event registry (FlagResult with solver identity)
│   │   ├── flag_tracker.py            # Flag submission dedup + cooldown tracking
│   │   ├── trace_analyzer.py          # Post-solve failure analysis via LLM
│   │   ├── executor.py                # Sandboxed tool execution (Python/shell)
│   │   ├── playbooks.py               # Category-specific solver playbooks
│   │   ├── solve_utils.py             # Prompt building, flag submission, utilities
│   │   ├── deep_solve.py              # Deep analysis mode (teardown subagents + merge)
│   │   └── attack_graph.py            # Post-solve attack graph generation (tool + approach layers)
│   ├── hooks/                         # Claude Code / Codex hooks
│   │   └── check_feedback.sh          # PostToolUse hook for live manager feedback
│   ├── tools/                         # CLI tools
│   │   └── logs                       # VictoriaLogs query tool
│   ├── mcp-config.json                # MCP servers (SearXNG, Context7)
│   ├── ctfd/                           # CTFd API client (client.py, types.py)
│   ├── events/                        # Discord event/message handling
│   ├── platforms/                     # CTF platform adapters (CTFd, picoCTF)
│   ├── discord_ui/                    # Embeds, thread management
│   ├── db/                            # SQLite challenge/CTF tracking
│   ├── data/                          # Runtime data (SQLite DB, cookies)
│   └── tests/                         # Unit tests (pytest)
├── challenges/                        # Challenge files organized by category
├── solvers/patterns/                  # Learned solve patterns per category
├── solvers/agents/                    # Per-category solver prompt templates (crypto.md, rev.md, etc.)
├── templates/                         # Solver prompt templates
├── deploy/searxng/                    # SearXNG search engine config
└── .claude/rules/soul.md              # Operating personality
```

## Workflow

### 1. Scout
`/scout url:https://play.picoctf.org event:"picoCTF 2026" race:True`

- Fetches challenges from CTFd or picoCTF API
- Filters by event, category, skips already-solved
- Creates Discord forum channels per category with challenge threads
- Optionally kicks off autosolve or multi-model racing

### 2. Solve
Challenges are solved via:
- **`/solve`** — single solver in a challenge thread
- **`/solve deep:True`** — deep analysis mode (teardown + solve)
- **`/scout autosolve:True`** — queue-based concurrent solving
- **`/scout race:True`** — race haiku vs opus vs codex per challenge
- **`/scout autosolve:True deep:True`** — queue with deep analysis per challenge

Each solver runs as a Claude Code or Codex subprocess with:
- Isolated tmpdir workspace (race mode)
- Live event streaming to telemetry + manager feed
- PostToolUse hook for mid-solve manager corrections
- Auto flag submission on completion
- Flag-aware timeout: on correct flag via `_submit_flag.py` → `/submit`, the hard timeout switches to a 120s grace period for deliverables (`flag_events.py`)

**Deep Analysis Mode:** For hard challenges with significant source or infrastructure:
1. **Teardown phase** — parallel Source Analyst + Infra Analyst subagents map the challenge
2. **Solve phase** — main solver receives merged `_deep_analysis.md` with full context
3. Falls back to normal solve if teardown produces nothing
Config: `DEEP_ANALYSIS_MODEL` env var (default: `haiku`) controls teardown model.

### 3. Manager
The intelligent manager monitors each solver's reasoning in real-time:
- **ManagerFeed** ring buffer receives tool calls, text, results via callback
- **Heuristic detectors** (loop, category drift, file neglect, stall, rabbit hole) run every 30s
- **RabbitHoleDetector** classifies Bash calls by base command (r2, strings, gdb) — diverse recon is OK, same command repeated 8+ times triggers
- **Escalation tiers**: Tier 1 (suggest alternative) → Tier 2 (must differ from prior advice) → Tier 3 (hard pivot, step-by-step new strategy)
- Prior corrections fed into LLM context so it doesn't repeat the same advice
- Correction written to `_live_feedback.md` → PostToolUse hook injects it into agent context
- **Corrections toggle**: disable non-security detectors per-solve or globally
  - `/solve manager:false` — disable for this solve (overrides env)
  - `/scout autosolve:True manager:false` or `/scout race:True manager:false` — disable for all queued solves
  - `MANAGER_CORRECTIONS=false` in .env — global default (commands override)
  - Security detectors (prompt injection, reverse shells, exfil) **always run** regardless of toggle
- **Two-tier advice**: detection uses heuristics, correction generation uses `MANAGER_ADVICE_MODEL` (defaults to `TRIAGE_MODEL`) for domain-aware advice
  - Set `MANAGER_ADVICE_MODEL=google/gemini-2.5-pro` for stronger corrections on complex challenges
  - Empty = fall back to `TRIAGE_MODEL` (Gemini Flash)
- **Category-aware stall**: pwn/rev get 180s stall threshold (vs 90s default) — long thinking pauses are normal for exploit development. Thresholds in `playbooks.py:STALL_THRESHOLDS`
- **`MANAGER_MAX_INTERVENTIONS=10`** — max corrections per solve (0 = fully disabled including security)

### 4. Observe
`docker compose -f docker-compose.telemetry.yml up -d`

- **Grafana** on `:3000` — Race View, Agent Deep Dive, Queue Operations dashboards
- **VictoriaMetrics** on `:8428` — solve cost, duration, tool calls, queue depth
- **VictoriaLogs** on `:9428` — live agent thinking stream, tool calls, manager interventions
- **Discord alerts** — flag found, all solvers failed, high cost

### 5. File Sharing
Challenge files served on startup via aiohttp (port `FILE_SERVER_PORT`, default 8080).
Uses tailscale hostname auto-detection (same as `TTYD_HOST`).
Links included in challenge thread embeds.

### 6. Development
- Initial setup: see `SETUP.md` (system prereqs) and `QUICKSTART.md` (competition workflow)
- Run bot: `cd bot && uv run python3 run.py`
- Run tests: `cd bot && uv run python3 -m pytest tests/ -x -q` (dev deps installed via `uv sync --extra dev`)
- Query solve logs: `bot/tools/logs <challenge>` or `bot/tools/logs --recent`
- All solver subprocesses MUST use `solver_env()` from `sandbox.py` — never raw `os.environ`
- All solver subprocesses MUST use `start_new_session=True` + `kill_process_tree()` for cleanup
- bwrap uses `--bind / /` (full host FS access) — this is a known limitation, not real isolation
- MCP servers configured in `bot/mcp-config.json` — passed via `--mcp-config` to Claude Code CLI
- bwrap mount ordering matters: `--tmpfs /tmp` hides earlier bind mounts — bind files in `/tmp` AFTER the tmpfs
- Nix flake sets `PYTHONPATH` with Python 3.13 packages — use `env -u PYTHONPATH` for subprocesses needing venv-only (3.12) packages
- Adding fields to `Config` dataclass requires updating `tests/conftest.py:mock_config` fixture too
- All Discord sends in `claude_code.py` MUST use `_safe_send(thread, ...)` — never bare `thread.send()`. Teardown subagents pass `thread=None`.
- bwrap workspace creation skips `_attempts/`, `.git/`, `__pycache__/` — adding large dirs to challenge paths requires updating `sandbox.py:create_bwrap_workspace` skip list
- `/solve` command has 4 dispatch paths (deep, race, normal, codex) — new parameters must be wired through ALL of them
- `build_solve_prompt()` runs before solver dispatch — `deep_solve()` injects analysis into the prompt itself after teardown, the `build_solve_prompt()` injection only catches retries
- Playwright on WSLg: use `--ozone-platform=x11` arg to force X11 (Wayland hides the window)
- Discord does NOT render Mermaid — always render to PNG via `mmdc` and post as `discord.File`. `_safe_send` passes `**kwargs` to `channel.send()` so `file=` works.
- Nix flake changes (new packages) require `direnv reload` or a new shell — binaries won't be on PATH until then
- Race mode runs each solver via `_run_racer()` → `_run_bwrap()` in `race.py` — per-solver features (e.g., ToolCallCollector) must be wired through this path separately from `/solve`
- Race mode uses flag event sentinel in `asyncio.wait` for instant winner detection — on correct flag, losers are cancelled immediately and the race returns without blocking. Winner's `_process_stream` handles its own grace period in background.
- Flag detection is callback-driven via `flag_events.FlagResult` — the `/submit` handler stores flag value + solver_id. Race identifies the actual winner by `FlagResult.solver_id`, not task completion order. `flag.txt` is a fallback only for non-race single-solver mode.
- Each racer gets a per-solver `_submit_flag.py` in its bwrap overlay with the racer's `solver_id` baked in. `_run_bwrap` accepts `solver_id` param and regex-patches the script in the overlay upperdir.
- All `flag_events.notify()` calls MUST include `flag=` and `solver_id=` — bare `notify(challenge_id)` is deprecated.
- `_process_stream` and `solve_with_codex` never call `unregister()` — the caller (queue `_solve_challenge` or `/solve` command) reads `get_result()` after the solver returns, then unregisters. This prevents the FlagResult from being cleared before the caller can read it.
- `_process_stream` and `solve_with_codex` accept `challenge_id` for flag-aware timeout — all callers must thread `challenge.ctfd_id` through. Teardown subagents intentionally don't get `challenge_id`.
- Solver progress no longer posts to Discord — reasoning streams to telemetry/Grafana only. Discord threads only get dispatch, timeout, flag, and error messages.
- All OpenRouter LLM calls (manager, advisor, trace_analyzer, attack_graph, dependency) MUST use `OpenRouterClient` from `openrouter.py` — never raw `openai.AsyncOpenAI`. Provides rate-limit retry with X-RateLimit-Reset parsing and per-model concurrency limiting.
- `solver.py` still uses its own `openai.AsyncOpenAI` with internal semaphore — don't migrate without reconciling the two concurrency mechanisms.
- Use `from __future__ import annotations` + `TYPE_CHECKING` for cross-module type annotations that would create circular imports (e.g., `claude_code.py` ↔ `attack_graph.py`)
- All category-specific behavior (normalization, behavioral sets, tool allowlists, embed colors) lives in `bot/ai/playbooks.py` — see `docs/competition-categories.md` for the full map
- Use `normalize_category()` from `playbooks.py` for category comparisons — never hardcode normalize dicts inline
- `event_callback` in `claude_code.py` pushes raw `tool_input` dicts (not strings) to the manager feed — detectors must handle both `dict` and `str` args via `isinstance` check
- `BruteForceDetector` skips `pwn` category — iterating on exploit scripts is normal workflow, not brute force
- Open files in Windows viewer from WSL: `open <path>` (exit code 4 is normal)
- `handle_submit` in `run.py` supports both `platform="picoctf"` and `platform="ctfd"` — CTFd requires `CTFD_URL` + `CTFD_TOKEN` in .env
- `try_auto_submit` accepts optional `config` param for CTFd credentials — callers in `solve.py` and `queue.py` pass `self.bot.config`
- Secret scanning is scoped by `.titusignore` (`.github/workflows/secret-scan.yml`). Solver output — `challenges/`, `archive/`, `solvers/patterns/`, `findings.jsonl` — is deliberately excluded: it is expected to contain exploit code, challenge credentials, and flags, and scanning it produced 1104 findings vs 3 for the authored tree. `deploy/` and `scripts/` stay in scope; both real secrets found during release prep lived there, not in `bot/`. `titus --ignore` REPLACES its built-in defaults, so build/vendor noise must be listed in `.titusignore` explicitly.
- Local scan: `titus scan . --ignore .titusignore --validate`. Expect 3 findings from `bot/.env` (real, gitignored, cannot leak). CI sees 0 — `.env` is never checked out.
- The challenge file server binds `FILE_SERVER_BIND` (default `127.0.0.1`) and requires a per-run bearer token on EVERY route including static files. Token is generated in `run.py`, exposed as `bot.file_server_token`, logged at startup. Solver scripts get it via `write_submit_script(token=...)`/`write_restart_script(token=...)`; Discord file links get `?t=<token>`. New callers must thread it through or requests 401.
- `show_index=False` on the static route is deliberate — `challenges/` holds `flag.txt` and solve artifacts.
- Authorization fails closed: `Config.from_env()` raises unless `ALLOWED_USER_IDS` is set or `ALLOW_ALL_USERS=true`. All cogs call `config.is_user_allowed()` — never reimplement the check inline.
- `normalize_category()` folds `-`/`_` to spaces before the `CATEGORY_MAP` lookup. Before this, hyphen-slugified directory names (`web-exploitation`) silently normalized to `misc`, which forked the pattern corpus into parallel spaced/hyphenated files.
- `learner.py` writes pattern files under `normalize_category(category)` — canonical names only (`crypto/forensics/misc/pwn/rev/web`). Don't reintroduce raw-category filenames.
- `pyghidra-mcp` is an optional extra (`uv sync --extra ghidra`), not a default dep — it is spawned out-of-process via `uvx` and drags in chromadb, which has unfixed advisories.
- Telemetry stack binds `127.0.0.1` only and requires `GRAFANA_ADMIN_PASSWORD`; Grafana anonymous access is off.
- The telemetry stack reads a **root** `.env` (compose only reads the project-root one) — separate from `bot/.env`. `${GRAFANA_ADMIN_PASSWORD:?}` treats empty as unset, so a blank value fails the same as a missing file. Grafana applies that password only when it first initializes `grafana-data`; on an existing volume the stored password wins and `.env` changes are ignored. Reset it with `docker compose -f docker-compose.telemetry.yml exec grafana grafana cli admin reset-admin-password '<new>'` — `grafana cli` as a subcommand, since the standalone `grafana-cli` binary was removed in Grafana 13.0 and this stack pins 13.2. To rebuild Grafana from scratch, stop the stack and remove only `<project>_grafana-data` — resolve `<project>` with `docker compose ... config --format json` rather than assuming it, since compose derives it from the directory name and `-p` / `COMPOSE_PROJECT_NAME` override that. Never `down -v`, which also destroys `vm-data` and `vl-data` — every retained metric and agent log.

## Key Configuration (.env)

```env
# Discord
DISCORD_TOKEN=...
DISCORD_GUILD_ID=...
OPENROUTER_API_KEY=...
ALLOWED_USER_IDS=...

# Platform
CTFD_URL=                              # CTFd instance URL
CTFD_TOKEN=                            # CTFd API token

# Solver
AUTOSOLVE_MODEL=haiku                  # Model for auto-solve (haiku/sonnet/opus)
AUTOSOLVE_EFFORT=high                  # Effort level
AUTOSOLVE_SUBAGENT=haiku               # Subagent model
AUTOSOLVE_CONCURRENCY=10               # Parallel solvers
AUTOSOLVE_MAX_BUDGET=0                 # 0 = unlimited (subscription mode)
RACE_MODELS=haiku,opus                 # Models to race
CODEX_ENABLED=true                     # Include Codex in races
MANAGER_CORRECTIONS=true               # Non-security corrections (false = security only, /solve manager: overrides)
MANAGER_ADVICE_MODEL=                   # Correction model (empty = TRIAGE_MODEL, e.g. google/gemini-2.5-pro)
FAST_MODE=false                        # Claude Code fast mode (faster output, higher cost per token)

# File Server
FILE_SERVER_PORT=8080                  # Challenge file server (0 = disabled)

# Telemetry
VICTORIA_LOGS_URL=http://localhost:9428
VICTORIA_METRICS_URL=http://localhost:8428

# Isolation
CTF_ISOLATION=bwrap                    # none/tmpfs/bwrap/devcontainer

# Ghidra MCP (rev/pwn decompiler)
GHIDRA_MCP_ENABLED=false               # Attach Ghidra MCP server for rev/pwn (needs uvx pyghidra-mcp)
DEEP_ANALYSIS_MODEL=haiku              # Model for deep analysis teardown subagents

# Search (SearXNG via MCP)
# SearXNG container on :8888, MCP config at bot/mcp-config.json
```

## Tool Quick Reference

| Task | Tool | Command |
|------|------|---------|
| Decode encoding chain | CyberChef / Python | `from base64 import b64decode` |
| Binary strings | strings | `strings -n 8 binary` |
| Binary RE | Ghidra / r2 | `r2 -A binary` |
| Dynamic analysis | gdb + gef | `gdb ./binary` |
| Packet analysis | tshark | `tshark -r capture.pcap` |
| Web fuzzing | ffuf | `ffuf -u URL/FUZZ -w wordlist` |
| Dir enumeration | feroxbuster | `feroxbuster -u URL` |
| SQL injection | sqlmap | `sqlmap -u URL --forms` |
| Image stego | steghide/zsteg | `zsteg image.png` |
| Metadata | exiftool | `exiftool file` |
| Constraint solving | z3 | Python z3-solver |
| RSA attacks | Python | pycryptodome + sympy + gmpy2 |
| Pwn interaction | pwntools | `from pwn import *` |
| Hash cracking | hashcat/john | `hashcat -m 0 hash wordlist` |
| 32-bit binaries | pkgsi686Linux.glibc | `$NIX_32BIT_GLIBC/lib/ld-linux.so.2 ./binary32` |

## Conventions

- One directory per challenge under `challenges/<category>/<challenge-name>/`
- Each solve gets a `solve.py` (or `solve.sh`) and a `README.md` with the flag and approach
- Flag goes in a `flag.txt` in the challenge directory once confirmed
- Downloaded challenge files go in the challenge directory
- Each solve attempt generates `_attack_graph.json` (cumulative) and `_attack_graph.md` (latest Mermaid) in the challenge directory
- Use `#!/usr/bin/env python3` for all Python scripts
- Use `uv` or nix flake for dependencies, never raw `pip install`
