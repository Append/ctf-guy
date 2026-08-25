# Competition Category Configuration

All category-specific behavior is centralized in **`bot/ai/playbooks.py`**. Update this single file when competition categories are known.

## Quick Reference

| What | Variable | Default | Effect |
|------|----------|---------|--------|
| **Name normalization** | `CATEGORY_MAP` | Maps aliases to canonical names | Everything uses this to resolve "Binary Exploitation" → "pwn" |
| **Brute-force OK** | `BRUTE_OK_CATEGORIES` | `forensics, misc, pwn` | Manager won't warn about iterative approaches in these categories |
| **Heavy model** | `HEAVY_MODEL_CATEGORIES` | `pwn, rev` | Auto-selects Opus regardless of point value |
| **Ghidra MCP** | `GHIDRA_CATEGORIES` | `rev, pwn` | Attaches Ghidra decompiler MCP server |
| **Playwright** | `WEB_CATEGORIES` | `web` | Injects browser instructions into solver prompt |
| **Tool drift map** | `TOOL_CATEGORY_MAP` | sqlmap→web, pwntools→pwn, etc. | Manager warns when tools are used outside their expected category |
| **Embed colors** | `CATEGORY_COLORS` | Per-category Discord embed colors | Visual only |

## KernelCon Caveats

### Unknown Categories Until Game Day

KernelCon category names are only known once the CTFd instance goes live. Past years used:
- Crypto, Forensics, Reversing, Web, Misc, Badge/Hardware, AI/Prompt Engineering

But names and new categories can change year to year. **Scout the CTFd first**, then update `CATEGORY_MAP` to normalize whatever they use.

### Likely Needed Updates

1. **`CATEGORY_MAP`** — Add any new KernelCon category names as aliases. Example: if they add a "Hardware Hacking" category, add `"hardware hacking": "misc"` (or a new canonical name if it deserves distinct behavior).

2. **`BRUTE_OK_CATEGORIES`** — KernelCon's "Badge" challenges often involve hardware brute-force or signal analysis. If badge gets its own category (not misc), consider adding it here.

3. **`HEAVY_MODEL_CATEGORIES`** — KernelCon's higher-point Crypto and AI challenges may warrant Opus. Consider adding `"crypto"` or `"ai"` if early haiku solves are failing on 300+ pt challenges in those categories.

4. **`TOOL_CATEGORY_MAP`** — If KernelCon introduces a new category with specific tooling (e.g., "Hardware" with logic analyzers), add the tool→category mappings so drift detection works.

5. **`CATEGORY_COLORS`** — Add colors for any new canonical categories so Discord embeds aren't all gray.

### Day-Of Checklist

```
1. /scout the CTFd instance
2. Check what category names appear in challenge threads
3. Open bot/ai/playbooks.py
4. Add any missing names to CATEGORY_MAP
5. Review behavioral sets (BRUTE_OK, HEAVY_MODEL, etc.)
6. Restart bot: cd bot && uv run python3 run.py
```

### What NOT to Change Mid-Competition

- **`TOOL_CATEGORY_MAP`** — Changing tool allowlists mid-comp can cause the manager to suddenly start/stop flagging solvers that are mid-solve. Only update between solve batches.
- **`HEAVY_MODEL_CATEGORIES`** — Changing model routing mid-queue affects cost. Update before kicking off autosolve, not during.

## File Locations

All in `bot/ai/playbooks.py`:
- `CATEGORY_MAP` (line ~6)
- `BRUTE_OK_CATEGORIES` (line ~28)
- `HEAVY_MODEL_CATEGORIES` (line ~31)
- `GHIDRA_CATEGORIES` (line ~34)
- `WEB_CATEGORIES` (line ~37)
- `TOOL_CATEGORY_MAP` (line ~41)
- `CATEGORY_COLORS` (line ~75)
- `normalize_category()` (line ~85)

## Consumers

These files import from `playbooks.py` — they should NOT have their own category lists:

| File | What it uses |
|------|-------------|
| `bot/ai/manager.py` | `BRUTE_OK_CATEGORIES`, `TOOL_CATEGORY_MAP`, `normalize_category` |
| `bot/ai/models.py` | `HEAVY_MODEL_CATEGORIES`, `normalize_category` |
| `bot/ai/claude_code.py` | `GHIDRA_CATEGORIES` |
| `bot/ai/solve_utils.py` | `WEB_CATEGORIES`, `GHIDRA_CATEGORIES`, `normalize_category` |
| `bot/discord_ui/embeds.py` | `CATEGORY_COLORS`, `normalize_category` |
