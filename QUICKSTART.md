# CTF Guy — Quick Start Guide

## Before the Competition

### 1. Identify the CTF Platform

| Platform | How to tell | Supported |
|----------|------------|-----------|
| **CTFd** | URL has `/challenges`, `/scoreboard`. Most common. | Yes — API scraping |
| **picoCTF** | `play.picoctf.org` | Yes — Playwright + API |
| **rCTF** | URL has `/challs` | Not yet — use CTFd adapter as base |
| **Other** | Custom platform | Manual — scout won't work, create threads manually |

### 2. Create an Account / Get Credentials

- Register on the CTF platform
- For **CTFd**: go to Settings → Access Tokens → generate an API token
- For **picoCTF**: just username/password (bot handles reCAPTCHA via browser)

### 3. Read the Rules

**Before running scout, check these:**

- [ ] **Is automated scraping allowed?** Most CTFs allow reading the challenge list via API. Some prohibit automated tools entirely.
- [ ] **Is automated solving allowed?** Some CTFs ban bots/AI. Others don't care as long as you don't DoS.
- [ ] **Rate limits?** Check if there are flag submission rate limits or request throttling.
- [ ] **Flag format?** Note the format (e.g. `kernel{...}`, `picoCTF{...}`, `flag{...}`).
- [ ] **Brute force policy?** Most CTFs prohibit brute forcing the platform. Challenge-specific brute force (password cracking) is usually fine.
- [ ] **Sharing policy?** Don't share flags or approaches with other teams during the competition.

**If the rules prohibit automated tools or AI, do not use this bot.**

### 4. Configure the Bot

```bash
cd bot
cp .env.example .env
nano .env
```

Fill in:

```env
# Required
DISCORD_TOKEN=<your bot token>
DISCORD_GUILD_ID=<your server ID>
OPENROUTER_API_KEY=<your openrouter key>
ALLOWED_USER_IDS=<your discord user ID>
CTF_ROOT=/path/to/ctf-guy

# Platform-specific (fill in the one you're using)
# CTFd:
CTFD_URL=https://ctf.example.com
CTFD_TOKEN=<api token from CTFd settings>

# picoCTF: no config needed — bot opens browser for login

# Models
DEFAULT_MODEL=anthropic/claude-sonnet-4.6
HEAVY_MODEL=anthropic/claude-opus-4.6
TRIAGE_MODEL=google/gemini-3-flash-preview

# Auto-solve settings
AUTOSOLVE_MODEL=sonnet          # Model for auto-solve (haiku/sonnet/opus)
AUTOSOLVE_EFFORT=high           # Thinking effort (low/medium/high/max)
AUTOSOLVE_SUBAGENT=haiku        # Subagent model (overrides global setting)
AUTOSOLVE_CONCURRENCY=10        # Parallel workers

# Isolation (optional)
CTF_ISOLATION=none              # none, tmpfs, bwrap, devcontainer
```

### 5. Start the Bot

```bash
cd bot
uv run python run.py
```

Verify it shows "Online as ctf-guy#..." in the terminal.

---

## During the Competition

### Phase 1: Scout

In your Discord server (any channel):

```
/scout url:https://ctf.example.com
```

Options:
- `platform:CTFd` or `platform:picoCTF` (auto-detected from URL)
- `category:crypto` — only import one category
- `limit:20` — limit how many new challenges to import
- `autosolve:true` — start auto-solving immediately after scouting

This creates:
- A Discord category named after the CTF
- One channel per challenge category (crypto, pwn, web, etc.)
- One thread per challenge with description and metadata

For **picoCTF**: a browser window opens for you to log in manually (reCAPTCHA). After login, scouting proceeds automatically.

### Phase 2: Solve

**Auto-solve all:**
```
/autosolve
```
Starts 10 concurrent Claude Code workers on all unsolved challenges, sorted by points ascending (easiest first).

**Solve one challenge:**
In a challenge thread:
```
/solve
/solve approach:try format string vulnerability
/solve model:opus effort:max
```

**Ask a question:**
```
/ask question:what encoding is this ciphertext?
```

**Submit manually:**
```
/submit flag:kernel{the_flag_here}
```

### Phase 3: Monitor

**Dashboard:** Auto-solve posts a live-updating embed showing progress, currently solving, recent solves/fails.

**Thread status:**
- ⬜ Unsolved
- ✅ Solved
- 🔴 Needs attention (failed, unusual flag, missing tools)

**Check progress:**
```
/status
/autosolve action:Show status
```

**Stop auto-solve:**
```
/autosolve action:Stop
```

### Phase 4: Triage Failures

Red threads need human attention. Common reasons:
- **Non-standard flag format** — bot found a flag but it doesn't match the expected format. Check the thread and `/submit` manually.
- **Missing tools** — bot lists what it needed. Add to `flake.nix` and re-solve.
- **Instance not launched** — for picoCTF, the instance may need manual launching via the browser.
- **Needs approach hint** — re-run `/solve approach:hint about what to try`.

### Phase 5: Learn

After a batch of solves:
```
/learn
```
Scans all solved challenge READMEs and builds pattern files. Future solves in the same category get these patterns injected into their prompts.

Learning also happens automatically after each solve.

---

## Re-scouting

If new challenges are released mid-competition:
```
/scout url:https://ctf.example.com
```
The bot detects the existing CTF, skips already-tracked challenges, and only creates threads for new ones.

---

## Tips

- **Start with `/scout ... limit:10 autosolve:true`** to verify everything works on easy challenges before going full blast.
- **Low-point challenges first** — they're usually solvable and build pattern context for harder ones.
- **Series challenges** (Part 1, Part 2) — the bot automatically injects Part 1's writeup into Part 2's prompt if Part 1 is solved first.
- **Use `model:opus effort:max`** on stubborn challenges after haiku/sonnet fails.
- **Check the terminal** for detailed logs if something seems stuck.
- **`nix-shell -p <package>`** — the solver knows to use this for tools not in the flake.
