import pytest

from contracts import FailureCategory
from evaluation.problem_routing import route, routing_table


def test_every_failure_category_has_owner():
    table = routing_table()

    assert set(table) == set(FailureCategory)
    assert all(owner in {"A", "B", "C"} for owner in table.values())


def test_known_failure_categories_route_to_expected_owners():
    assert route(FailureCategory.BELIEF_UPDATE_ERROR) == "A"
    assert route(FailureCategory.HARMFUL_BELIEF_OVERRIDE) == "A"
    assert route(FailureCategory.PHASE_STUCK) == "A"
    assert route(FailureCategory.BAD_VOTE) == "B"
    assert route(FailureCategory.MISSED_SAVE) == "B"
    assert route(FailureCategory.WRONG_POISON) == "B"
    assert route(FailureCategory.HARMFUL_BUSSING) == "B"
    assert route(FailureCategory.OVER_DEFENSE) == "B"
    assert route(FailureCategory.LATE_CLAIM) == "B"
    assert route(FailureCategory.CONTEXT_LEAK) == "C"
    assert route(FailureCategory.MISSING_PRIVATE_INFO) == "C"
    assert route(FailureCategory.CONTEXT_TRUNCATION_ERROR) == "C"


def test_unknown_failure_category_raises():
    with pytest.raises(ValueError):
        route("new_category")  # type: ignore[arg-type]
