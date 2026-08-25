#!/usr/bin/env python3
"""Load solver playbooks from solvers/agents/*.md."""

from pathlib import Path

CATEGORY_MAP = {
    "crypto": "crypto",
    "cryptography": "crypto",
    "rev": "rev",
    "reverse": "rev",
    "reverse engineering": "rev",
    "reversing": "rev",
    "pwn": "pwn",
    "binary exploitation": "pwn",
    "exploitation": "pwn",
    "web": "web",
    "web exploitation": "web",
    "forensics": "forensics",
    "misc": "misc",
    "miscellaneous": "misc",
    "general skills": "misc",
    "osint": "osint",
    "ai": "ai",
    "prompt engineering": "ai",
    "badge": "misc",
    "hardware": "misc",
}

# Categories to skip in autosolve (require physical hardware or special handling)
SKIP_CATEGORIES = {"badge", "hardware", "kernelcoin"}

# --- Behavioral category sets (single source of truth) ---
# Update these when competition categories are known.

# Categories where iterative/brute-force approaches are expected workflow
BRUTE_OK_CATEGORIES = {"forensics", "misc", "pwn"}

# Categories that get the heavy model (Opus) regardless of point value
HEAVY_MODEL_CATEGORIES = {"pwn", "rev"}

# Categories that get Ghidra MCP decompiler attached
GHIDRA_CATEGORIES = {"rev", "pwn"}

# Per-category stall thresholds (seconds). Pwn/rev need longer thinking pauses.
STALL_THRESHOLDS = {
    "pwn": 180,
    "rev": 180,
}
STALL_THRESHOLD_DEFAULT = 90

# Categories that get playwright-cli browser instructions
WEB_CATEGORIES = {"web"}

# Tool → category allowlist for CategoryDriftDetector
# Maps tool names to the set of categories where they're appropriate.
# Tools used outside their expected category trigger a drift warning.
TOOL_CATEGORY_MAP = {
    "sqlmap": {"web"},
    "ffuf": {"web"},
    "feroxbuster": {"web"},
    "curl": {"web", "misc"},
    "nikto": {"web"},
    "burpsuite": {"web"},
    "pwntools": {"pwn"},
    "gdb": {"pwn", "rev"},
    "checksec": {"pwn"},
    "ropper": {"pwn"},
    "ROPgadget": {"pwn"},
    "one_gadget": {"pwn"},
    "ghidra": {"rev", "pwn"},
    "radare2": {"rev", "pwn"},
    "r2": {"rev", "pwn"},
    "angr": {"rev"},
    "z3": {"rev", "crypto"},
    "binwalk": {"forensics", "misc"},
    "volatility": {"forensics"},
    "foremost": {"forensics"},
    "steghide": {"forensics", "misc"},
    "exiftool": {"forensics", "misc"},
    "tshark": {"forensics"},
    "wireshark": {"forensics"},
    "hashcat": {"crypto", "forensics"},
    "john": {"crypto", "forensics"},
    "sage": {"crypto"},
    "sympy": {"crypto"},
    "gmpy2": {"crypto"},
    "factordb": {"crypto"},
}

# Discord embed colors per canonical category
CATEGORY_COLORS = {
    "crypto": 0x9B59B6,  # Purple
    "pwn": 0xE74C3C,  # Red
    "rev": 0xE67E22,  # Orange
    "web": 0x3498DB,  # Blue
    "forensics": 0x2ECC71,  # Green
    "misc": 0x95A5A6,  # Gray
    "osint": 0x1ABC9C,  # Teal
    "ai": 0xF39C12,  # Yellow
}


def normalize_category(category: str) -> str:
    """Normalize a category name to its canonical form.

    Separators are folded before lookup: challenge directories are hyphen-
    slugified ("web-exploitation") while CATEGORY_MAP is keyed on the spaced
    platform names ("web exploitation"). Without this, every hyphenated
    category silently fell through to "misc".
    """
    key = category.lower().strip().replace("-", " ").replace("_", " ")
    key = " ".join(key.split())
    return CATEGORY_MAP.get(key, "misc")


def load_playbook(category: str, ctf_root: Path) -> str:
    """Load the solver playbook for a challenge category.

    Reads from solvers/agents/{category}.md on disk so updates
    are picked up immediately without restarting the bot.
    """
    normalized = CATEGORY_MAP.get(category.lower(), "misc")
    playbook_path = ctf_root / "solvers" / "agents" / f"{normalized}.md"

    if not playbook_path.exists():
        playbook_path = ctf_root / "solvers" / "agents" / "misc.md"

    if not playbook_path.exists():
        return f"You are a CTF challenge solver specializing in {category}. Solve the challenge methodically."

    return playbook_path.read_text()
