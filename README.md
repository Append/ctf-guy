# CTF Guy

A Discord bot that orchestrates AI coding agents to solve Jeopardy-style CTF challenges in parallel.

Point it at a CTF platform, and it scrapes the challenge list, spins up a Discord thread per challenge,
and dispatches Claude Code or Codex subprocesses to work on them concurrently — with live supervision,
automatic flag submission, and a telemetry stack so you can watch every agent think.

> **Scope:** This is competition tooling. It is built to be pointed at CTF infrastructure you have been
> explicitly authorized to attack — the designated challenge hosts of an event you are registered for.
> See [Responsible use](#responsible-use).

## What it does

**Scout** — `/scout url:https://ctf.example.org event:"Some CTF 2026"`
Pulls challenges from CTFd or picoCTF, filters out already-solved ones, downloads attachments, and
creates a forum channel per category with a thread per challenge.

**Solve** — challenges get worked by isolated agent subprocesses:

| Mode | Command | Behavior |
|------|---------|----------|
| Single | `/solve` | One solver in the current challenge thread |
| Deep | `/solve deep:True` | Parallel Source + Infra teardown subagents, then solve with merged analysis |
| Queue | `/scout autosolve:True` | Concurrent queue across every scouted challenge |
| Race | `/scout race:True` | Races several models per challenge; first correct flag wins, losers cancelled |

**Manage** — a supervisor watches each solver's reasoning in real time. Heuristic detectors catch loops,
category drift, stalls, and rabbit holes; a second model turns those signals into domain-aware course
corrections, injected mid-solve through a `PostToolUse` hook. Escalates across three tiers if the agent
keeps missing. Security detectors (prompt injection, reverse shells, exfiltration) always run and cannot
be toggled off.

**Observe** — `docker compose -f docker-compose.telemetry.yml up -d`
Grafana on `:3000` with Race View / Agent Deep Dive / Queue Operations dashboards, VictoriaMetrics for
cost and duration, VictoriaLogs for the live thinking stream.

**Learn** — solved challenges are distilled into per-category pattern files under `solvers/patterns/`,
which get fed back into later solver prompts.

## Getting started

1. **System prerequisites** — Nix, direnv, Docker, and the agent CLIs. See [SETUP.md](SETUP.md).
2. **Environment** — copy `bot/.env.example` to `bot/.env` and fill in your Discord token, OpenRouter key,
   and CTF platform credentials. Copy `.env.example` to `.env` for the compose stack.
3. **Run** — `cd bot && uv run python3 run.py`
4. **Competition workflow** — see [QUICKSTART.md](QUICKSTART.md).

Tests: `cd bot && uv run python3 -m pytest tests/ -x -q`

## Layout

```
bot/            Discord bot — commands, solver orchestration, platform adapters, telemetry
  ai/           Solver abstraction, racing, queue, manager, sandboxing, learning
  commands/     Slash commands (scout, solve, submit, autosolve, interact, ...)
  platforms/    CTF platform adapters (CTFd, picoCTF)
solvers/
  agents/       Per-category solver prompt templates
  patterns/     Learned solve patterns, accumulated across events
deploy/         Grafana dashboards + alerting, SearXNG config
docs/           Category map and design docs
flake.nix       Dev environment with the CTF toolchain
```

`CLAUDE.md` documents the architecture and the non-obvious invariants in far more detail — read it before
changing solver internals.

## Isolation caveat

`CTF_ISOLATION=bwrap` gives each solver its own workspace overlay, which is what makes concurrent and race
solving safe from agents stepping on each other's files. It is **not** a security boundary: the sandbox
binds the full host filesystem. Run this on a machine you would be comfortable handing to an autonomous
agent, not on your daily driver with production credentials in `~/.aws`.

## Responsible use

This tool drives autonomous agents that execute code and interact with network services. Use it only
against systems you are authorized to test — the designated challenge infrastructure of a CTF you are
participating in.

Most CTFs have rules that this tooling makes easy to break by accident. Before pointing it at an event,
check the rules on brute-forcing, request rates, and which hosts are in scope, and configure concurrency
accordingly. Don't DoS challenge infrastructure, and don't attack the scoring platform itself.

The pattern files in `solvers/patterns/` contain flags from CTFs that have already finished.

## License

Two licenses, split by directory:

| Path | License | |
|---|---|---|
| Everything except `solvers/patterns/` | **Apache License 2.0** | [LICENSE](LICENSE) |
| `solvers/patterns/**` | **CC BY-NC 4.0** — noncommercial only | [solvers/patterns/LICENSE](solvers/patterns/LICENSE) |

The code is permissively licensed: use it, fork it, build on it, commercially or
not, under Apache 2.0's terms (attribution, state your changes, and you get an
explicit patent grant).

The learned solve-pattern corpus under `solvers/patterns/` is **not** covered by
that grant. It is CC BY-NC 4.0 — share and adapt it for noncommercial purposes
with attribution; incorporating it into a commercial product or service requires
separate terms from the copyright holder. See
[solvers/patterns/README.md](solvers/patterns/README.md).

If you want the tool without the licensing split, delete `solvers/patterns/`. The
code runs fine against an empty corpus — `learner.py` simply rebuilds it from your
own solves.
