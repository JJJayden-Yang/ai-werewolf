import json
from pathlib import Path

from contracts import BeliefState


def test_belief_state_v2_fixture_validates():
    path = Path(__file__).resolve().parent / "fixtures" / "belief_state_v2.json"
    belief = BeliefState.model_validate(json.loads(path.read_text(encoding="utf-8")))

    assert belief.game_id == "fixture_belief_v2"
    assert set(belief.beliefs) == {"P1", "P2", "P3", "P4", "P5", "P6"}
