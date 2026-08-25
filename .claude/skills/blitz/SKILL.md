---
name: blitz
description: Full competition mode. Scouts the scoreboard, triages, and dispatches parallel solver agents for all unsolved challenges. Use to go full auto on a CTF.
context: fork
agent: general-purpose
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Task
argument-hint: "[ctfd-url]"
disable-model-invocation: true
---

# Blitz — Full Auto Competition Mode

Scoreboard scrape → triage → parallel solve → report. Maximum velocity.

## Inputs

- CTFd URL: `$ARGUMENTS` (or from `.env`)
- Auth token: `CTFD_TOKEN` from `.env`

## Procedure

### Phase 1: Scout
Run the scout workflow:
1. Fetch all challenges from CTFd API
2. Download all challenge files
3. Create directory structure
4. Generate triage report

### Phase 2: Dispatch Solvers
Spin up parallel solver agents using the `Task` tool with `subagent_type=general-purpose`:

**Wave 1 — Freebies & Quick Wins (50-100pt)**
- Launch one agent per challenge
- These should return flags in seconds to minutes
- Collect flags as agents complete

**Wave 2 — Medium Challenges (150-250pt)**
- Launch agents for medium-difficulty challenges
- Group by category if multiple similar challenges exist
- Allow more time for these

**Wave 3 — Heavy Lifts (300-500pt)**
- Launch agents for hard challenges
- These may need operator input — surface blockers quickly

### Phase 3: Collect & Report
As solver agents return:
1. Validate each flag matches expected format
2. Report flags to operator for submission (don't auto-submit)
3. Update `challenges/TRIAGE.md` with results
4. Track: solved count, total points captured, challenges remaining

### Agent Dispatch Template

For each challenge, the solver agent prompt should include:
- Full challenge description from `challenge.json`
- List of files in the challenge directory
- Category and point value
- Instructions to write `flag.txt` and `solve.py` on success
- The challenge directory path

```
Solve the CTF challenge at {challenge_path}.
Challenge: {name} ({points}pts, {category})
Description: {description}
Files: {file_list}

Analyze the challenge files using appropriate tools for the {category} category.
Write flag.txt with the flag and solve.py documenting the approach.
If stuck after reasonable effort, write README.md with what you tried and any leads.
```

### Parallelism Strategy

- Run up to 5 solver agents concurrently
- Prioritize by points-per-estimated-effort
- If an agent stalls, don't wait — move to next wave
- Freebies should complete before medium challenges launch

## Output

Final scoreboard report:
```
BLITZ RESULTS
=============
Solved: X/Y challenges
Points: N/M total
Time: Xm

SOLVED:
  [x] Challenge A (50pt) — kernel{...}
  [x] Challenge B (100pt) — kernel{...}

UNSOLVED:
  [ ] Challenge C (500pt) — tried RSA, needs more analysis
  [ ] Challenge D (300pt) — binary requires dynamic analysis
```
