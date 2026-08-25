---
name: scout
description: Scrape a CTFd scoreboard, download challenge files, triage by difficulty, and create the challenge directory structure. Use at the start of a CTF competition.
allowed-tools: Read, Write, Bash, Glob, Grep, Edit
argument-hint: "[ctfd-url]"
---

# Scout — CTFd Scoreboard Scraper & Triager

Connect to the CTFd instance, pull all challenges, download files, and triage.

## Inputs

- CTFd URL: `$ARGUMENTS` (or fall back to `CTFD_URL` from `.env`)
- Auth token: `CTFD_TOKEN` from `.env`

## Procedure

1. **Load credentials** from `.env` in the project root. If missing, ask the operator for the CTFd URL and API token.

2. **Fetch all challenges** via the CTFd API:
   ```bash
   curl -s -H "Authorization: Token $CTFD_TOKEN" "$CTFD_URL/api/v1/challenges" | jq .
   ```

3. **For each challenge**, fetch details and files:
   ```bash
   curl -s -H "Authorization: Token $CTFD_TOKEN" "$CTFD_URL/api/v1/challenges/{id}" | jq .
   ```

4. **Create directory structure** under `challenges/<category>/<challenge-name>/`:
   - Slugify challenge names (lowercase, hyphens, no special chars)
   - Download all challenge files into the directory
   - Write a `challenge.json` with the raw API response (id, name, description, points, category, files, hints)

5. **Triage and rank** all challenges. Output a table sorted by points ascending:

   | Priority | Challenge | Category | Points | Estimate | Approach |
   |----------|-----------|----------|--------|----------|----------|
   | 1 | Survey | misc | 50 | Freebie | Fill out form |
   | 2 | ... | ... | ... | ... | ... |

   Priority categories:
   - **Freebie**: Surveys, sanity checks, flag-in-description
   - **Quick win**: Single-step decode, `strings` on binary, simple stego, basic web
   - **Medium**: Standard crypto, basic rev, web enum, network forensics
   - **Heavy**: Multi-stage, complex pwn, 500pt challenges

6. **Save triage report** to `challenges/TRIAGE.md`

7. **Identify already-solved challenges** (if any have `solved_by_me: true` in API response) and note them.

## Output

Print the triage table to the operator. Recommend which challenges to hit first. If running in competition mode, suggest parallel solve dispatch.
