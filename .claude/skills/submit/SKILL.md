---
name: submit
description: Submit a flag to CTFd. Validates flag format before submission. Use after solving a challenge.
allowed-tools: Read, Bash, Glob
argument-hint: "[challenge-name] [flag]"
disable-model-invocation: true
---

# Submit — Flag Submission

Validate and submit a flag to the CTFd platform.

## Inputs

- Challenge name or path: `$ARGUMENTS[0]`
- Flag: `$ARGUMENTS[1]` (or read from `flag.txt` in challenge directory)

## Procedure

1. **Resolve the challenge**:
   - Find the challenge directory under `challenges/`
   - Read `challenge.json` to get the challenge ID
   - If flag not provided as argument, read `flag.txt`

2. **Validate flag format**:
   - Must match `kernel{...}` pattern (or whatever format the CTF uses)
   - If flag doesn't match expected format, warn the operator and ask for confirmation
   - NEVER submit empty strings or obviously wrong flags

3. **Submit via CTFd API**:
   ```bash
   curl -s -X POST \
     -H "Authorization: Token $CTFD_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"challenge_id": ID, "submission": "FLAG"}' \
     "$CTFD_URL/api/v1/challenges/attempt"
   ```

4. **Handle response**:
   - `"correct"` → Report success, update `challenge.json` with solved status
   - `"incorrect"` → Report failure, do NOT retry automatically
   - `"already_solved"` → Note it's already solved
   - Rate limited → Wait and inform operator

5. **Update tracking**: Mark challenge as solved in `challenges/TRIAGE.md` if successful.

## Safety

- This skill requires manual invocation (`disable-model-invocation: true`)
- Never spray flags — each submission should be deliberate
- Respect rate limits on the CTFd API
