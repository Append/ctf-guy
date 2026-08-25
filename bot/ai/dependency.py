#!/usr/bin/env python3
"""Challenge dependency detection — identifies series ordering.

Uses regex for obvious patterns (Part 1/2, trailing numbers) and
Gemini Flash for creative naming that regex can't catch.
"""

import json
import logging

from ai.openrouter import OpenRouterClient
from ai.solve_utils import detect_series
from config import Config
from db.challenges import ChallengeRecord

log = logging.getLogger(__name__)


async def detect_dependencies(
    challenges: list[ChallengeRecord],
    config: Config,
) -> dict[int, list[int]]:
    """Detect challenge dependencies and return a prerequisite map.

    Uses LLM as the primary detector — it handles all naming patterns
    naturally (parenthesized parts, creative sequels, implicit series).
    Falls back to regex if the LLM is unavailable.

    Returns {challenge_id: [prerequisite_challenge_ids]}.
    Only includes challenges that actually have prerequisites.
    """
    if len(challenges) < 2:
        return {}

    # Primary: LLM-based detection (handles all patterns)
    deps = await _llm_detect_series(challenges, challenges, config)

    # Fallback: regex if LLM failed or returned nothing
    if not deps:
        log.info("LLM dependency detection returned nothing, falling back to regex")
        deps = _regex_detect_series(challenges)

    return deps


def _regex_detect_series(challenges: list[ChallengeRecord]) -> dict[int, list[int]]:
    """Fallback regex-based series detection."""
    deps: dict[int, list[int]] = {}
    series_groups: dict[str, list[tuple[int, int, ChallengeRecord]]] = {}

    for c in challenges:
        result = detect_series(c.name)
        if result:
            base_name, part_num = result
            series_groups.setdefault(base_name, []).append((part_num, c.id, c))

    for base_name, parts in series_groups.items():
        if len(parts) < 2:
            continue
        parts.sort(key=lambda x: x[0])
        for i in range(1, len(parts)):
            current_id = parts[i][1]
            prereq_ids = [parts[j][1] for j in range(i)]
            deps[current_id] = prereq_ids
            log.info(f"Regex dependency: {parts[i][2].name} depends on " f"{[parts[j][2].name for j in range(i)]}")

    return deps


async def _llm_detect_series(
    _unused: list[ChallengeRecord],
    all_challenges: list[ChallengeRecord],
    config: Config,
) -> dict[int, list[int]]:
    """Use LLM to detect all challenge series and dependencies."""
    if not config.openrouter_api_key:
        return {}

    try:
        client = OpenRouterClient(config)

        challenge_list = []
        id_map = {}
        for c in all_challenges:
            desc_preview = (c.description or "")[:100]
            challenge_list.append(f"- [{c.id}] {c.name} ({c.category}, {c.points}pt) {desc_preview}")
            id_map[c.id] = c

        response = await client.chat_completion(
            model=config.triage_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are analyzing CTF challenge names to find series/sequels that must be solved in order.\n"
                        "Look for ALL patterns:\n"
                        "- Explicit numbering: Part 1/2, (Part 1)/(Part 2), I/II/III, 1/2/3, 0/1/2\n"
                        "- Parenthesized parts: 'Cheese (Part 1)' → 'Cheese (Part 2)'\n"
                        "- Thematic sequels: 'challenge' → 'challenge returns' → 'back to the challenge'\n"
                        "- Progressive series: same base name with increasing numbers or difficulty\n"
                        "- Description references: 'continuation of...', 'building on...', 'sequel to...'\n\n"
                        'Return ONLY valid JSON: {"dependent_id": ["prerequisite_id"], ...}\n'
                        "Use the numeric IDs in brackets. Each dependent should list ALL its prerequisites.\n"
                        "Only include ACTUAL dependencies. If none found, return {}."
                    ),
                },
                {"role": "user", "content": "\n".join(challenge_list)},
            ],
            max_tokens=1000,
        )

        raw = response.choices[0].message.content.strip()
        # Extract JSON from response (may have markdown wrapping)
        if "```" in raw:
            raw = raw.split("```")[1].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

        result = json.loads(raw)
        deps = {}
        for dep_id, prereq_ids in result.items():
            dep_id = int(dep_id)
            if dep_id in id_map:
                valid_prereqs = [int(p) for p in prereq_ids if int(p) in id_map]
                if valid_prereqs:
                    deps[dep_id] = valid_prereqs
                    log.info(
                        f"LLM dependency: {id_map[dep_id].name} depends on "
                        f"{[id_map[p].name for p in valid_prereqs]}"
                    )
        return deps

    except Exception as e:
        log.warning(f"LLM dependency detection failed: {e}")
        return {}
