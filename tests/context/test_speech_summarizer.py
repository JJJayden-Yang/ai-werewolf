"""SpeechSummarizer 测试。

覆盖：
- 按 round 分组
- Daybreak / Speech / Vote / Result 各类拼装
- exclude_round 排除当前轮
- 自报角色 / 查验结果 摘取
"""

from __future__ import annotations

from contracts import (
    ClaimResult,
    ClaimedAlignment,
    EventType,
    Phase,
    PublicEvent,
    Role,
)

from context.speech_summarizer import SpeechSummarizer


def _ev(
    event_type: EventType,
    *,
    round_num: int = 1,
    actor: str | None = None,
    target: str | None = None,
    public_message: str | None = None,
    role_claim: Role | None = None,
    claim_result: ClaimResult | None = None,
    summary: str | None = None,
) -> PublicEvent:
    return PublicEvent(
        event_id=f"evt_{round_num}_{event_type.value}_{actor or 'sys'}",
        round=round_num,
        phase=Phase.DAY_DISCUSSION,
        event_type=event_type,
        actor=actor,
        target=target,
        public_message=public_message,
        role_claim=role_claim,
        claim_result=claim_result,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Round-level summarization
# ---------------------------------------------------------------------------


class TestSummarizeRound:
    def setup_method(self):
        self.s = SpeechSummarizer()

    def test_daybreak_from_night_kill(self):
        events = [_ev(EventType.NIGHT_KILL_ANNOUNCED, target="P4")]
        facts = self.s.summarize_round(events)
        assert any("P4 died by night_kill" in f for f in facts)
        assert any("Daybreak" in f for f in facts)

    def test_speech_with_self_claim_and_check(self):
        events = [
            _ev(
                EventType.SPEECH,
                actor="P1",
                role_claim=Role.SEER,
                claim_result=ClaimResult(
                    target="P3", claimed_alignment=ClaimedAlignment.WEREWOLF
                ),
            ),
        ]
        facts = self.s.summarize_round(events)
        line = next(f for f in facts if "Speech" in f)
        assert "P1 self_claim seer" in line
        assert "check P3 werewolf" in line

    def test_speech_with_only_role_claim(self):
        events = [_ev(EventType.SPEECH, actor="P2", role_claim=Role.VILLAGER)]
        facts = self.s.summarize_round(events)
        line = next(f for f in facts if "Speech" in f)
        assert "P2 self_claim villager" in line

    def test_plain_speech(self):
        events = [
            _ev(EventType.SPEECH, actor="P3", public_message="I think P1 is sus.")
        ]
        facts = self.s.summarize_round(events)
        line = next(f for f in facts if "Speech" in f)
        assert "P3 spoke" in line
        # 原文不应该出现在 Fact Stream 里
        assert "I think P1 is sus" not in line

    def test_vote_aggregation(self):
        events = [
            _ev(EventType.VOTE_CAST, actor="P1", target="P3"),
            _ev(EventType.VOTE_CAST, actor="P2", target="P3"),
            _ev(EventType.VOTE_CAST, actor="P3", target="P1"),
        ]
        facts = self.s.summarize_round(events)
        vote_line = next(f for f in facts if "Vote" in f)
        assert "P1->P3" in vote_line
        assert "P2->P3" in vote_line
        assert "P3->P1" in vote_line

    def test_exile_result(self):
        events = [_ev(EventType.EXILE, target="P3")]
        facts = self.s.summarize_round(events)
        result_line = next(f for f in facts if "Result" in f)
        assert "P3 executed" in result_line

    def test_tie_detected(self):
        events = [_ev(EventType.TIE_DETECTED)]
        facts = self.s.summarize_round(events)
        result_line = next(f for f in facts if "Result" in f)
        assert "vote tied" in result_line

    def test_hunter_shot_with_target(self):
        events = [_ev(EventType.HUNTER_SHOT, actor="P5", target="P1")]
        facts = self.s.summarize_round(events)
        hunter_line = next(f for f in facts if "Hunter" in f)
        assert "P5->P1" in hunter_line

    def test_hunter_passed(self):
        events = [_ev(EventType.HUNTER_SHOT, actor="P5", target=None)]
        facts = self.s.summarize_round(events)
        hunter_line = next(f for f in facts if "Hunter" in f)
        assert "P5 passed" in hunter_line

    def test_game_over(self):
        events = [_ev(EventType.GAME_OVER, summary="villagers win")]
        facts = self.s.summarize_round(events)
        line = next(f for f in facts if "game over" in f.lower())
        assert "villagers win" in line

    def test_combined_round(self):
        events = [
            _ev(EventType.NIGHT_KILL_ANNOUNCED, target="P4"),
            _ev(EventType.SPEECH, actor="P1", role_claim=Role.SEER),
            _ev(EventType.SPEECH, actor="P2"),
            _ev(EventType.VOTE_CAST, actor="P1", target="P3"),
            _ev(EventType.VOTE_CAST, actor="P2", target="P3"),
            _ev(EventType.EXILE, target="P3"),
        ]
        facts = self.s.summarize_round(events)
        # 4 行：Daybreak / Speech / Vote / Result
        assert len(facts) == 4

    def test_empty_round_returns_empty(self):
        assert self.s.summarize_round([]) == []


# ---------------------------------------------------------------------------
# Multi-round summarization
# ---------------------------------------------------------------------------


class TestSummarizeByRound:
    def setup_method(self):
        self.s = SpeechSummarizer()

    def test_groups_by_round(self):
        events = [
            _ev(EventType.NIGHT_KILL_ANNOUNCED, round_num=1, target="P4"),
            _ev(EventType.EXILE, round_num=1, target="P3"),
            _ev(EventType.NIGHT_KILL_ANNOUNCED, round_num=2, target="P1"),
            _ev(EventType.EXILE, round_num=2, target="P5"),
        ]
        result = self.s.summarize_by_round(events)
        assert len(result) == 2
        rounds = [r.round for r in result]
        assert rounds == [1, 2]

    def test_exclude_round_filters_out(self):
        events = [
            _ev(EventType.SPEECH, round_num=1, actor="P1"),
            _ev(EventType.SPEECH, round_num=2, actor="P2"),
            _ev(EventType.SPEECH, round_num=3, actor="P3"),
        ]
        result = self.s.summarize_by_round(events, exclude_round=2)
        assert len(result) == 2
        assert {r.round for r in result} == {1, 3}

    def test_excluded_round_completely_skipped(self):
        events = [_ev(EventType.SPEECH, round_num=1, actor="P1")]
        result = self.s.summarize_by_round(events, exclude_round=1)
        assert result == []
