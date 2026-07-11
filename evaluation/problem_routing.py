"""FailureCategory ownership routing for Day-1 diagnostics.

This module is intentionally a pure convention layer: it reuses the frozen
``FailureCategory`` enum and does not introduce schema changes.
"""

from __future__ import annotations

from typing import Literal

from contracts import FailureCategory

Owner = Literal["A", "B", "C"]


_CATEGORY_OWNER: dict[FailureCategory, Owner] = {
    # A: supervisor / engine flow / belief mechanics.
    FailureCategory.SYSTEM_ERROR: "A",
    FailureCategory.PHASE_STUCK: "A",
    FailureCategory.ILLEGAL_ACTION_PENETRATION: "A",
    FailureCategory.BELIEF_UPDATE_ERROR: "A",
    FailureCategory.HARMFUL_BELIEF_OVERRIDE: "A",
    # B: agent output, prompt behavior, and strategy mistakes.
    FailureCategory.JSON_PARSE_ERROR: "B",
    FailureCategory.CANONICALIZER_ERROR: "B",
    FailureCategory.FALLBACK_OVERUSED: "B",
    FailureCategory.ROLE_LEAK: "B",
    FailureCategory.META_AI_LEAK: "B",
    FailureCategory.COT_LEAK: "B",
    FailureCategory.TIMEOUT_OR_RATE_LIMIT: "B",
    FailureCategory.BAD_VOTE: "B",
    FailureCategory.LATE_CLAIM: "B",
    FailureCategory.FALSE_CLAIM_FAILED: "B",
    FailureCategory.OVER_DEFENSE: "B",
    FailureCategory.HARMFUL_BUSSING: "B",
    FailureCategory.MISSED_SAVE: "B",
    FailureCategory.WRONG_POISON: "B",
    FailureCategory.HUNTER_WRONG_SHOT: "B",
    # C: context construction, visibility, and private information delivery.
    FailureCategory.CONTEXT_LEAK: "C",
    FailureCategory.MISSING_PRIVATE_INFO: "C",
    FailureCategory.CONTEXT_TRUNCATION_ERROR: "C",
}


def route(category: FailureCategory) -> Owner:
    """Return the owner responsible for a failure category.

    Unknown values are rejected instead of silently defaulting, so taxonomy
    changes cannot bypass the routing table.
    """
    if not isinstance(category, FailureCategory):
        raise ValueError(f"unknown failure category: {category!r}")
    try:
        return _CATEGORY_OWNER[category]
    except KeyError as exc:
        raise ValueError(f"unrouted failure category: {category!r}") from exc


def routing_table() -> dict[FailureCategory, Owner]:
    """Return a copy for tests and reporting."""
    return dict(_CATEGORY_OWNER)
