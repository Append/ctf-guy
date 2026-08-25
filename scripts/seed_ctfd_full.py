#!/usr/bin/env python3
"""Seed local CTFd with a diverse set of solved challenges from the archive.

Picks challenges across categories and difficulty levels, only those with
local files (no running infrastructure needed).
"""

import os
import json
import re
import sys
from pathlib import Path

import httpx

CTFD_URL = os.environ.get("CTFD_URL", "http://localhost:8001")
CTFD_TOKEN = os.environ.get("CTFD_TOKEN", "")

if not CTFD_TOKEN:
    sys.exit("CTFD_TOKEN is not set. Export it before seeding (see .env.example).")
ARCHIVE = Path(__file__).parent.parent / "archive"

# Skip patterns that indicate the challenge needs running infra
INFRA_KEYWORDS = {"nc ", "netcat", "ssh ", "connect to", "instance", "launch", "docker", "port "}

# File extensions that are actual challenge files (not solve artifacts)
CHALLENGE_EXTS = {
    ".py",
    ".txt",
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".gif",
    ".ppm",
    ".pcap",
    ".pcapng",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".7z",
    ".elf",
    ".bin",
    ".exe",
    ".dll",
    ".so",
    ".class",
    ".jar",
    ".apk",
    ".enc",
    ".c",
    ".h",
    ".cpp",
    ".rs",
    ".java",
    ".pdf",
    ".wav",
    ".mp3",
    ".csv",
    ".dat",
    ".msg",
    ".log",
    ".pptx",
    ".docx",
    ".doc",
    ".disk",
    ".img",
    ".dd",
    ".raw",
    ".evtx",
    ".pcapng",
    ".pyc",
    ".flag",
    ".encoded",
    ".cipher",
}

# Category mapping for CTFd
CATEGORY_MAP = {
    "cryptography": "Cryptography",
    "crypto": "Cryptography",
    "forensics": "Forensics",
    "reverse-engineering": "Reversing",
    "rev": "Reversing",
    "general-skills": "Misc",
    "misc": "Misc",
    "blockchain": "Misc",
    "ai": "AI",
    "osint": "OSINT",
}


def find_challenge_files(challenge_dir: Path) -> list[Path]:
    """Find actual challenge files (not solve artifacts)."""
    files = []
    for f in challenge_dir.iterdir():
        if not f.is_file():
            continue
        if f.name in ("flag.txt", "challenge.json", "README.md", "progress.md", "findings.jsonl", "missing_tools.json"):
            continue
        if f.name.startswith("_") or f.name.startswith("solve"):
            continue
        if f.suffix in CHALLENGE_EXTS or f.suffix == "" and f.stat().st_size > 0:
            # Include files without extension if they're not too large (likely binaries/data)
            if f.suffix == "" and f.stat().st_size > 50_000_000:
                continue  # Skip huge files
            files.append(f)
    return files


