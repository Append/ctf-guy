# Security

## What this project is

CTF Guy runs autonomous AI agents that execute arbitrary code and interact with
network services, by design. It is competition tooling: you point it at CTF
infrastructure you are authorized to attack, and it tries to break in.

That makes the threat model unusual, so it is worth stating plainly.

## Threat model

**Trusted:** the operator, the machine it runs on, and the Discord users in
`ALLOWED_USER_IDS`.

**Untrusted:** everything a solver touches — challenge files, challenge
descriptions, remote service output, web pages fetched during a solve. Challenge
content is attacker-controlled by definition and reaches an agent that can run
commands. The manager includes always-on detectors for prompt injection, reverse
shells, and exfiltration attempts, but these are heuristics, not a boundary.

**Explicitly not a security boundary:** the sandbox. `CTF_ISOLATION=bwrap` gives
each solver its own workspace overlay so concurrent solves don't collide, but it
binds the full host filesystem and leaves agent CLI credentials reachable.
`CTF_ISOLATION=none` gives an agent your host outright.

Run this on a machine you would be comfortable handing to an autonomous agent.
Not your daily driver with production credentials in `~/.aws`.

## Defaults

The project ships closed where it can:

- The bot refuses to start with an empty `ALLOWED_USER_IDS` unless you set
  `ALLOW_ALL_USERS=true` deliberately.
- The challenge file server binds `127.0.0.1` and requires a per-run bearer
  token on every route, including static files. It serves no directory indexes.
- The telemetry stack publishes only on `127.0.0.1`, Grafana anonymous access is
  off, and the stack refuses to start without `GRAFANA_ADMIN_PASSWORD`.
- Solver subprocesses get a sanitized environment (`bot/ai/sandbox.py`) with
  platform and API credentials stripped.

If you widen any of these — binding the file server to `0.0.0.0` for tailnet
access is the common case — understand that the file server exposes challenge
files, `flag.txt`, and endpoints that act on your competition account.

## Reporting a vulnerability

Please report security issues through GitHub's private vulnerability reporting
("Report a vulnerability" on the Security tab) rather than a public issue.

Include what you were doing, what happened, and the impact. There is no bounty.

Findings in *challenge solutions* under `solvers/patterns/` are not
vulnerabilities — that directory is a record of exploiting CTF challenges and is
expected to contain exploit code and credentials for retired competition
services.
