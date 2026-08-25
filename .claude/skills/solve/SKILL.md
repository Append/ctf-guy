---
name: solve
description: Attempt to solve a CTF challenge. Auto-detects category from challenge files and metadata, then dispatches to the right approach. Use on a specific challenge directory.
context: fork
agent: general-purpose
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Task
argument-hint: "[challenge-path-or-name]"
---

# Solve — Challenge Solver Dispatcher

Analyze a challenge directory and solve it using the appropriate approach.

## Inputs

- Challenge path or name: `$ARGUMENTS`
- If a name is given, resolve to `challenges/<category>/<name>/`
- If no argument, ask the operator which challenge to target

## Procedure

1. **Load challenge context**:
   - Read `challenge.json` for description, points, category, hints
   - List all files in the directory with `file` command on each
   - Run `strings` on any binaries
   - Check file sizes, magic bytes, extensions

2. **Auto-detect category** from metadata and file analysis:
   - Has `.py`/`.sage`/numbers/RSA terms → crypto
   - Has ELF/PE/Mach-O binary → rev or pwn
   - Has URL/port/web keywords → web
   - Has `.pcap`/`.pcapng` → forensics (network)
   - Has image files → stego or forensics
   - Has `.apk` → mobile/misc
   - Challenge description mentions AI/chatbot → AI/prompt engineering

3. **Dispatch to category solver** by invoking the appropriate skill:
   - Crypto → `crypto-solver`
   - Reversing → `rev-solver`
   - Web → `web-solver`
   - Forensics → `forensics-solver`
   - Misc/OSINT/Stego/AI → `misc-solver`
   - Pwn → Use pwntools template directly

4. **If solve succeeds**:
   - Write flag to `flag.txt` in the challenge directory
   - Write `solve.py` or `solve.sh` documenting the approach
   - Write brief `README.md` with: flag, approach summary, tools used
   - Report flag to operator and suggest running `/submit`

5. **If solve fails after reasonable effort**:
   - Document what was tried in `README.md`
   - Note any partial progress or leads
   - Report to operator with recommendation: retry with different approach, skip, or ask for hints

## Parallel Solving

When called from `/blitz`, this skill may be running as a subagent. Write all output to files in the challenge directory so results persist.
