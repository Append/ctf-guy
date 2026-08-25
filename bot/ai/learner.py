#!/usr/bin/env python3
"""Learning system — extract patterns from solved challenge READMEs.

No LLM needed — READMEs already have structured sections.
Scans challenge directories, parses READMEs, builds pattern files
per category for injection into future solve prompts.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from ai.playbooks import normalize_category

log = logging.getLogger(__name__)


def scan_and_build_patterns(ctf_root: Path) -> dict[str, int]:
    """Scan all challenge READMEs and rebuild pattern files.

    Returns dict of {category: count} for how many patterns were extracted.
    """
    challenges_dir = ctf_root / "challenges"
    patterns_dir = ctf_root / "solvers" / "patterns"
    patterns_dir.mkdir(parents=True, exist_ok=True)

    # Collect patterns grouped by category
    by_category: dict[str, list[dict]] = {}

    for cat_dir in sorted(challenges_dir.iterdir()):
        if not cat_dir.is_dir():
            continue
        category = cat_dir.name

        for chall_dir in sorted(cat_dir.iterdir()):
            if not chall_dir.is_dir():
                continue

            readme_path = chall_dir / "README.md"
            flag_path = chall_dir / "flag.txt"

            if not readme_path.exists():
                continue

            pattern = parse_readme(readme_path, chall_dir.name, flag_path.exists())
            if pattern:
                by_category.setdefault(category, []).append(pattern)

    # Write pattern files
    counts = {}
    for category, patterns in by_category.items():
        pattern_file = patterns_dir / f"{normalize_category(category)}.json"
        pattern_file.write_text(json.dumps(patterns, indent=2))
        counts[category] = len(patterns)
        log.info(f"Patterns: {category} — {len(patterns)} entries")

    return counts


def parse_readme(readme_path: Path, challenge_slug: str, has_flag: bool) -> dict | None:
    """Parse a structured README.md into a pattern entry."""
    try:
        content = readme_path.read_text()
    except Exception:
        return None

    if not content.strip():
        return None

    # Extract sections
    name = _extract_heading(content) or challenge_slug
    summary = (
        _extract_section(content, "summary")
        or _extract_section(content, "description")
        or ""
    )
    approach = _extract_section(content, "approach") or ""
    key_insight = (
        _extract_section(content, "key insight")
        or _extract_section(content, "insight")
        or ""
    )
    tools = (
        _extract_section(content, "tools")
        or _extract_section(content, "tools used")
        or ""
    )
    missing_tools = _extract_section(content, "missing tools") or ""
    flag = _extract_flag(content)

    # Extract points and category from the first few lines
    points = _extract_field(content, "points") or "0"
    # Skip if too sparse
    if not summary and not approach and not key_insight:
        return None

    return {
        "challenge": name,
        "slug": challenge_slug,
        "pattern": summary[:200] if summary else "",
        "key_insight": key_insight[:300] if key_insight else "",
        "approach": approach[:500] if approach else "",
        "tools": _parse_list(tools),
        "missing_tools": _parse_list(missing_tools),
        "points": _safe_int(points),
        "solved": has_flag,
        "flag": flag or "",
        "timestamp": datetime.fromtimestamp(readme_path.stat().st_mtime).isoformat(),
    }


def learn_from_challenge(
    challenge_dir: str,
    ctf_root: Path,
    cost_usd: float = 0.0,
    num_turns: int = 0,
    duration_ms: int = 0,
    model: str = "",
) -> dict | None:
    """Learn from a single challenge after solving. Call this post-solve.

    Parses the README, updates the category pattern file, returns the pattern entry.
    """
    chall_path = Path(challenge_dir)
    readme_path = chall_path / "README.md"
    flag_path = chall_path / "flag.txt"

    if not readme_path.exists():
        return None

    pattern = parse_readme(readme_path, chall_path.name, flag_path.exists())
    if pattern:
        pattern["cost_usd"] = cost_usd
        pattern["num_turns"] = num_turns
        pattern["duration_ms"] = duration_ms
        pattern["model"] = model
    if not pattern:
        return None

    # Determine category from challenge.json or directory structure
    category = _get_category(chall_path)

    # Update the category pattern file
    patterns_dir = ctf_root / "solvers" / "patterns"
    patterns_dir.mkdir(parents=True, exist_ok=True)
    pattern_file = patterns_dir / f"{normalize_category(category)}.json"

    existing = []
    if pattern_file.exists():
        try:
            existing = json.loads(pattern_file.read_text())
        except (json.JSONDecodeError, OSError):
            existing = []

    # Deduplicate by slug
    existing = [p for p in existing if p.get("slug") != pattern["slug"]]
    existing.append(pattern)

    pattern_file.write_text(json.dumps(existing, indent=2))
    log.info(
        f"Learned from {pattern['challenge']} -> {category} patterns ({len(existing)} total)"
    )

    return pattern


def get_patterns_context(category: str, ctf_root: Path) -> str:
    """Load learned patterns for a category to inject into solver prompts."""
    patterns_dir = ctf_root / "solvers" / "patterns"

    # Canonical name first; the bare spellings are legacy files from before
    # normalize_category() was applied to the filename.
    for name in [normalize_category(category), category.lower(), category]:
        patterns_file = patterns_dir / f"{name}.json"
        if patterns_file.exists():
            break
    else:
        return ""

    try:
        patterns = json.loads(patterns_file.read_text())
    except (json.JSONDecodeError, OSError):
        return ""

    if not patterns:
        return ""

    # Only include solved challenges with insights
    useful = [
        p
        for p in patterns
        if p.get("solved") and (p.get("key_insight") or p.get("approach"))
    ]
    if not useful:
        return ""

    lines = ["\n\nLEARNED PATTERNS FROM PREVIOUS CHALLENGES:"]
    for p in useful[-15:]:  # Last 15 patterns
        insight = p.get("key_insight", "")[:150]
        approach = p.get("approach", "")[:100]
        lines.append(
            f"- [{p.get('challenge', '?')} ({p.get('points', '?')}pts)]: "
            f"{insight or approach}"
        )

    return "\n".join(lines)


# --- Parsing helpers ---


def _extract_heading(content: str) -> str:
    """Extract the first # heading."""
    m = re.search(r"^#\s+(.+?)(?:\s*[-—|]|$)", content, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _extract_section(content: str, section_name: str) -> str:
    """Extract content under a ## heading matching section_name."""
    pattern = rf"(?:^|\n)##\s+{re.escape(section_name)}[^\n]*\n(.*?)(?=\n##\s|\Z)"
    m = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
    if not m:
        # Try with ** bold markers
        pattern = rf"\*\*{re.escape(section_name)}[:\*]*\*\*\s*(.*?)(?=\n\*\*|\n##|\Z)"
        m = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_field(content: str, field: str) -> str:
    """Extract a field like 'Points: 50' or '**Points:** 50'."""
    m = re.search(
        rf"(?:\*\*)?{re.escape(field)}(?:\*\*)?[:\s]+(.+?)(?:\n|\||$)",
        content,
        re.IGNORECASE,
    )
    return m.group(1).strip().strip("*`") if m else ""


def _extract_flag(content: str) -> str:
    """Extract picoCTF{...} or kernel{...} flag from content."""
    m = re.search(r"(picoCTF\{[^}]+\}|kernel\{[^}]+\})", content)
    return m.group(1) if m else ""


def _parse_list(text: str) -> list[str]:
    """Parse a markdown list or comma-separated items."""
    if not text:
        return []
    items = []
    for line in text.split("\n"):
        line = line.strip().lstrip("- *•").strip()
        line = line.strip("`")
        if (
            line
            and line.lower() not in ("none", "n/a", "no", "none.")
            and not line.startswith("none")
        ):
            items.append(line)
    return items


def _safe_int(s: str) -> int:
    try:
        return int(re.sub(r"[^\d]", "", s))
    except (ValueError, TypeError):
        return 0


def _get_category(chall_path: Path) -> str:
    """Get category from challenge.json or parent directory name."""
    meta_path = chall_path / "challenge.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            return meta.get("category", chall_path.parent.name).lower()
        except Exception:
            pass
    return chall_path.parent.name
