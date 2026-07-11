"""Phase 3 静态高级策略库：frontmatter / library / scene_detector / selector 单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_policy.advanced_strategy import scene_detector
from agent_policy.advanced_strategy.frontmatter import parse_markdown_frontmatter
from agent_policy.advanced_strategy.scene_detector import (
    ENDGAME_ALIVE_THRESHOLD,
    TAG_DAY1_PEACEFUL_NIGHT,
    TAG_ENDGAME_CLOSE,
    TAG_HUNTER_SHOOT,
    TAG_SEER_CLASH,
    TAG_TIE_REVOTE,
    TAG_UNDER_ACCUSATION,
    TAG_WITCH_POISON,
    TAG_WITCH_SAVE,
)
from agent_policy.advanced_strategy.strategy_library import StrategyLibrary, StrategySnippet
from agent_policy.advanced_strategy.strategy_selector import StrategySelector
from contracts import (
    ActionType,
    AgentContext,
    ClaimedAlignment,
    ClaimResult,
    EventType,
    Phase,
    PlayerStatus,
    PublicEvent,
    Role,
    VisiblePlayer,
)


def _ctx(
    *,
    role: Role = Role.VILLAGER,
    phase: Phase = Phase.DAY_DISCUSSION,
    alive: int = 9,
    allowed: list[ActionType] | None = None,
    tie: list[str] | None = None,
    rnd: int = 2,
    seer_claimers: list[str] | None = None,
    accused: bool = False,
) -> AgentContext:
    players = [
        VisiblePlayer(
            player_id=f"P{i}",
            status=PlayerStatus.ALIVE if i <= alive else PlayerStatus.DEAD,
        )
        for i in range(1, 10)
    ]
    public_events = [
        PublicEvent(
            event_id=f"e{idx}",
            round=rnd,
            phase=Phase.DAY_DISCUSSION,
            event_type=EventType.SPEECH,
            actor=actor,
            role_claim=Role.SEER,
        )
        for idx, actor in enumerate(seer_claimers or [])
    ]
    if accused:
        # 有人公开把 P1（agent_id）查杀成狼
        public_events.append(
            PublicEvent(
                event_id="acc",
                round=rnd,
                phase=Phase.DAY_DISCUSSION,
                event_type=EventType.SPEECH,
                actor="P9",
                role_claim=Role.SEER,
                claim_result=ClaimResult(target="P1", claimed_alignment=ClaimedAlignment.WEREWOLF),
            )
        )
    return AgentContext(
        game_id="g",
        agent_id="P1",
        role=role,
        round=rnd,
        phase=phase,
        visible_players=players,
        public_events=public_events,
        allowed_actions=allowed or [ActionType.SPEAK],
        tie_candidates=tie or [],
    )


# --------------------------------------------------------------------------- frontmatter
class TestFrontmatter:
    def test_parses_meta_and_body(self, tmp_path: Path):
        p = tmp_path / "s.md"
        p.write_text("---\nid: x\npriority: 5\n---\n# Title\nbody line", encoding="utf-8")
        meta, body = parse_markdown_frontmatter(p)
        assert meta == {"id": "x", "priority": 5}
        assert body == "# Title\nbody line"

    def test_no_frontmatter_returns_empty_meta(self, tmp_path: Path):
        p = tmp_path / "s.md"
        p.write_text("# just content\nno meta", encoding="utf-8")
        meta, body = parse_markdown_frontmatter(p)
        assert meta == {}
        assert body == "# just content\nno meta"

    def test_non_mapping_frontmatter_raises(self, tmp_path: Path):
        p = tmp_path / "s.md"
        p.write_text("---\n- just\n- a list\n---\nbody", encoding="utf-8")
        with pytest.raises(ValueError):
            parse_markdown_frontmatter(p)


# --------------------------------------------------------------------------- library
class TestStrategyLibrary:
    def test_real_dir_loads_expected_snippets(self):
        lib = StrategyLibrary()
        ids = sorted(s.id for s in lib.snippets)
        assert ids == [
            "day1_peaceful_night",
            "endgame_close",
            "hunter_shoot",
            "seer_clash",
            "tie_revote",
            "under_accusation",
            "witch_poison",
            "witch_save",
        ]

    def test_snippet_fields_parsed(self):
        lib = StrategyLibrary()
        hunter = next(s for s in lib.snippets if s.id == "hunter_shoot")
        assert hunter.role_scope == "hunter"
        assert hunter.phase_scope == ("HUNTER_SHOOT",)
        assert "hunter_shoot" in hunter.scene_tags
        assert hunter.priority == 80
        assert "以后者为准" in hunter.text  # 硬约束尾

    def test_missing_dir_yields_empty(self, tmp_path: Path):
        lib = StrategyLibrary(snippets_dir=tmp_path / "nope")
        assert len(lib) == 0

    def test_md_without_id_is_skipped(self, tmp_path: Path):
        (tmp_path / "readme.md").write_text("# not a snippet", encoding="utf-8")
        (tmp_path / "real.md").write_text("---\nid: real\n---\nbody", encoding="utf-8")
        lib = StrategyLibrary(snippets_dir=tmp_path)
        assert [s.id for s in lib.snippets] == ["real"]

    def test_duplicate_id_raises(self, tmp_path: Path):
        (tmp_path / "a.md").write_text("---\nid: dup\n---\nA", encoding="utf-8")
        (tmp_path / "b.md").write_text("---\nid: dup\n---\nB", encoding="utf-8")
        with pytest.raises(ValueError):
            StrategyLibrary(snippets_dir=tmp_path)


# --------------------------------------------------------------------------- scene_detector
class TestSceneDetector:
    def test_tie_revote_on_nonempty_candidates(self):
        assert TAG_TIE_REVOTE in scene_detector.detect(
            _ctx(phase=Phase.DAY_TIE_REVOTE, tie=["P3", "P5"], allowed=[ActionType.VOTE])
        )

    def test_hunter_shoot_on_allowed(self):
        assert TAG_HUNTER_SHOOT in scene_detector.detect(
            _ctx(role=Role.HUNTER, phase=Phase.HUNTER_SHOOT, allowed=[ActionType.HUNTER_SHOOT])
        )

    def test_witch_poison_on_allowed(self):
        assert TAG_WITCH_POISON in scene_detector.detect(
            _ctx(role=Role.WITCH, phase=Phase.NIGHT_WITCH, allowed=[ActionType.POISON, ActionType.SKIP])
        )

    def test_endgame_threshold_boundary(self):
        assert TAG_ENDGAME_CLOSE in scene_detector.detect(_ctx(alive=ENDGAME_ALIVE_THRESHOLD))
        assert TAG_ENDGAME_CLOSE not in scene_detector.detect(_ctx(alive=ENDGAME_ALIVE_THRESHOLD + 1))

    def test_day1_peaceful_night_fires_on_round1_all_alive(self):
        # 首日(round=1) + 白天讨论 + 全员存活 → 命中
        assert TAG_DAY1_PEACEFUL_NIGHT in scene_detector.detect(
            _ctx(phase=Phase.DAY_DISCUSSION, alive=9, rnd=1)
        )

    def test_day1_peaceful_night_silent_when_someone_died(self):
        # 有人出局(非平安夜) → 不命中
        assert TAG_DAY1_PEACEFUL_NIGHT not in scene_detector.detect(
            _ctx(phase=Phase.DAY_DISCUSSION, alive=8, rnd=1)
        )

    def test_day1_peaceful_night_silent_after_day1(self):
        # 第二天即使平安过 → 不命中（只限首日）
        assert TAG_DAY1_PEACEFUL_NIGHT not in scene_detector.detect(
            _ctx(phase=Phase.DAY_DISCUSSION, alive=9, rnd=2)
        )

    def test_witch_save_on_allowed(self):
        assert TAG_WITCH_SAVE in scene_detector.detect(
            _ctx(role=Role.WITCH, phase=Phase.NIGHT_WITCH, allowed=[ActionType.SAVE, ActionType.SKIP])
        )

    def test_witch_save_silent_without_save_action(self):
        assert TAG_WITCH_SAVE not in scene_detector.detect(
            _ctx(role=Role.WITCH, phase=Phase.NIGHT_WITCH, allowed=[ActionType.POISON, ActionType.SKIP])
        )

    def test_seer_clash_fires_on_two_distinct_seer_claims(self):
        # 两个不同 actor 各自跳预言家 → 对跳命中
        assert TAG_SEER_CLASH in scene_detector.detect(_ctx(seer_claimers=["P3", "P7"]))

    def test_seer_clash_silent_on_single_claim(self):
        # 只有一个预言家声明 → 不算对跳
        assert TAG_SEER_CLASH not in scene_detector.detect(_ctx(seer_claimers=["P3"]))

    def test_seer_clash_dedupes_same_actor(self):
        # 同一个 actor 跳两次（两条 SPEECH）仍只算一个声明者 → 不命中
        assert TAG_SEER_CLASH not in scene_detector.detect(_ctx(seer_claimers=["P3", "P3"]))

    def test_under_accusation_fires_when_claimed_werewolf(self):
        # 有人公开把自己（P1）查杀成狼 → 命中
        assert TAG_UNDER_ACCUSATION in scene_detector.detect(_ctx(accused=True))

    def test_under_accusation_silent_without_accusation(self):
        # 只有别的预言家声明、没人查杀自己 → 不命中
        assert TAG_UNDER_ACCUSATION not in scene_detector.detect(_ctx(seer_claimers=["P3", "P7"]))

    def test_no_tags_in_plain_discussion(self):
        assert scene_detector.detect(_ctx(allowed=[ActionType.SPEAK])) == set()


# --------------------------------------------------------------------------- selector
class TestStrategySelector:
    def test_hunter_scene_selects_hunter_snippet(self):
        sel = StrategySelector()
        out = sel.select(_ctx(role=Role.HUNTER, phase=Phase.HUNTER_SHOOT, allowed=[ActionType.HUNTER_SHOOT]))
        assert [s.id for s in out] == ["hunter_shoot"]

    def test_generic_tie_matches_any_role(self):
        sel = StrategySelector()
        out = sel.select(_ctx(role=Role.WEREWOLF, phase=Phase.DAY_TIE_REVOTE, tie=["P3", "P5"], allowed=[ActionType.VOTE]))
        assert [s.id for s in out] == ["tie_revote"]

    def test_no_trigger_returns_empty(self):
        sel = StrategySelector()
        assert sel.select(_ctx(allowed=[ActionType.SPEAK])) == []

    def test_priority_desc_and_cap(self):
        # 两条都命中（witch_poison prio80 + endgame_close prio60），按优先级降序
        sel = StrategySelector()
        out = sel.select(
            _ctx(role=Role.WITCH, phase=Phase.NIGHT_WITCH, alive=4, allowed=[ActionType.POISON, ActionType.SKIP])
        )
        assert [s.id for s in out] == ["witch_poison", "endgame_close"]

    def test_max_snippets_cap(self):
        sel = StrategySelector(max_snippets=1)
        out = sel.select(
            _ctx(role=Role.WITCH, phase=Phase.NIGHT_WITCH, alive=4, allowed=[ActionType.POISON])
        )
        assert [s.id for s in out] == ["witch_poison"]  # 只取最高优先级一条

    def test_budget_drops_low_priority(self):
        # max_chars 很小：第一条（高优先级）进，第二条因预算被跳过
        lib = StrategyLibrary()
        sel = StrategySelector(lib, max_snippets=3, max_chars=10)
        out = sel.select(
            _ctx(role=Role.WITCH, phase=Phase.NIGHT_WITCH, alive=4, allowed=[ActionType.POISON])
        )
        assert [s.id for s in out] == ["witch_poison"]

    def test_phase_scope_filters_out_wrong_phase(self):
        # hunter_shoot 限 HUNTER_SHOOT phase；构造 tag 命中但 phase 不符的情况验证 phase 过滤。
        # 直接用自建 library 注入一个 phase 受限片段更干净：
        snippet = StrategySnippet(
            id="x", role_scope="generic", phase_scope=("HUNTER_SHOOT",),
            scene_tags=frozenset({TAG_ENDGAME_CLOSE}), priority=50, text="t",
        )
        lib = StrategyLibrary(snippets_dir=Path("/nonexistent"))
        lib._snippets.append(snippet)  # type: ignore[attr-defined]
        sel = StrategySelector(lib)
        # endgame 触发但当前 phase=DAY_DISCUSSION ∉ phase_scope → 不选
        assert sel.select(_ctx(alive=4)) == []
