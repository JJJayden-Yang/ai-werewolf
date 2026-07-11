"""PromptTemplateLoader —— v0 prompt 唯一真相源读取 + AgentContext 序列化。

设计要点（A 5/26 00:50 @_all 三段确认项 选①：prompts/<role>/v0_free_llm.md 当唯一真相源）：

- ``load(prompt_version_id)`` 按 ``<role>:<template_name>`` 解析 id（如 ``werewolf:v0_free_llm``）→
  读 ``<prompts_dir>/<role>/<template_name>.md`` → 包成 ``PromptTemplate``。
- ``render(template, context)`` 把策略文本作为 ``system`` 消息，``AgentContext`` 序列化成中文 markdown
  作为 ``user`` 消息，组合成 OpenAI 风格 ``[{role, content}, ...]``。
- 完整 prompt 内容不进 EventLog —— 只记录 ``prompt_version_id``（见 Interface_v2_1 §5.5）。
- 序列化只读 ``AgentContext`` 已暴露字段；不读 ``GameSession`` / ``TruthState`` / 任何 Store。

A 给 v0 LLM Supervisor 的接线不变量（5/26 00:47 @C，提醒一下，不在本文件强制）：
- 构造 ``Supervisor`` 时显式传 ``deliver_witch_kill_info=True``，否则 NIGHT_WITCH 看不到当晚刀口。
- 传给 Supervisor 的 EventSink 必须与 ``ContextAssembler(event_store=...)`` 是同一个 ``EventStore`` 实例。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from agent_runtime.exceptions import PromptTemplateNotFoundError
from agent_runtime.types import PromptTemplate

if TYPE_CHECKING:
    from contracts import AgentContext, PrivateEvent, PublicEvent, VisiblePlayer
    from contracts.schemas import ClaimRecord, VoteRecord


_DEFAULT_TEMPLATE_NAME = "v0_free_llm"
_V1_BELIEF_TEMPLATE_NAME = "v1_belief_llm"
# v1 belief「消费纪律」变体（段2 实验）：在 v1_belief_llm 之上追加 v1_belief_consume.md，
# 提高 belief→投票一致率。是 v1 的一个可切换变体（独立 template_name → 进 prompt_version_id
# → trace 可区分 → A/B 自动判 from!=to）。默认基线 v1_belief_llm 完全不受影响。
_V1_CONSUME_TEMPLATE_NAME = "v1_belief_consume_llm"
_V1_TEMPLATE_NAMES = (_V1_BELIEF_TEMPLATE_NAME, _V1_CONSUME_TEMPLATE_NAME)
_PROMPT_ID_SEP = ":"
_DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "agent_policy" / "prompts"
# B 5/26 18:47 推的全局输出契约（v0 LLM Global Output Contract）。
# 路径相对 prompts_dir：``<prompts_dir>/shared/output_contract.md``。
# 当文件存在时，load() 把它 prepend 到 role prompt 作为完整 system_prompt；
# 文件缺失时退化为只加载 role prompt（向后兼容；shared 是 v2.2+ 引入，老 fixture 不受影响）。
_SHARED_OUTPUT_CONTRACT_RELPATH = ("shared", "output_contract.md")
# 通用对局知识层（局配置 / 阶段常识 / 胜负规则）。与 output_contract 一样按
# "文件存在才加、缺失则跳过" 的向后兼容方式注入；拼接顺序固定在 output_contract 之后、
# role prompt 之前。缺失时退化为不含该层（老 fixture / 自定义 prompts_dir 不受影响）。
_SHARED_GAME_KNOWLEDGE_RELPATH = ("shared", "game_knowledge.md")
_SHARED_V1_BELIEF_GUIDANCE_RELPATH = ("shared", "v1_belief_guidance.md")
# consume 变体专属追加段（叠在 v1_belief_guidance 之后）。仅 _V1_CONSUME_TEMPLATE_NAME 时加。
_SHARED_V1_BELIEF_CONSUME_RELPATH = ("shared", "v1_belief_consume.md")
# Soul 人格层（phase2）：正交于 role/template 的第三维度，**不进 prompt_version_id**，
# 而是 loader 实例级配置（单一全局 soul，应用于所有玩家）。拼接顺序固定在 role 之后、
# belief_guidance 之前。soul_id=None（默认）= 不注入，保持 v0 baseline 不变（向后兼容）；
# 显式给了 soul_id 但文件缺失则报错（防 typo，与 v1 belief_guidance 缺失同策略）。
_SOUL_RELDIR = "souls"
_SOUL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# 中性默认人格（冷静分析）。脚本 --soul 默认值、SeatSoulAgent 缺座位兜底都用它。
# 注意：这只是"上层默认"——底层 PromptTemplateLoader/LLMAgent 的 soul_id=None 仍表示
# "不注入 soul"（向后兼容），真·无 soul 基线可由 `--soul none` 触发。
DEFAULT_SOUL_ID = "default_balanced"


class PromptTemplateLoader:
    """按 ``prompt_version_id`` 加载策略 markdown + 把 ``AgentContext`` 序列化成 LLM 输入。"""

    def __init__(
        self,
        prompts_dir: Path | str | None = None,
        *,
        soul_id: str | None = None,
        custom_souls_dir: Path | str | None = None,
    ) -> None:
        self._prompts_dir = Path(prompts_dir) if prompts_dir is not None else _DEFAULT_PROMPTS_DIR
        self._custom_souls_dir = (
            Path(custom_souls_dir)
            if custom_souls_dir is not None
            else Path(os.getenv("AI_WOLF_DATA_DIR", "./data")) / "souls"
        )
        # 全局 soul（phase2）：None = 不注入 soul 层（默认，向后兼容）。
        self._soul_id = soul_id

    @property
    def prompts_dir(self) -> Path:
        return self._prompts_dir

    @property
    def soul_id(self) -> str | None:
        return self._soul_id

    def load(self, prompt_version_id: str) -> PromptTemplate:
        """``prompt_version_id`` 格式：``"<role>:<template_name>"``，例 ``werewolf:v0_free_llm``。

        基础层按固定顺序组装（每个 shared 层"文件存在才加、缺失则跳过"，向后兼容）：

            system_prompt =
              shared/output_contract.md  (全局：JSON shape / 字段规则 / one-shot 示例)
              \n\n
              shared/game_knowledge.md   (通用：局配置 / 阶段常识 / 胜负规则，可选)
              \n\n
              <role>/<template_name>.md   (角色：策略 / 技能 / 红线)
              \n\n
              souls/<soul_id>.md          (人格：表达风格 / 软倾向，loader.soul_id 开启时)

        请求 ``<role>:v1_belief_llm`` 时，如果没有角色专属 v1 文件，会复用该角色
        ``v0_free_llm.md``，并在末尾追加 ``shared/v1_belief_guidance.md``。这样 v1
        可以消费 ``belief_top_suspects``，而 v0 仍保持纯 LLM prompt。

        ``metadata`` 同时记录 ``source_path`` (role 文件)、``shared_path``（如果加载了
        output_contract）、``game_knowledge_path``（如果加载了 game_knowledge）、
        ``soul_id`` / ``soul_path``（如果 loader 配了 soul_id）和
        ``belief_guidance_path``（v1 时）。
        """
        role_value, template_name = self._parse_prompt_version_id(prompt_version_id)
        is_v1 = template_name in _V1_TEMPLATE_NAMES
        is_consume = template_name == _V1_CONSUME_TEMPLATE_NAME
        role_path = self._prompts_dir / role_value / f"{template_name}.md"
        # v1 系列没有角色专属文件时，复用该角色 v0_free_llm.md 再追加 belief 段
        if is_v1 and not role_path.is_file():
            role_path = self._prompts_dir / role_value / f"{_DEFAULT_TEMPLATE_NAME}.md"
        if not role_path.is_file():
            raise PromptTemplateNotFoundError(prompt_version_id=prompt_version_id, path=str(role_path))

        role_text_raw = role_path.read_text(encoding="utf-8").strip()
        role_text = (
            _strip_v0_belief_disallowance(role_text_raw) if is_v1 else role_text_raw
        )

        shared_path = self._prompts_dir.joinpath(*_SHARED_OUTPUT_CONTRACT_RELPATH)
        game_knowledge_path = self._prompts_dir.joinpath(*_SHARED_GAME_KNOWLEDGE_RELPATH)
        belief_guidance_path = self._prompts_dir.joinpath(*_SHARED_V1_BELIEF_GUIDANCE_RELPATH)
        metadata: dict = {"source_path": str(role_path)}

        # 按固定 layer 顺序组装非 belief 的基础层：
        #   output_contract (可选) → game_knowledge (可选) → role (必需) → soul (可选)
        # output_contract / game_knowledge "文件存在才加、缺失则跳过"；soul 由 loader 的
        # soul_id 显式开启（None 则不加，缺文件则报错）。路径 / soul_id 写进 metadata 便于
        # 调试 / trace 归因 / 后续 hash。
        # base_parts 即"不含 belief guidance 的 system"，同时作为 v1 empty-belief fallback 的基底
        # —— soul 也在 base_parts 里，所以拿空 belief 的 v1 玩家仍保留人格层。
        base_parts: list[str] = []
        if shared_path.is_file():
            base_parts.append(shared_path.read_text(encoding="utf-8").strip())
            metadata["shared_path"] = str(shared_path)
        if game_knowledge_path.is_file():
            base_parts.append(game_knowledge_path.read_text(encoding="utf-8").strip())
            metadata["game_knowledge_path"] = str(game_knowledge_path)
        base_parts.append(role_text)
        if self._soul_id:
            soul_path = self._resolve_soul_path(self._soul_id)
            if soul_path is None:
                raise PromptTemplateNotFoundError(
                    prompt_version_id=prompt_version_id,
                    path=str(self._prompts_dir / _SOUL_RELDIR / f"{self._soul_id}.md"),
                )
            base_parts.append(soul_path.read_text(encoding="utf-8").strip())
            metadata["soul_id"] = self._soul_id
            metadata["soul_path"] = str(soul_path)

        prompt_parts = list(base_parts)

        if is_v1:
            if not belief_guidance_path.is_file():
                raise PromptTemplateNotFoundError(
                    prompt_version_id=prompt_version_id,
                    path=str(belief_guidance_path),
                )
            prompt_parts.append(belief_guidance_path.read_text(encoding="utf-8").strip())
            metadata["belief_guidance_path"] = str(belief_guidance_path)
            # consume 变体：在 belief_guidance 之后再追加「消费纪律」段（仅此变体生效）。
            if is_consume:
                consume_path = self._prompts_dir.joinpath(*_SHARED_V1_BELIEF_CONSUME_RELPATH)
                if not consume_path.is_file():
                    raise PromptTemplateNotFoundError(
                        prompt_version_id=prompt_version_id, path=str(consume_path)
                    )
                prompt_parts.append(consume_path.read_text(encoding="utf-8").strip())
                metadata["belief_consume_path"] = str(consume_path)
            # PR-FD-A2（phase5 三方向并行地基 §2.2）：v1 模板需要在 belief 为空时退化为
            # **不含 "belief" 关键词** 的 prompt。这是为 A 的混合实验"按 player 注入 belief"
            # 做公平性前置 —— 非狼/非民拿到空 belief 时不应被 v1 guidance 干扰行为。
            # 注意：这只是"无 belief 词的 fallback"，**不等同纯 v0**：
            #   - role 文本用的是已经 _strip 过的版本（剥掉了 v0 里"不读 belief"的禁令行 ——
            #     否则禁令行本身就含 "belief" 词会带回来）
            #   - 不附加 v1_belief_guidance
            #   - 复用 base_parts（含 output_contract + game_knowledge + role），保证新增基础层
            #     不会在 fallback 里被漏掉（曾经写死 prompt_parts[0] 只取 shared，插层即丢）
            # 命名上用 system_prompt_empty_belief_fallback 而非 _v0_fallback 也是这个原因。
            metadata["system_prompt_empty_belief_fallback"] = "\n\n".join(base_parts)

        system_prompt = "\n\n".join(prompt_parts)

        return PromptTemplate(
            prompt_version_id=prompt_version_id,
            role=role_value,
            system_prompt=system_prompt,
            user_prompt_template="",
            metadata=metadata,
        )

    def load_for_role(self, role_value: str, template_name: str = _DEFAULT_TEMPLATE_NAME) -> PromptTemplate:
        """便利方法：按 ``Role.value``（小写字符串）+ 模板名拼 id 加载。"""
        return self.load(self._build_prompt_version_id(role_value, template_name))

    def render(
        self,
        template: PromptTemplate,
        context: AgentContext,
        extra_system_sections: list[str] | None = None,
    ) -> list[dict]:
        """渲染成 OpenAI 风格的 messages 列表（``[{role, content}, ...]``）。

        - ``system``：策略 markdown 原文（``prompts/<role>/<tmpl>.md``）。
        - ``user``：当前局面快照（``AgentContext`` 字段序列化成中文 markdown block）。

        **PR-FD-A2 退化（phase5 三方向并行地基 §2.2）**：v1 模板且 ``belief_state == {}`` 且
        ``belief_top_suspects == []`` 时，切到预算好的 ``system_prompt_empty_belief_fallback``
        —— 不附加 v1 belief guidance，渲染出**不含 "belief" 关键词**的 system，让混合实验中
        拿空 belief 的玩家做无 belief 干扰的自由推理（这是 v0-like fallback，不严格等同纯 v0）。

        **Phase 3 高级策略注入**：``extra_system_sections`` 非空时（由 LLMAgent 的
        StrategySelector 按场景选出），把这些片段拼到 base system 之后、统一包一个
        **"参考打法（非硬规则）"硬约束框**——层序语义 output_contract(最高) > role/soul >
        参考打法 > belief data。框里那句"冲突时以角色策略/契约为准"是防策略被当硬规则的关键防线。
        """
        system_content = self._select_system_prompt(template, context)
        strategy_block = _render_strategy_block(extra_system_sections)
        if strategy_block:
            system_content = system_content.strip() + "\n\n" + strategy_block
        return [
            {"role": "system", "content": system_content.strip()},
            {"role": "user", "content": _serialize_agent_context(context)},
        ]

    @staticmethod
    def _select_system_prompt(template: PromptTemplate, context: AgentContext) -> str:
        fallback = template.metadata.get("system_prompt_empty_belief_fallback")
        if fallback is None:
            return template.system_prompt  # v0 模板没有 fallback；按原 system_prompt 走
        belief_empty = not context.belief_state and not context.belief_top_suspects
        return fallback if belief_empty else template.system_prompt

    @staticmethod
    def _parse_prompt_version_id(prompt_version_id: str) -> tuple[str, str]:
        if not isinstance(prompt_version_id, str) or _PROMPT_ID_SEP not in prompt_version_id:
            raise ValueError(
                "prompt_version_id must be '<role>:<template_name>', "
                f"got {prompt_version_id!r}"
            )
        role_value, template_name = prompt_version_id.split(_PROMPT_ID_SEP, 1)
        role_value = role_value.strip()
        template_name = template_name.strip()
        if not role_value or not template_name:
            raise ValueError(
                "prompt_version_id parts must be non-empty, "
                f"got {prompt_version_id!r}"
            )
        return role_value, template_name

    @staticmethod
    def _build_prompt_version_id(role_value: str, template_name: str) -> str:
        return f"{role_value}{_PROMPT_ID_SEP}{template_name}"

    def _resolve_soul_path(self, soul_id: str) -> Path | None:
        if not _SOUL_ID_RE.fullmatch(soul_id):
            return None
        builtin_path = self._prompts_dir / _SOUL_RELDIR / f"{soul_id}.md"
        if builtin_path.is_file():
            return builtin_path
        custom_path = self._custom_souls_dir / f"{soul_id}.md"
        if custom_path.is_file():
            return custom_path
        return None


# ---------------------------------------------------------------------------
# Phase 3 高级策略注入框
# ---------------------------------------------------------------------------

_STRATEGY_FRAME_HEADER = (
    "## 参考打法（非硬规则）\n"
    "以下是针对当前局面的可选参考思路，不是命令；与角色基础策略、`allowed_actions`、"
    "信息边界、统一 JSON 输出契约冲突时，一律以后者为准。"
)


def _render_strategy_block(sections: list[str] | None) -> str:
    """把选中的策略片段包进统一硬约束框；无片段时返回空串。"""
    cleaned = [s.strip() for s in (sections or []) if s and s.strip()]
    if not cleaned:
        return ""
    return _STRATEGY_FRAME_HEADER + "\n\n" + "\n\n".join(cleaned)


# ---------------------------------------------------------------------------
# AgentContext → user prompt (markdown)
# ---------------------------------------------------------------------------


def _serialize_agent_context(context: AgentContext) -> str:
    """把 ``AgentContext`` 按 prompt 文件里点名引用的字段序列化成中文 markdown。

    blocks 顺序固定，便于 LLM 学习 + diff baseline。空 block 跳过（除 header / allowed_actions / output 三个必出）。
    不暴露 ``TruthState`` / ``BeliefState.shadow`` 等 lane 内部细节。
    """
    sections: list[str] = []
    sections.append(_render_basic_block(context))
    sections.append(_render_allowed_actions_block(context))
    visible = _render_visible_players_block(context.visible_players, context.agent_id)
    if visible:
        sections.append(visible)
    if context.tie_candidates:
        sections.append(_render_tie_candidates_block(context.tie_candidates))
    if context.public_memory_summary:
        sections.append(_render_public_memory_summary_block(context.public_memory_summary))
    if context.current_round_events:
        sections.append(_render_events_block("当前轮事件 (current_round_events)", context.current_round_events))
    if context.recent_public_events:
        sections.append(_render_events_block("近期公开事件 (recent_public_events)", context.recent_public_events))
    if context.private_events:
        sections.append(_render_private_events_block(context.private_events))
    if context.previous_vote_summary:
        sections.append(_render_previous_vote_summary_block(context.previous_vote_summary))
    if context.claim_records:
        sections.append(_render_claim_records_block(context.claim_records))
    if context.vote_records:
        sections.append(_render_vote_records_block(context.vote_records))
    if context.rule_hints:
        sections.append(_render_rule_hints_block(context.rule_hints))
    if context.belief_state:
        belief_full = _render_belief_full_block(context.belief_state)
        if belief_full:
            sections.append(belief_full)
    if context.belief_top_suspects:
        sections.append(_render_belief_top_suspects_block(context.belief_top_suspects))
    sections.append(_render_output_instruction_block(context))
    return "\n\n".join(section for section in sections if section)


def _strip_v0_belief_disallowance(role_text: str) -> str:
    """Remove v0-only belief bans when composing the v1 belief prompt.

    The v1 template intentionally reuses role strategy text, but hard v0 lines
    such as "do not read belief_top_suspects" would conflict with the appended
    v1 guidance. Keep all other information-boundary rules intact.
    """
    blocked_fragments = (
        "不读取 `belief_state` / `belief_top_suspects`",
        "不注入 belief",
        "不读 `belief_state` / `belief_top_suspects`",
        "v0 不读取 belief",
    )
    lines = [
        line for line in role_text.splitlines()
        if not any(fragment in line for fragment in blocked_fragments)
    ]
    return "\n".join(lines).strip()


def _render_basic_block(context: AgentContext) -> str:
    lines = [
        "## 当前局面",
        f"- game_id: {context.game_id}",
        f"- agent_id: {context.agent_id}（你）",
        f"- role: {context.role.value}",
        f"- round: {context.round}",
        f"- phase: {context.phase.value}",
    ]
    if context.is_secondary_stage:
        lines.append(f"- is_secondary_stage: True (type={context.secondary_stage_type})")
    return "\n".join(lines)


def _render_allowed_actions_block(context: AgentContext) -> str:
    actions = ", ".join(a.value for a in context.allowed_actions) or "(空)"
    return "## allowed_actions\n[" + actions + "]"


def _render_visible_players_block(players: list[VisiblePlayer], self_id: str) -> str:
    if not players:
        return ""
    lines = ["## 视野内玩家 (visible_players)"]
    for p in players:
        marker = "（你）" if p.player_id == self_id else ""
        claim = f", public_claim={p.public_claim}" if p.public_claim else ""
        lines.append(f"- {p.player_id}: {p.status.value}{claim}{marker}")
    return "\n".join(lines)


def _render_tie_candidates_block(candidates: list[str]) -> str:
    return "## 平票候选 (tie_candidates)\n[" + ", ".join(candidates) + "]"


def _render_public_memory_summary_block(summary: list) -> str:
    lines = ["## 公开历史摘要 (public_memory_summary)"]
    for item in summary:
        if isinstance(item, str):
            lines.append(f"- {item}")
        else:
            lines.append(f"- {_safe_json(item)}")
    return "\n".join(lines)


def _render_events_block(title: str, events: list[PublicEvent]) -> str:
    lines = [f"## {title}"]
    for ev in events:
        bits = [f"D{ev.round}", ev.phase.value, ev.event_type.value]
        if ev.actor:
            bits.append(f"actor={ev.actor}")
        if ev.target:
            bits.append(f"target={ev.target}")
        if ev.role_claim:
            bits.append(f"role_claim={ev.role_claim.value}")
        if ev.claim_result:
            bits.append(
                f"claim_result=(target={ev.claim_result.target},"
                f" alignment={ev.claim_result.claimed_alignment.value})"
            )
        head = " ".join(bits)
        if ev.public_message:
            lines.append(f"- [{head}] \"{ev.public_message}\"")
        else:
            lines.append(f"- [{head}]")
    return "\n".join(lines)


def _render_private_events_block(events: list[PrivateEvent]) -> str:
    lines = ["## 私密事件 (private_events)"]
    for ev in events:
        bits = [ev.event_type.value]
        if ev.round is not None:
            bits.append(f"round={ev.round}")
        if ev.visibility:
            bits.append(f"visibility={ev.visibility.value}")
        if ev.target:
            bits.append(f"target={ev.target}")
        if ev.result:
            bits.append(f"result={ev.result}")
        if ev.teammates:
            bits.append(f"teammates={ev.teammates}")
        lines.append("- " + ", ".join(bits))
    return "\n".join(lines)


def _render_previous_vote_summary_block(summary: dict[str, int]) -> str:
    return "## 票数摘要 (previous_vote_summary)\n" + _safe_json(summary)


def _render_claim_records_block(records: list[ClaimRecord]) -> str:
    lines = ["## 跳身份/查杀台账 (claim_records)"]
    for r in records:
        bits = [f"D{r.round}", r.phase.value, f"actor={r.actor}"]
        if r.claimed_role:
            bits.append(f"claimed_role={r.claimed_role.value}")
        if r.claim_target:
            bits.append(f"claim_target={r.claim_target}")
        if r.claimed_alignment:
            bits.append(f"alignment={r.claimed_alignment.value}")
        if r.is_counter_claim:
            bits.append("counter_claim=True")
        lines.append("- " + ", ".join(bits))
    return "\n".join(lines)


def _render_vote_records_block(records: list[VoteRecord]) -> str:
    lines = ["## 投票台账 (vote_records)"]
    for r in records:
        bits = [f"D{r.round}", r.phase.value, f"stage={r.stage}", f"voter={r.voter}"]
        if r.target:
            bits.append(f"target={r.target}")
        if r.is_revote:
            bits.append("revote=True")
        if r.is_tie_candidate_vote:
            bits.append("tie_candidate=True")
        lines.append("- " + ", ".join(bits))
    return "\n".join(lines)


def _render_rule_hints_block(rule_hints: dict) -> str:
    return "## rule_hints\n" + _safe_json(rule_hints)


def _render_belief_top_suspects_block(suspects: list) -> str:
    return "## belief_top_suspects（主观判断，仅参考）\n" + _safe_json(suspects)


# belief_state.beliefs 里 RoleBelief 的角色键 → 中文标签，固定顺序便于 LLM 阅读 + diff。
_BELIEF_ROLE_LABELS: tuple[tuple[str, str], ...] = (
    ("werewolf", "狼"),
    ("seer", "预言家"),
    ("witch", "女巫"),
    ("hunter", "猎人"),
    ("villager", "平民"),
)


def _render_belief_full_block(belief_state: dict) -> str:
    """渲染完整角色概率（v2 rich role belief）。

    ``belief_top_suspects`` 只浓缩了狼人嫌疑榜，会把 seer/witch/hunter/villager
    概率丢掉（context_assembler ``_top_werewolf_suspects``）。这里把
    ``belief_state.beliefs`` 的完整角色分布按玩家逐行渲染，确保 v2 算出的
    rich belief 真正进入 LLM 主链路，而不是只停在 updater / 离线指标里。

    紧凑成「P1: 狼45% 预言家10% …」一行一个玩家，控制 token 预算。
    """
    beliefs = belief_state.get("beliefs") or {}
    if not beliefs:
        return ""
    lines = ["## 角色概率判断 (belief_state，主观、仅参考)"]
    for pid, role_belief in beliefs.items():
        parts = [
            f"{label}{round(float(role_belief.get(key, 0.0)) * 100)}%"
            for key, label in _BELIEF_ROLE_LABELS
        ]
        suffix = ""
        if role_belief.get("locked"):
            reason = role_belief.get("lock_reason")
            suffix = f"（已锁定{'：' + reason if reason else ''}）"
        lines.append(f"- {pid}: " + " ".join(parts) + suffix)
    return "\n".join(lines)


def _render_output_instruction_block(context: AgentContext) -> str:
    """输出模板按当前 ``allowed_actions`` 动态裁剪字段（A 5/26 18:00-18:04 拍板）。

    与 ``shared/output_contract.md``（B 5/26 18:47 推的全局输出契约）分工：
    - shared 给出 **静态全局规则**：JSON shape / 字段规则 / 标准 action_type / one-shot 示例 / 红线
    - 本 block 只给 **动态 phase-specific 字段指引**：当前 ``allowed_actions`` 每个 action 该填哪些字段

    避免重复 shared 已经说过的 schema（B 18:47 提醒 "可以保留一个很短的最终提醒，但不要再写一份独立 schema"）。
    """
    allowed = list(context.allowed_actions)
    if not allowed:
        return (
            "## 当前阶段动作指引\n"
            "**当前 phase 无合法 action**（`allowed_actions` 空）。系统会自动跳过；"
            "如果被迫返回 JSON 则按 shared/output_contract.md 输出 `{\"action_type\": \"skip\"}`。"
        )

    header = (
        "## 当前阶段动作指引\n"
        "请按 system 顶部 `v0 LLM Global Output Contract` 输出统一 `AgentAction` JSON。\n"
        f"当前 `allowed_actions` = [{', '.join(a.value for a in allowed)}]，"
        "从下列 case 中选 1 个 `action_type` 并填对应字段（其余字段保持 `null`）：\n"
    )

    cases: list[str] = []
    for action in allowed:
        cases.append(_render_action_example(action, context))
    body = "\n\n".join(cases)

    return header + body


def _render_action_example(action, context: AgentContext) -> str:
    """单个 action 的 JSON 字段示例 + 该 action 的字段约束说明。"""
    from contracts import ActionType

    if action == ActionType.NIGHT_KILL_NOMINATE:
        return (
            "- **night_kill_nominate**（狼夜刀）：\n"
            '  `{"action_type": "night_kill_nominate", "target": "P<n>"}`\n'
            "  → target 必须存活、非自己、**非 `private_events.teammates` 中的狼队友**"
        )
    if action == ActionType.CHECK:
        return (
            "- **check**（预言家查验）：\n"
            '  `{"action_type": "check", "target": "P<n>"}`\n'
            "  → target 必须存活、非自己；尽量不重复 `private_events.SEER_CHECK_RESULT` 已查过的人"
        )
    if action == ActionType.SAVE:
        return (
            "- **save**（女巫救）：\n"
            '  `{"action_type": "save", "target": "P<n>"}`\n'
            "  → target = 当晚刀口（看 `private_events` 里的 `witch_kill_target_info.target`）；"
            "**首夜（round=1）可救自己；2 夜+ 不能自救**"
        )
    if action == ActionType.POISON:
        return (
            "- **poison**（女巫毒）：\n"
            '  `{"action_type": "poison", "target": "P<n>"}`\n'
            "  → target 必须存活、非自己；缺高置信狼嫌疑时优先 `skip`"
        )
    if action == ActionType.HUNTER_SHOOT:
        return (
            "- **hunter_shoot**（猎人开枪/不开枪）：\n"
            '  `{"action_type": "hunter_shoot", "target": "P<n>"}`（开枪）或 '
            '`{"action_type": "hunter_shoot", "target": null}`（不开枪）\n'
            "  → 开枪 target 必须存活、非自己；无高置信嫌疑时 target=null"
        )
    if action == ActionType.SPEAK:
        return (
            "- **speak**（发言）：\n"
            '  `{"action_type": "speak", "public_message": "...", '
            '"role_claim": null | "<seer|witch|hunter|villager|werewolf>", '
            '"claim_result": null | {"target": "P<n>", "claimed_alignment": "werewolf|villager"}}`\n'
            "  → `public_message` 必填；跳身份时填 `role_claim`；"
            "公开报查杀/金水时填 `claim_result`（要和 `private_events.SEER_CHECK_RESULT` 一致，不能编）"
        )
    if action == ActionType.VOTE:
        tie_clause = ""
        if context.tie_candidates:
            tie_clause = (
                f"，且必须 ∈ `tie_candidates` = {list(context.tie_candidates)}"
            )
        return (
            "- **vote**（投票）：\n"
            '  `{"action_type": "vote", "target": "P<n>"}`\n'
            f"  → target 必须存活、非自己{tie_clause}"
        )
    if action == ActionType.SKIP:
        return (
            "- **skip**（跳过/弃权）：\n"
            '  `{"action_type": "skip", "target": null}`\n'
            "  → 无合法目标或女巫两药用尽时用 skip；不要在能行动时偷懒 skip"
        )
    # 兜底：未识别 action 仍给出基本模板（前向兼容）
    return (
        f"- **{action.value}**：\n"
        f'  `{{"action_type": "{action.value}"}}`'
    )


def _safe_json(value) -> str:
    """对任意结构尝试 JSON 序列化，失败回退 repr。"""
    try:
        return json.dumps(value, ensure_ascii=False, default=_json_default)
    except (TypeError, ValueError):
        return repr(value)


def _json_default(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "value"):
        return obj.value
    return str(obj)