def load_challenges() -> list[dict]:
    """Scan archive for solved challenges with local files."""
    challenges = []

    for cat_dir in sorted(ARCHIVE.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.endswith(".json"):
            continue

        category = cat_dir.name
        if category in ("binary-exploitation", "pwn", "web", "web-exploitation"):
            continue  # Skip infra-dependent categories entirely

        ctfd_category = CATEGORY_MAP.get(category, "Misc")

        for chall_dir in sorted(cat_dir.iterdir()):
            if not chall_dir.is_dir():
                continue

            flag_path = chall_dir / "flag.txt"
            if not flag_path.exists():
                continue

            try:
                flag = flag_path.read_text().strip()
            except UnicodeDecodeError:
                continue
            if not flag or "test_flag" in flag or "placeholder" in flag:
                continue

            # Keep original flags — challenge files contain the real encoded content

            # Load metadata
            meta_path = chall_dir / "challenge.json"
            points = 100
            description = ""
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    points = meta.get("points", 100)
                    description = meta.get("description", "")
                except Exception:
                    pass

            # Clean description — strip URLs and HTML
            description = re.sub(r"https?://\S+", "", description)
            description = re.sub(r"<[^>]+>", "", description)
            description = re.sub(r"\s+", " ", description).strip()
            if len(description) > 500:
                description = description[:500]

            # Find challenge files
            files = find_challenge_files(chall_dir)

            challenges.append(
                {
                    "dir": str(chall_dir),
                    "name": (
                        chall_dir.name.replace("-", " ").title()
                        if not any(c.isupper() for c in chall_dir.name)
                        else chall_dir.name
                    ),
                    "original_name": chall_dir.name,
                    "category": ctfd_category,
                    "value": points if points > 0 else 100,
                    "description": description or f"Solve the {ctfd_category.lower()} challenge.",
                    "flag": flag,
                    "files": files,
                }
            )

    return challenges


def main():
    all_challenges = load_challenges()
    print(f"Found {len(all_challenges)} eligible challenges")

    # Select ~10 per category across difficulty tiers
    MAX_PER_CATEGORY = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 10

    by_cat = {}
    for c in all_challenges:
        by_cat.setdefault(c["category"], []).append(c)

    challenges = []
    for cat, challs in sorted(by_cat.items()):
        challs.sort(key=lambda c: c["value"])
        pts = [c["value"] for c in challs]
        print(f"  {cat}: {len(challs)} available ({min(pts)}-{max(pts)}pts)")

        if len(challs) <= MAX_PER_CATEGORY:
            challenges.extend(challs)
        else:
            # Pick evenly across the point range
            step = max(1, len(challs) // MAX_PER_CATEGORY)
            selected = challs[::step][:MAX_PER_CATEGORY]
            challenges.extend(selected)

    print(f"\nSelected {len(challenges)} challenges to seed")

    # Sort by category then points
    challenges.sort(key=lambda c: (c["category"], c["value"]))

    if "--dry-run" in sys.argv:
        for c in challenges:
            nfiles = len(c["files"])
            print(f"  [{c['category']}] {c['original_name']} ({c['value']}pts, {nfiles} files)")
        return

    # Seed CTFd
    client = httpx.Client(
        base_url=CTFD_URL,
        headers={
            "Authorization": f"Token {CTFD_TOKEN}",
            "Content-Type": "application/json",
        },
        timeout=30.0,
    )

    created = 0
    for ch in challenges:
        # Create challenge
        resp = client.post(
            "/api/v1/challenges",
            json={
                "name": ch["original_name"],
                "category": ch["category"],
                "description": ch["description"],
                "value": ch["value"],
                "type": "standard",
                "state": "visible",
            },
        )
        if resp.status_code != 200:
            print(f"  ERROR creating {ch['original_name']}: {resp.status_code} {resp.text[:100]}")
            continue

        challenge_id = resp.json()["data"]["id"]

        # Set flag
        client.post(
            "/api/v1/flags",
            json={
                "challenge_id": challenge_id,
                "content": ch["flag"],
                "type": "static",
            },
        )

        # Upload files
        for filepath in ch["files"]:
            try:
                upload_resp = httpx.post(
                    f"{CTFD_URL}/api/v1/files",
                    headers={"Authorization": f"Token {CTFD_TOKEN}"},
                    files={"file": (filepath.name, filepath.read_bytes())},
                    data={"challenge_id": challenge_id, "type": "challenge"},
                    timeout=60.0,
                )
                if upload_resp.status_code != 200:
                    print(f"  WARN: failed to upload {filepath.name} for {ch['original_name']}")
            except Exception as e:
                print(f"  WARN: upload error {filepath.name}: {e}")

        created += 1
        nfiles = len(ch["files"])
        print(f"  [{challenge_id}] {ch['original_name']} ({ch['category']}, {ch['value']}pts, {nfiles} files)")

    print(f"\nDone! Created {created} challenges.")
    client.close()


if __name__ == "__main__":
    main()
