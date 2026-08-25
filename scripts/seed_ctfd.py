#!/usr/bin/env python3
"""Seed a local CTFd instance with test challenges from solved picoCTF challenges."""

import httpx
import os
import json
import sys
from pathlib import Path

CTFD_URL = os.environ.get("CTFD_URL", "http://localhost:8001")
CTFD_TOKEN = os.environ.get("CTFD_TOKEN", "")

if not CTFD_TOKEN:
    sys.exit("CTFD_TOKEN is not set. Export it before seeding (see .env.example).")

# Challenges to seed: (dir, name, category, points, description, flag, files[])
CHALLENGES = [
    {
        "dir": "challenges/forensics/information",
        "name": "Information",
        "category": "Forensics",
        "value": 10,
        "description": "Files can always be changed in a secret way. Can you find the flag? Download the file below.",
        "flag": "picoCTF{the_m3tadata_1s_modified}",
        "files": ["cat.jpg"],
    },
    {
        "dir": "challenges/forensics/glory-of-the-garden",
        "name": "Glory of the Garden",
        "category": "Forensics",
        "value": 50,
        "description": "This garden image contains more than it seems. Can you find the hidden flag?",
        "flag": "picoCTF{more_than_m33ts_the_3y398ee229a}",
        "files": ["garden.jpg"],
    },
    {
        "dir": "challenges/forensics/so-meta",
        "name": "So Meta",
        "category": "Forensics",
        "value": 150,
        "description": "Find the flag hidden in this picture.",
        "flag": "picoCTF{s0_m3ta_9a8b5aa1}",
        "files": ["pico_img.png"],
    },
    {
        "dir": "challenges/cryptography/13",
        "name": "13",
        "category": "Cryptography",
        "value": 100,
        "description": "Cryptography can be easy, do you know what ROT13 is? cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}",
        "flag": "picoCTF{not_too_bad_of_a_problem}",
        "files": [],
    },
    {
        "dir": "challenges/cryptography/rotation",
        "name": "rotation",
        "category": "Cryptography",
        "value": 100,
        "description": "You will find the flag after decrypting this file. Download the encrypted flag.",
        "flag": "picoCTF{r0tat1on_d3crypt3d_25d7c61b}",
        "files": ["encrypted.txt"],
    },
    {
        "dir": "challenges/general-skills/repetitions",
        "name": "repetitions",
        "category": "Misc",
        "value": 100,
        "description": "Can you make sense of this file? Download the file below.",
        "flag": "picoCTF{base64_n3st3d_dic0d!n8_d0wnl04d3d_9b59b35c}",
        "files": ["enc_flag"],
    },
]


def main():
    ctf_root = Path(__file__).parent.parent
    client = httpx.Client(
        base_url=CTFD_URL,
        headers={
            "Authorization": f"Token {CTFD_TOKEN}",
            "Content-Type": "application/json",
        },
        timeout=30.0,
    )

    for ch in CHALLENGES:
        print(f"Creating: {ch['name']} ({ch['category']}, {ch['value']}pts)...")

        # Create challenge
        resp = client.post(
            "/api/v1/challenges",
            json={
                "name": ch["name"],
                "category": ch["category"],
                "description": ch["description"],
                "value": ch["value"],
                "type": "standard",
                "state": "visible",
            },
        )
        if resp.status_code != 200:
            print(f"  ERROR creating: {resp.status_code} {resp.text}")
            continue

        challenge_id = resp.json()["data"]["id"]
        print(f"  Created with ID {challenge_id}")

        # Set flag
        resp = client.post(
            "/api/v1/flags",
            json={
                "challenge_id": challenge_id,
                "content": ch["flag"],
                "type": "static",
            },
        )
        if resp.status_code != 200:
            print(f"  ERROR setting flag: {resp.status_code} {resp.text}")

        # Upload files
        for filename in ch["files"]:
            filepath = ctf_root / ch["dir"] / filename
            if not filepath.exists():
                print(f"  SKIP file {filename} (not found at {filepath})")
                continue

            # File upload needs multipart, not JSON
            upload_resp = httpx.post(
                f"{CTFD_URL}/api/v1/files",
                headers={"Authorization": f"Token {CTFD_TOKEN}"},
                files={"file": (filename, filepath.read_bytes())},
                data={"challenge_id": challenge_id, "type": "challenge"},
                timeout=30.0,
            )
            if upload_resp.status_code == 200:
                print(f"  Uploaded {filename}")
            else:
                print(f"  ERROR uploading {filename}: {upload_resp.status_code} {upload_resp.text}")

    print("\nDone! Seeded challenges:")
    resp = client.get("/api/v1/challenges")
    if resp.status_code == 200:
        for c in resp.json()["data"]:
            print(f"  [{c['id']}] {c['name']} ({c['category']}, {c['value']}pts)")

    client.close()


if __name__ == "__main__":
    main()
