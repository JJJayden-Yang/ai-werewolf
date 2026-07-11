"""PromptTemplateLoader 测试。

覆盖：
- ``load`` 五个角色 prompt 文件正确读取
- ``load_for_role`` 便利 helper
- ``prompt_version_id`` 格式校验（无冒号 / 空段）
- 文件不存在抛 ``PromptTemplateNotFoundError``
- 自定义 ``prompts_dir`` 路径
- ``render`` 输出 OpenAI messages 格式（system + user）
- 序列化 ``AgentContext`` 包含 prompt 文件点名引用的字段
  （visible_players / private_events.teammates / SEER_CHECK_RESULT / tie_candidates / allowed_actions）
- 端到端：PromptTemplateLoader + FakeLLMProvider + ActionParser → AgentAction
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from contracts import (
    ActionType,
    AgentContext,
    ClaimedAlignment,
    ClaimRecord,
    ClaimResult,
    EventType,
    Phase,
    PlayerStatus,
    PrivateEvent,
    PublicEvent,
    Role,
    Visibility,
    VisiblePlayer,
    VoteRecord,
)

from agent_runtime.action_parser import ActionParser
from agent_runtime.exceptions import PromptTemplateNotFoundError
from agent_runtime.llm_provider import FakeLLMProvider
from agent_runtime.prompt_template_loader import PromptTemplateLoader
from agent_runtime.types import PromptTemplate
from tests.fixtures.agent_contexts import (
    day_discussion_context,
    hunter_shoot_context,
    seer_context,
    tie_revote_context,
    vote_context,
    werewolf_context,
    witch_context,
)


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------


class TestLoadAllRoles:
    @pytest.fixture
    def loader(self) -> PromptTemplateLoader:
        return PromptTemplateLoader()

    @pytest.mark.parametrize("role_value", ["werewolf", "seer", "witch", "hunter", "villager"])
    def test_load_for_role_returns_non_empty_system_prompt(self, loader: PromptTemplateLoader, role_value: str):
        template = loader.load_for_role(role_value)
        assert isinstance(template, PromptTemplate)
        assert template.prompt_version_id == f"{role_value}:v0_free_llm"
        assert template.role == role_value
        assert template.system_prompt.strip(), "system prompt should not be empty"
        assert "v0" in template.system_prompt.lower() or role_value in template.system_prompt.lower()
        # 文件路径写入 metadata，调试方便
        assert "source_path" in template.metadata

    def test_load_explicit_id(self, loader: PromptTemplateLoader):
        t = loader.load("werewolf:v0_free_llm")
        assert t.role == "werewolf"
        assert "狼人" in t.system_prompt

    def test_load_strips_trailing_whitespace(self, loader: PromptTemplateLoader):
        t = loader.load("seer:v0_free_llm")
        # 文件可能有末尾换行，加载后应被 strip
        assert not t.system_prompt.endswith("\n\n\n")


class TestLoadErrors:
    def setup_method(self) -> None:
        self.loader = PromptTemplateLoader()

    def test_missing_colon_raises_value_error(self):
        with pytest.raises(ValueError, match="must be"):
            self.loader.load("werewolf_v0_free_llm")

    def test_empty_role_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            self.loader.load(":v0_free_llm")

    def test_empty_template_name_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            self.loader.load("werewolf:")

    def test_non_string_raises_value_error(self):
        with pytest.raises(ValueError):
            self.loader.load(None)  # type: ignore[arg-type]

    def test_unknown_role_raises_not_found(self):
        with pytest.raises(PromptTemplateNotFoundError) as exc_info:
            self.loader.load("ghost:v0_free_llm")
        assert exc_info.value.prompt_version_id == "ghost:v0_free_llm"
        assert "ghost" in exc_info.value.path

    def test_unknown_template_name_raises_not_found(self):
        with pytest.raises(PromptTemplateNotFoundError):
            self.loader.load("werewolf:does_not_exist")


class TestCustomPromptsDir:
    def test_custom_dir_loads_template(self, tmp_path: Path):
        role_dir = tmp_path / "werewolf"
        role_dir.mkdir()
        (role_dir / "v0_free_llm.md").write_text("# custom wolf strategy\n\nbe sneaky.", encoding="utf-8")

        loader = PromptTemplateLoader(prompts_dir=tmp_path)
        t = loader.load("werewolf:v0_free_llm")
        assert "be sneaky" in t.system_prompt
        assert t.metadata["source_path"].endswith("v0_free_llm.md")

    def test_string_path_also_works(self, tmp_path: Path):
        role_dir = tmp_path / "seer"
        role_dir.mkdir()
        (role_dir / "v1.md").write_text("seer v1", encoding="utf-8")

        loader = PromptTemplateLoader(prompts_dir=str(tmp_path))
        t = loader.load("seer:v1")
        assert t.system_prompt == "seer v1"


# ---------------------------------------------------------------------------
# shared/output_contract.md 全局输出契约（B 5/26 18:47 推的设计）
# ---------------------------------------------------------------------------


class TestSharedOutputContract:
    """shared/output_contract.md 存在时，自动 prepend 到 role prompt 当 system_prompt。"""

    def test_shared_contract_prepended_when_present(self, tmp_path: Path):
        # 同时给 shared 和 role 两个文件
        shared_dir = tmp_path / "shared"
        shared_dir.mkdir()
        (shared_dir / "output_contract.md").write_text(
            "# v0 LLM Global Output Contract\n\n所有角色都按这个 JSON shape 输出。",
            encoding="utf-8",
        )
        role_dir = tmp_path / "werewolf"
        role_dir.mkdir()
        (role_dir / "v0_free_llm.md").write_text(
            "# 狼人 v0 Prompt\n\n你是狼人。", encoding="utf-8"
        )

        loader = PromptTemplateLoader(prompts_dir=tmp_path)
        t = loader.load("werewolf:v0_free_llm")
        # shared 在前 + role 在后，用空行隔开
        assert "v0 LLM Global Output Contract" in t.system_prompt
        assert "你是狼人" in t.system_prompt
        assert t.system_prompt.index("Global Output Contract") < t.system_prompt.index("你是狼人")
        # metadata 同时记录 shared_path + source_path
        assert "shared_path" in t.metadata
        assert "source_path" in t.metadata
        assert t.metadata["shared_path"].endswith("output_contract.md")

    def test_shared_absent_falls_back_to_role_only(self, tmp_path: Path):
        # 只给 role 不给 shared（向后兼容：v2.2 之前的 fixture / 测试场景）
        role_dir = tmp_path / "seer"
        role_dir.mkdir()
        (role_dir / "v0_free_llm.md").write_text(
            "# 预言家 v0\n\n你是预言家。", encoding="utf-8"
        )

        loader = PromptTemplateLoader(prompts_dir=tmp_path)
        t = loader.load("seer:v0_free_llm")
        # 只有 role 内容，没有 shared
        assert t.system_prompt == "# 预言家 v0\n\n你是预言家。"
        assert "shared_path" not in t.metadata

    def test_render_system_message_contains_shared_when_present(self, tmp_path: Path):
        shared_dir = tmp_path / "shared"
        shared_dir.mkdir()
        (shared_dir / "output_contract.md").write_text(
            "GLOBAL_CONTRACT_MARKER", encoding="utf-8"
        )
        role_dir = tmp_path / "villager"
        role_dir.mkdir()
        (role_dir / "v0_free_llm.md").write_text("ROLE_CONTENT_MARKER", encoding="utf-8")

        loader = PromptTemplateLoader(prompts_dir=tmp_path)
        t = loader.load("villager:v0_free_llm")
        msgs = loader.render(t, vote_context())
        sys_msg = msgs[0]["content"]
        # 双内容都在 system 消息里，顺序：shared 在前
        assert "GLOBAL_CONTRACT_MARKER" in sys_msg
        assert "ROLE_CONTENT_MARKER" in sys_msg
        assert sys_msg.index("GLOBAL_CONTRACT_MARKER") < sys_msg.index("ROLE_CONTENT_MARKER")


class TestGameKnowledgeLayer:
    """shared/game_knowledge.md 存在时，注入到 output_contract 之后、role 之前。"""

    def _write_three_layers(self, tmp_path: Path) -> PromptTemplateLoader:
        shared_dir = tmp_path / "shared"
        shared_dir.mkdir()
        (shared_dir / "output_contract.md").write_text("CONTRACT_MARKER", encoding="utf-8")
        (shared_dir / "game_knowledge.md").write_text("KNOWLEDGE_MARKER", encoding="utf-8")
        role_dir = tmp_path / "werewolf"
        role_dir.mkdir()
        (role_dir / "v0_free_llm.md").write_text("ROLE_MARKER", encoding="utf-8")
        return PromptTemplateLoader(prompts_dir=tmp_path)

    def test_game_knowledge_injected_between_contract_and_role(self, tmp_path: Path):
        loader = self._write_three_layers(tmp_path)
        t = loader.load("werewolf:v0_free_llm")
        sp = t.system_prompt
        assert "CONTRACT_MARKER" in sp
        assert "KNOWLEDGE_MARKER" in sp
        assert "ROLE_MARKER" in sp
        # 顺序：output_contract < game_knowledge < role
        assert sp.index("CONTRACT_MARKER") < sp.index("KNOWLEDGE_MARKER") < sp.index("ROLE_MARKER")
        assert t.metadata["game_knowledge_path"].endswith("game_knowledge.md")

    def test_game_knowledge_absent_is_skipped(self, tmp_path: Path):
        # 只给 contract + role，不给 game_knowledge（向后兼容）
        shared_dir = tmp_path / "shared"
        shared_dir.mkdir()
        (shared_dir / "output_contract.md").write_text("CONTRACT_MARKER", encoding="utf-8")
        role_dir = tmp_path / "seer"
        role_dir.mkdir()
        (role_dir / "v0_free_llm.md").write_text("ROLE_MARKER", encoding="utf-8")

        loader = PromptTemplateLoader(prompts_dir=tmp_path)
        t = loader.load("seer:v0_free_llm")
        assert t.system_prompt == "CONTRACT_MARKER\n\nROLE_MARKER"
        assert "game_knowledge_path" not in t.metadata

    def test_game_knowledge_without_contract_still_prepends_role(self, tmp_path: Path):
        # 只给 game_knowledge + role（无 output_contract）：knowledge 仍在 role 之前
        shared_dir = tmp_path / "shared"
        shared_dir.mkdir()
        (shared_dir / "game_knowledge.md").write_text("KNOWLEDGE_MARKER", encoding="utf-8")
        role_dir = tmp_path / "villager"
        role_dir.mkdir()
        (role_dir / "v0_free_llm.md").write_text("ROLE_MARKER", encoding="utf-8")

        loader = PromptTemplateLoader(prompts_dir=tmp_path)
        t = loader.load("villager:v0_free_llm")
        assert t.system_prompt == "KNOWLEDGE_MARKER\n\nROLE_MARKER"
        assert "shared_path" not in t.metadata
        assert t.metadata["game_knowledge_path"].endswith("game_knowledge.md")

    def test_v1_fallback_includes_game_knowledge(self, tmp_path: Path):
        # v1 empty-belief fallback 必须保留 game_knowledge 层（不能像旧实现只取 contract）
        shared_dir = tmp_path / "shared"
        shared_dir.mkdir()
        (shared_dir / "output_contract.md").write_text("CONTRACT_MARKER", encoding="utf-8")
        (shared_dir / "game_knowledge.md").write_text("KNOWLEDGE_MARKER", encoding="utf-8")
        (shared_dir / "v1_belief_guidance.md").write_text("BELIEF_MARKER", encoding="utf-8")
        role_dir = tmp_path / "villager"
        role_dir.mkdir()
        (role_dir / "v0_free_llm.md").write_text("ROLE_MARKER", encoding="utf-8")

        loader = PromptTemplateLoader(prompts_dir=tmp_path)
        t = loader.load("villager:v1_belief_llm")
        fallback = t.metadata["system_prompt_empty_belief_fallback"]
        assert "CONTRACT_MARKER" in fallback
        assert "KNOWLEDGE_MARKER" in fallback
        assert "ROLE_MARKER" in fallback
        # fallback 是"无 belief 词"版本：不含 belief guidance
        assert "BELIEF_MARKER" not in fallback

    def test_real_prompts_dir_includes_game_knowledge(self):
        # 真实 prompts_dir 已带 game_knowledge.md：每个角色 system 都应包含其内容
        loader = PromptTemplateLoader()
        t = loader.load("seer:v0_free_llm")
        assert "game_knowledge_path" in t.metadata
        assert "通用对局知识" in t.system_prompt


class TestStrategyInjection:
    """render(extra_system_sections=...) 把策略片段包进硬约束框拼到 system 末尾。"""

    def test_no_sections_leaves_system_unchanged(self, tmp_path: Path):
        loader = _role_only_loader(tmp_path, "villager", "ROLE_MARKER")
        t = loader.load("villager:v0_free_llm")
        msgs = loader.render(t, vote_context())
        assert "参考打法" not in msgs[0]["content"]
        # 与不传参数完全一致
        assert loader.render(t, vote_context(), extra_system_sections=None) == msgs

    def test_sections_wrapped_in_hard_constraint_frame_after_base(self, tmp_path: Path):
        loader = _role_only_loader(tmp_path, "villager", "ROLE_MARKER")
        t = loader.load("villager:v0_free_llm")
        msgs = loader.render(t, vote_context(), extra_system_sections=["SNIP_A", "SNIP_B"])
        sys = msgs[0]["content"]
        assert "ROLE_MARKER" in sys
        assert "参考打法（非硬规则）" in sys
        assert "一律以后者为准" in sys
        assert "SNIP_A" in sys and "SNIP_B" in sys
        # 策略框在 base system（role）之后
        assert sys.index("ROLE_MARKER") < sys.index("参考打法")
        assert sys.index("参考打法") < sys.index("SNIP_A") < sys.index("SNIP_B")

    def test_empty_strings_filtered(self, tmp_path: Path):
        loader = _role_only_loader(tmp_path, "villager", "ROLE_MARKER")
        t = loader.load("villager:v0_free_llm")
        msgs = loader.render(t, vote_context(), extra_system_sections=["", "  "])
        assert "参考打法" not in msgs[0]["content"]


def _role_only_loader(tmp_path: Path, role: str, marker: str) -> PromptTemplateLoader:
    role_dir = tmp_path / role
    role_dir.mkdir()
    (role_dir / "v0_free_llm.md").write_text(marker, encoding="utf-8")
    return PromptTemplateLoader(prompts_dir=tmp_path)


class TestSoulLayer:
    """souls/<soul_id>.md 由 loader.soul_id 开启，注入到 role 之后、belief_guidance 之前。"""

    def _write_dirs(self, tmp_path: Path) -> None:
        shared_dir = tmp_path / "shared"
        shared_dir.mkdir()
        (shared_dir / "output_contract.md").write_text("CONTRACT_MARKER", encoding="utf-8")
        (shared_dir / "game_knowledge.md").write_text("KNOWLEDGE_MARKER", encoding="utf-8")
        (shared_dir / "v1_belief_guidance.md").write_text("BELIEF_MARKER", encoding="utf-8")
        role_dir = tmp_path / "werewolf"
        role_dir.mkdir()
        (role_dir / "v0_free_llm.md").write_text("ROLE_MARKER", encoding="utf-8")
        souls_dir = tmp_path / "souls"
        souls_dir.mkdir()
        (souls_dir / "cautious.md").write_text("SOUL_MARKER", encoding="utf-8")

    def test_soul_none_is_backward_compatible(self, tmp_path: Path):
        # soul_id 默认 None：不注入 soul 层，system 与无 soul 时完全一致
        self._write_dirs(tmp_path)
        loader = PromptTemplateLoader(prompts_dir=tmp_path)  # 无 soul_id
        t = loader.load("werewolf:v0_free_llm")
        assert "SOUL_MARKER" not in t.system_prompt
        assert "soul_id" not in t.metadata
        assert "soul_path" not in t.metadata

    def test_soul_injected_after_role(self, tmp_path: Path):
        self._write_dirs(tmp_path)
        loader = PromptTemplateLoader(prompts_dir=tmp_path, soul_id="cautious")
        t = loader.load("werewolf:v0_free_llm")
        sp = t.system_prompt
        # 顺序：contract < knowledge < role < soul
        assert sp.index("CONTRACT_MARKER") < sp.index("KNOWLEDGE_MARKER") < sp.index("ROLE_MARKER") < sp.index("SOUL_MARKER")
        assert t.metadata["soul_id"] == "cautious"
        assert t.metadata["soul_path"].endswith("cautious.md")

    def test_missing_soul_file_raises(self, tmp_path: Path):
        self._write_dirs(tmp_path)
        loader = PromptTemplateLoader(prompts_dir=tmp_path, soul_id="does_not_exist")
        with pytest.raises(PromptTemplateNotFoundError) as exc_info:
            loader.load("werewolf:v0_free_llm")
        assert "does_not_exist" in exc_info.value.path

    def test_soul_before_belief_guidance_in_v1(self, tmp_path: Path):
        self._write_dirs(tmp_path)
        loader = PromptTemplateLoader(prompts_dir=tmp_path, soul_id="cautious")
        t = loader.load("werewolf:v1_belief_llm")
        sp = t.system_prompt
        # 顺序：role < soul < belief_guidance
        assert sp.index("ROLE_MARKER") < sp.index("SOUL_MARKER") < sp.index("BELIEF_MARKER")

    def test_soul_kept_in_v1_empty_belief_fallback(self, tmp_path: Path):
        # 拿空 belief 的 v1 玩家仍保留人格层，但 fallback 不含 belief 词
        self._write_dirs(tmp_path)
        loader = PromptTemplateLoader(prompts_dir=tmp_path, soul_id="cautious")
        t = loader.load("werewolf:v1_belief_llm")
        fallback = t.metadata["system_prompt_empty_belief_fallback"]
        assert "SOUL_MARKER" in fallback
        assert "ROLE_MARKER" in fallback
        assert "BELIEF_MARKER" not in fallback

    @pytest.mark.parametrize("soul_id", ["default_balanced", "cautious", "aggressive", "logical"])
    def test_real_souls_load_and_carry_hard_constraint(self, soul_id: str):
        # 真实 souls/*.md 都能被加载，且都带"以角色策略/契约为准"的硬约束
        loader = PromptTemplateLoader(soul_id=soul_id)
        t = loader.load("seer:v0_free_llm")
        assert t.metadata["soul_id"] == soul_id
        assert "永远以后者为准" in t.system_prompt
        # soul 文案不得含 "belief" 关键词（避免干扰 v1 strip / fallback 逻辑）
        soul_text = Path(t.metadata["soul_path"]).read_text(encoding="utf-8")
        assert "belief" not in soul_text.lower()


class TestV1BeliefPrompt:
    def test_v1_belief_template_reuses_role_prompt_and_appends_guidance(self):
        loader = PromptTemplateLoader()
        template = loader.load("villager:v1_belief_llm")

        assert template.prompt_version_id == "villager:v1_belief_llm"
        assert template.role == "villager"
        assert "平民 v0 Free LLM Prompt" in template.system_prompt
        assert "v1 Belief Guidance" in template.system_prompt
        assert "`belief_top_suspects` 是基于可见事件递归更新出的主观判断" in template.system_prompt
        assert template.metadata["source_path"].replace("\\", "/").endswith("villager/v0_free_llm.md")
        assert template.metadata["belief_guidance_path"].replace("\\", "/").endswith("shared/v1_belief_guidance.md")

    @pytest.mark.parametrize("role_value", ["werewolf", "villager", "witch", "hunter"])
    def test_v1_belief_template_strips_v0_belief_ban(self, role_value: str):
        loader = PromptTemplateLoader()
        template = loader.load(f"{role_value}:v1_belief_llm")

        assert "不读取 `belief_state` / `belief_top_suspects`" not in template.system_prompt
        assert "不读 `belief_state` / `belief_top_suspects`" not in template.system_prompt
        assert "不注入 belief" not in template.system_prompt
        assert "v0 不读取 belief" not in template.system_prompt
        assert "允许参考 `belief_top_suspects`" in template.system_prompt

    def test_v0_prompt_does_not_include_v1_belief_guidance(self):
        loader = PromptTemplateLoader()
        template = loader.load("villager:v0_free_llm")

        assert "v1 Belief Guidance" not in template.system_prompt
        assert "允许参考 `belief_top_suspects`" not in template.system_prompt


# ---------------------------------------------------------------------------
# render — basic shape
# ---------------------------------------------------------------------------


class TestRenderShape:
    def setup_method(self) -> None:
        self.loader = PromptTemplateLoader()

    def test_render_returns_system_user_pair(self):
        t = self.loader.load_for_role("villager")
        msgs = self.loader.render(t, vote_context())
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_system_message_equals_template_text(self):
        t = self.loader.load_for_role("werewolf")
        msgs = self.loader.render(t, werewolf_context())
        assert msgs[0]["content"] == t.system_prompt.strip()

    def test_user_message_always_has_basic_block(self):
        t = self.loader.load_for_role("villager")
        ctx = vote_context()
        user = self.loader.render(t, ctx)[1]["content"]
        assert "## 当前局面" in user
        assert ctx.game_id in user
        assert ctx.agent_id in user
        assert ctx.role.value in user
        assert ctx.phase.value in user

    def test_user_message_always_has_allowed_actions(self):
        t = self.loader.load_for_role("villager")
        user = self.loader.render(t, vote_context())[1]["content"]
        assert "## allowed_actions" in user
        assert "vote" in user

    def test_user_message_always_has_output_instruction(self):
        t = self.loader.load_for_role("villager")
        user = self.loader.render(t, vote_context())[1]["content"]
        assert "## 当前阶段动作指引" in user
        assert "action_type" in user
        # shared 已说全局 JSON shape；本 block 只引用 shared, 不再独立维护 schema
        assert "AgentAction" in user  # 在引用 shared 的句子里仍然提到
        assert "v0 LLM Global Output Contract" in user


# ---------------------------------------------------------------------------
# render — context-specific serialization (prompt 文件点名引用的字段必须出现)
# ---------------------------------------------------------------------------


class TestRenderContextFields:
    def setup_method(self) -> None:
        self.loader = PromptTemplateLoader()

    def test_werewolf_teammates_appear_in_private_events_block(self):
        t = self.loader.load_for_role("werewolf")
        ctx = werewolf_context()  # teammates=["P1", "P2"], visibility=private_to_wolves
        user = self.loader.render(t, ctx)[1]["content"]
        assert "## 私密事件 (private_events)" in user
        assert "teammates=" in user
        assert "P1" in user and "P2" in user
        assert "private_to_wolves" in user

    def test_seer_check_result_appears_with_target_and_result(self):
        t = self.loader.load_for_role("seer")
        ctx = seer_context()
        user = self.loader.render(t, ctx)[1]["content"]
        assert "seer_check_result" in user
        assert "target=P1" in user
        assert "result=werewolf" in user
        assert "private_to_seer" in user

    def test_witch_context_shows_three_actions(self):
        t = self.loader.load_for_role("witch")
        ctx = witch_context()
        user = self.loader.render(t, ctx)[1]["content"]
        assert "save" in user
        assert "poison" in user
        assert "skip" in user

    def test_hunter_shoot_phase_visible(self):
        t = self.loader.load_for_role("hunter")
        ctx = hunter_shoot_context()
        user = self.loader.render(t, ctx)[1]["content"]
        assert "hunter_shoot" in user
        assert "HUNTER_SHOOT" in user or "hunter_shoot" in user.lower()

    def test_tie_candidates_block_when_present(self):
        t = self.loader.load_for_role("villager")
        ctx = tie_revote_context()  # tie_candidates=["P3", "P4"]
        user = self.loader.render(t, ctx)[1]["content"]
        assert "## 平票候选 (tie_candidates)" in user
        assert "P3" in user and "P4" in user

    def test_visible_players_block_marks_self(self):
        t = self.loader.load_for_role("villager")
        ctx = vote_context()  # agent_id=P2
        user = self.loader.render(t, ctx)[1]["content"]
        assert "## 视野内玩家" in user
        # P2 应该被标"你"
        assert "P2: alive" in user
        assert "（你）" in user
        # P5 是死亡
        assert "P5: dead" in user

    def test_public_claim_rendered_when_present(self):
        t = self.loader.load_for_role("villager")
        ctx = vote_context()  # P3 has public_claim=Role.SEER
        user = self.loader.render(t, ctx)[1]["content"]
        assert "public_claim=seer" in user


# ---------------------------------------------------------------------------
# render — optional blocks skipped when data absent
# ---------------------------------------------------------------------------


class TestOptionalBlocks:
    def setup_method(self) -> None:
        self.loader = PromptTemplateLoader()

    def test_empty_private_events_block_skipped(self):
        t = self.loader.load_for_role("villager")
        user = self.loader.render(t, vote_context())[1]["content"]
        assert "## 私密事件" not in user

    def test_empty_tie_candidates_block_skipped(self):
        t = self.loader.load_for_role("villager")
        user = self.loader.render(t, vote_context())[1]["content"]
        assert "## 平票候选" not in user

    def test_empty_public_memory_summary_block_skipped(self):
        t = self.loader.load_for_role("villager")
        user = self.loader.render(t, vote_context())[1]["content"]
        assert "## 公开历史摘要" not in user

    def test_empty_rule_hints_block_skipped(self):
        t = self.loader.load_for_role("villager")
        user = self.loader.render(t, vote_context())[1]["content"]
        assert "## rule_hints" not in user


# ---------------------------------------------------------------------------
# render — richer context (public events, claim_records, vote_records, rule_hints)
# ---------------------------------------------------------------------------


def _rich_context_with_d2_events() -> AgentContext:
    """模拟 D2 预言家 + 投票阶段，含 claim_records / vote_records / public_events / rule_hints。"""
    return AgentContext(
        game_id="g001",
        agent_id="P3",
        role=Role.SEER,
        round=2,
        phase=Phase.DAY_VOTE,
        visible_players=[
            VisiblePlayer(player_id="P1", status=PlayerStatus.ALIVE, public_claim=None),
            VisiblePlayer(player_id="P2", status=PlayerStatus.ALIVE, public_claim=Role.SEER.value),
            VisiblePlayer(player_id="P3", status=PlayerStatus.ALIVE, public_claim=Role.SEER.value),
            VisiblePlayer(player_id="P4", status=PlayerStatus.DEAD, public_claim=None),
        ],
        public_memory_summary=["D1 Daybreak: P4 died by night_kill", "D1 Vote: P1->P3"],
        current_round_events=[
            PublicEvent(
                event_id="e_speech_1",
                round=2,
                phase=Phase.DAY_DISCUSSION,
                event_type=EventType.SPEECH,
                actor="P2",
                public_message="我是预言家",
                role_claim=Role.SEER,
                claim_result=ClaimResult(target="P3", claimed_alignment=ClaimedAlignment.WEREWOLF),
            )
        ],
        private_events=[
            PrivateEvent(
                event_type=EventType.SEER_CHECK_RESULT,
                round=1,
                target="P1",
                result="werewolf",
                visibility=Visibility.PRIVATE_TO_SEER,
            ),
        ],
        previous_vote_summary={"P1": 2, "P3": 1},
        claim_records=[
            ClaimRecord(
                record_id="cr_001",
                game_id="g001",
                round=2,
                phase=Phase.DAY_DISCUSSION,
                actor="P2",
                claimed_role=Role.SEER,
                claim_target="P3",
                claimed_alignment=ClaimedAlignment.WEREWOLF,
                is_counter_claim=True,
                source_event_id="e_speech_1",
                derived_by="context_assembler",
            )
        ],
        vote_records=[
            VoteRecord(
                record_id="vr_001",
                game_id="g001",
                round=1,
                phase=Phase.DAY_VOTE,
                stage="primary",
                voter="P1",
                target="P3",
                source_event_id="e_vote_1",
                derived_by="context_assembler",
            )
        ],
        allowed_actions=[ActionType.VOTE],
        rule_hints={"fallback_targets": ["P1", "P2"]},
    )


class TestRichContextBlocks:
    def setup_method(self) -> None:
        self.loader = PromptTemplateLoader()
        self.ctx = _rich_context_with_d2_events()
        self.user = self.loader.render(self.loader.load_for_role("seer"), self.ctx)[1]["content"]

    def test_public_memory_summary_block(self):
        assert "## 公开历史摘要 (public_memory_summary)" in self.user
        assert "P4 died" in self.user
        assert "P1->P3" in self.user

    def test_current_round_events_block(self):
        assert "## 当前轮事件 (current_round_events)" in self.user
        assert "speech" in self.user
        assert "actor=P2" in self.user
        assert "role_claim=seer" in self.user
        # claim_result 拼成 target/alignment
        assert "target=P3" in self.user
        assert "alignment=werewolf" in self.user

    def test_previous_vote_summary_block(self):
        assert "## 票数摘要 (previous_vote_summary)" in self.user
        # JSON 序列化
        assert '"P1": 2' in self.user
        assert '"P3": 1' in self.user

    def test_claim_records_block(self):
        assert "## 跳身份/查杀台账 (claim_records)" in self.user
        assert "actor=P2" in self.user
        assert "claimed_role=seer" in self.user
        assert "claim_target=P3" in self.user
        assert "counter_claim=True" in self.user

    def test_vote_records_block(self):
        assert "## 投票台账 (vote_records)" in self.user
        assert "voter=P1" in self.user
        assert "target=P3" in self.user
        assert "stage=primary" in self.user

    def test_rule_hints_block_serialized_as_json(self):
        assert "## rule_hints" in self.user
        assert '"fallback_targets"' in self.user
        assert '"P1"' in self.user


# ---------------------------------------------------------------------------
# end-to-end smoke：Loader + FakeLLMProvider + ActionParser → AgentAction
# ---------------------------------------------------------------------------


class TestOutputBlockByAllowedActions:
    """输出 JSON 模板按 ``allowed_actions`` 动态裁剪字段（A 5/26 18:00-18:04 拍板）。

    覆盖：
    - NIGHT_WEREWOLF only 列 night_kill_nominate, 不混 speak/vote
    - NIGHT_SEER only 列 check
    - NIGHT_WITCH 列 save/poison/skip 三 case
    - DAY_DISCUSSION + speak 含 public_message/role_claim/claim_result
    - DAY_TIE_REVOTE + vote 提示 tie_candidates 限制
    - HUNTER_SHOOT 同时给出"开枪"和"不开枪 (target=null)"两种例子
    - skip 单独说明
    - 空 allowed_actions 给一个明确兜底说明
    """

    def setup_method(self) -> None:
        self.loader = PromptTemplateLoader()

    def _user(self, ctx: AgentContext) -> str:
        return self.loader.render(self.loader.load_for_role(ctx.role.value), ctx)[1]["content"]

    def test_werewolf_night_only_lists_night_kill(self):
        user = self._user(werewolf_context())
        assert "**night_kill_nominate**" in user
        assert "private_events.teammates" in user
        # 不应该混入其他 action 的模板
        assert "**speak**" not in user
        assert "**vote**" not in user
        assert "**check**" not in user

    def test_seer_night_only_lists_check(self):
        user = self._user(seer_context())
        assert "**check**" in user
        assert "SEER_CHECK_RESULT" in user
        assert "**night_kill_nominate**" not in user
        assert "**speak**" not in user

    def test_witch_night_lists_save_poison_skip(self):
        user = self._user(witch_context())
        assert "**save**" in user
        assert "**poison**" in user
        assert "**skip**" in user
        # 女巫首夜可救自己的提示
        assert "首夜" in user or "round=1" in user

    def test_day_discussion_speak_includes_role_claim_and_claim_result(self):
        user = self._user(day_discussion_context())
        assert "**speak**" in user
        assert "public_message" in user
        assert "role_claim" in user
        assert "claim_result" in user
        # 不应该有 vote/check 模板
        assert "**vote**" not in user
        assert "**check**" not in user

    def test_tie_revote_vote_mentions_tie_candidates(self):
        ctx = tie_revote_context()
        user = self._user(ctx)
        assert "**vote**" in user
        assert "tie_candidates" in user
        # 列出实际候选
        for cand in ctx.tie_candidates:
            assert cand in user

    def test_hunter_shoot_lists_both_open_fire_and_pass(self):
        user = self._user(hunter_shoot_context())
        assert "**hunter_shoot**" in user
        # 开枪 + 不开枪两种 case 同时给出
        assert '"target": "P<n>"' in user
        assert '"target": null' in user
        assert "不开枪" in user or "target=null" in user

    def test_empty_allowed_actions_gracefully_handled(self):
        ctx = AgentContext(
            game_id="g_empty",
            agent_id="P1",
            role=Role.VILLAGER,
            round=1,
            phase=Phase.DAY_DISCUSSION,
            allowed_actions=[],
        )
        user = self._user(ctx)
        # 不抛错；给一个明确兜底文案
        assert "无合法 action" in user or "allowed_actions" in user

    def test_output_block_lists_all_allowed_actions_in_header(self):
        ctx = witch_context()  # [save, poison, skip]
        user = self._user(ctx)
        # 头部应该明确列出所有 allowed_actions
        assert "save" in user and "poison" in user and "skip" in user
        assert "`allowed_actions` = [" in user
        # 引用 shared 输出契约，避免重复维护 schema
        assert "v0 LLM Global Output Contract" in user


class TestEndToEndSmoke:
    """v0 baseline 链路烟囱测试：prompt + 假 LLM + parser 三件套能跑通到合法 AgentAction。"""

    def test_wolf_decision_pipeline(self):
        loader = PromptTemplateLoader()
        parser = ActionParser()
        provider = FakeLLMProvider(
            responses=json.dumps({"action_type": "night_kill_nominate", "target": "P3"})
        )

        ctx = werewolf_context()
        template = loader.load_for_role(ctx.role.value)
        messages = loader.render(template, ctx)
        assert len(messages) == 2

        # FakeLLMProvider 是 async；smoke 里 asyncio.run 包一层（与 test_llm_provider.py 同款）
        response = asyncio.run(provider.generate(messages, {"model_name": "fake"}))
        action = parser.parse(response.raw_output, ctx)
        assert action.action_type == ActionType.NIGHT_KILL_NOMINATE
        assert action.target == "P3"
        assert action.game_id == ctx.game_id
        assert action.agent_id == ctx.agent_id

    def test_seer_decision_pipeline(self):
        loader = PromptTemplateLoader()
        parser = ActionParser()
        provider = FakeLLMProvider(responses=json.dumps({"action_type": "check", "target": "P4"}))

        ctx = seer_context()
        template = loader.load_for_role(ctx.role.value)
        messages = loader.render(template, ctx)
        response = asyncio.run(provider.generate(messages, {"model_name": "fake"}))
        action = parser.parse(response.raw_output, ctx)
        assert action.action_type == ActionType.CHECK
        assert action.target == "P4"

    def test_villager_speak_pipeline(self):
        loader = PromptTemplateLoader()
        parser = ActionParser()
        provider = FakeLLMProvider(
            responses=json.dumps(
                {
                    "action_type": "speak",
                    "public_message": "我倾向投 P3，他发言有矛盾。",
                    "reason_summary": "P3 发言矛盾",
                }
            )
        )

        ctx = day_discussion_context()
        template = loader.load_for_role(ctx.role.value)
        messages = loader.render(template, ctx)
        response = asyncio.run(provider.generate(messages, {"model_name": "fake"}))
        action = parser.parse(response.raw_output, ctx)
        assert action.action_type == ActionType.SPEAK
        assert "P3" in (action.public_message or "")


def test_loader_reads_custom_soul_from_data_dir(tmp_path):
    custom_dir = tmp_path / "souls"
    custom_dir.mkdir()
    (custom_dir / "patient_reader.md").write_text(
        "# Soul：耐心读牌型\n\n先复述公开信息，再给低风险判断。",
        encoding="utf-8",
    )
    loader = PromptTemplateLoader(soul_id="patient_reader", custom_souls_dir=custom_dir)

    template = loader.load_for_role("villager")

    assert template.metadata["soul_id"] == "patient_reader"
    assert template.metadata["soul_path"] == str(custom_dir / "patient_reader.md")
    assert "耐心读牌型" in template.system_prompt


# ---------------------------------------------------------------------------
# PR-FD-A2：v1 模板 belief={} 退化
# phase5 三方向并行地基 §2.2 —— 给 A 的混合实验做"按 player 注入 belief"提供公平性前置。
# 非混合实验场景（全 v1 全注 belief）行为不变。
# ---------------------------------------------------------------------------


_FALLBACK_KEY = "system_prompt_empty_belief_fallback"
_ALL_ROLE_VALUES = ("werewolf", "seer", "witch", "hunter", "villager")


class TestV1BeliefFallback:
    """v1 模板在 belief 为空时退化成"无 belief 关键词"的 prompt（v0-like，不严格等同 v0）。"""

    @pytest.fixture
    def loader(self) -> PromptTemplateLoader:
        return PromptTemplateLoader()

    @pytest.mark.parametrize("role_value", _ALL_ROLE_VALUES)
    def test_v1_load_caches_empty_belief_fallback_in_metadata(
        self, loader: PromptTemplateLoader, role_value: str
    ):
        """load v1 模板时，metadata 同时缓存空-belief fallback system_prompt（render 退化用）。
        参数化覆盖 5 个角色，防止某个角色 prompt 后续新增 belief 文案后漏掉。"""
        t = loader.load(f"{role_value}:v1_belief_llm")
        assert "belief_guidance_path" in t.metadata
        assert _FALLBACK_KEY in t.metadata
        fallback = t.metadata[_FALLBACK_KEY]
        assert fallback, f"{role_value} fallback system_prompt should not be empty"
        # fallback 比完整 v1 短（少了 belief_guidance 那一段）
        assert len(fallback) < len(t.system_prompt)
        # 全 v1 system_prompt 含 belief 词；fallback 不含
        assert "belief" in t.system_prompt.lower()
        assert "belief" not in fallback.lower(), (
            f"{role_value}: fallback 仍含 'belief'，可能某行 v0 belief 禁令未被 _strip 吃掉"
        )

    @pytest.mark.parametrize("role_value", _ALL_ROLE_VALUES)
    def test_v0_load_does_not_set_fallback(
        self, loader: PromptTemplateLoader, role_value: str
    ):
        """v0 模板不需要 fallback；render 走原 system_prompt。"""
        t = loader.load(f"{role_value}:v0_free_llm")
        assert _FALLBACK_KEY not in t.metadata
        assert "belief_guidance_path" not in t.metadata

    def test_v1_render_falls_back_when_belief_empty(self, loader: PromptTemplateLoader):
        """混合实验关键不变量：v1 模板 + belief 空 → 渲染出的 system 不含 belief 词。"""
        t = loader.load("werewolf:v1_belief_llm")
        ctx = werewolf_context()  # 默认 belief_state={} belief_top_suspects=[]
        assert ctx.belief_state == {}
        assert ctx.belief_top_suspects == []
        messages = loader.render(t, ctx)
        system_content = messages[0]["content"]
        assert messages[0]["role"] == "system"
        assert "belief" not in system_content.lower(), (
            "belief 空时 v1 prompt 必须退化为无 belief 关键词"
        )

    def test_v1_render_keeps_guidance_when_belief_nonempty(
        self, loader: PromptTemplateLoader
    ):
        """belief 非空时不退化：v1 guidance 仍然附加，prompt 含 belief 关键词。"""
        t = loader.load("werewolf:v1_belief_llm")
        ctx = werewolf_context()
        ctx.belief_top_suspects = [
            {"player_id": "P3", "werewolf_prob": 0.72},
            {"player_id": "P5", "werewolf_prob": 0.55},
        ]
        messages = loader.render(t, ctx)
        system_content = messages[0]["content"]
        assert "belief" in system_content.lower()
        # 关键短语：belief_top_suspects 来自 v1_belief_guidance.md
        assert "belief_top_suspects" in system_content

    def test_v1_render_falls_back_only_when_both_belief_fields_empty(
        self, loader: PromptTemplateLoader
    ):
        """belief_state 非空 OR belief_top_suspects 非空都不退化；同时空才退化。"""
        t = loader.load("werewolf:v1_belief_llm")

        # 只有 belief_state 非空 → 不退化
        ctx_a = werewolf_context()
        ctx_a.belief_state = {"P3": {"werewolf": 0.7}}
        ctx_a.belief_top_suspects = []
        sys_a = loader.render(t, ctx_a)[0]["content"]
        assert "belief" in sys_a.lower()

        # 只有 belief_top_suspects 非空 → 不退化
        ctx_b = werewolf_context()
        ctx_b.belief_state = {}
        ctx_b.belief_top_suspects = [{"player_id": "P3", "werewolf_prob": 0.7}]
        sys_b = loader.render(t, ctx_b)[0]["content"]
        assert "belief" in sys_b.lower()

        # 两个都空 → 退化
        ctx_c = werewolf_context()
        ctx_c.belief_state = {}
        ctx_c.belief_top_suspects = []
        sys_c = loader.render(t, ctx_c)[0]["content"]
        assert "belief" not in sys_c.lower()

    def test_v0_render_unaffected_by_belief_fields(self, loader: PromptTemplateLoader):
        """v0 模板不论 belief 是否为空，行为都和原来一样（不退化路径不影响 v0）。"""
        t = loader.load("werewolf:v0_free_llm")
        ctx = werewolf_context()
        ctx.belief_top_suspects = [{"player_id": "P3", "werewolf_prob": 0.9}]
        messages = loader.render(t, ctx)
        # v0 模板 system_prompt 本来就是 output_contract + 完整 role 文本（含 v0 禁令）
        # render 不应去动它（fallback 元数据没设置 → 不切换）
        assert messages[0]["content"].strip() == t.system_prompt.strip()


class TestV1ConsumeVariant:
    """段2 消费纪律变体 v1_belief_consume_llm（叠在 v1 之上，基线 v1/v0 不受影响）。"""

    def setup_method(self) -> None:
        self.loader = PromptTemplateLoader()

    def test_consume_variant_appends_section_and_tags_prompt_version(self):
        t = self.loader.load("villager:v1_belief_consume_llm")
        # 变体名进 prompt_version_id → trace 可区分，便于离线分析。
        assert t.prompt_version_id == "villager:v1_belief_consume_llm"
        assert "消费纪律" in t.system_prompt           # consume 段已追加
        assert "必须投头号嫌疑" in t.system_prompt       # 硬性对齐规则（去逃逸口后）
        assert "置信档" in t.system_prompt or "Belief Guidance" in t.system_prompt  # v1 段仍在
        assert t.metadata.get("belief_consume_path") is not None

    def test_v1_baseline_unaffected_by_consume_variant(self):
        t = self.loader.load("villager:v1_belief_llm")
        assert "消费纪律" not in t.system_prompt
        assert t.metadata.get("belief_consume_path") is None

    def test_v0_baseline_has_neither_belief_nor_consume(self):
        t = self.loader.load("villager:v0_free_llm")
        assert "消费纪律" not in t.system_prompt
        assert t.metadata.get("belief_consume_path") is None
        assert t.metadata.get("belief_guidance_path") is None
