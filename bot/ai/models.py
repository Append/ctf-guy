#!/usr/bin/env python3
"""Model routing — select the right model for the task."""

from ai.playbooks import HEAVY_MODEL_CATEGORIES, normalize_category
from config import Config


def select_model(category: str, points: int, config: Config) -> str:
    """Pick a model based on challenge difficulty.

    - Default model (Sonnet) for easy/medium challenges
    - Heavy model (Opus) for hard challenges (>=300pt) or categories in HEAVY_MODEL_CATEGORIES

    Note: triage_model (Gemini) is reserved for non-tool tasks like
    the learner's post-solve analysis. Solving always needs tool_use
    which Gemini doesn't support well via OpenRouter.
    """
    if points >= 300 or normalize_category(category) in HEAVY_MODEL_CATEGORIES:
        return config.heavy_model

    return config.default_model
