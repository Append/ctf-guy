---
description: Review solve logs for a challenge from VictoriaLogs telemetry
user-invocable: true
---

Review the solve logs for a CTF challenge. Use the `bot/tools/logs` CLI tool to query VictoriaLogs.

**User input:** $ARGUMENTS

If the user provided a challenge name, run:
```
bot/tools/logs <challenge-name>
```

If the user said "recent" or didn't specify a challenge, run:
```
bot/tools/logs --recent
```

If the user said "active" or "running" or "current", run:
```
bot/tools/logs --active
```

If the user asked about manager interventions, add `--manager`.
If the user asked about errors, add `--errors`.
If the user asked about all attempts (not just the latest), add `--all`.

After getting the output, provide a concise analysis:
1. Which solvers participated and what each did
2. Whether the flag was found and by whom
3. How web search and manager interventions were used
4. Any issues (policy blocks, errors, timeouts, tool problems)
5. What could be improved on a retry
