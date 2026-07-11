"""B 侧 PromptPolicy。

PromptPolicy 负责描述“不同角色/阶段应该怎么思考和输出”，但不负责：
- 拼装 AgentContext；
- 调用 LLM；
- 解析 LLM 输出；
- 生成 GameEvent 或写 Store。

最终进入 LLM 的 prompt 通常由 C 的 Runtime 将本模块提供的策略 prompt
和 C 渲染的 AgentContext 合并得到。
Legacy/mock-only prompt policy. Real LLM prompt source is agent_policy/prompts/*.md via PromptTemplateLoader.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts import ActionType, AgentContext, Phase, Role


_SHARED_OUTPUT_CONSTRAINTS = """\
输出约束：
1. 只能从 AgentContext.allowed_actions 中选择一个 action_type。
2. 必须输出一个 AgentAction JSON 对象，不要输出 Markdown、解释段落或多余文本。
3. action_type、target、public_message、role_claim、claim_result 必须和当前阶段匹配。
4. reason_summary 只能写简短可审计理由，不要输出完整隐藏推理链。
5. 如果没有合法目标，优先选择 allowed_actions 中的 skip；如果不能 skip，再选择安全发言。
"""


@dataclass(frozen=True)
class PromptPolicySpec:
    """PromptPolicy 的轻量描述，供 runtime / trace 记录版本信息。"""

    prompt_policy_id: str
    role: Role
    phase: Phase
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptPolicy:
    """单个角色/阶段的策略 prompt。"""

    prompt_policy_id: str
    role: Role
    phase: Phase
    strategy_prompt: str
    output_constraints: str = _SHARED_OUTPUT_CONSTRAINTS
    metadata: dict[str, str] = field(default_factory=lambda: {"owner": "B"})

    def build_prompt(self, context: AgentContext) -> str:
        """生成给 C Runtime 使用的策略 prompt 片段。"""
        allowed_actions = ", ".join(action.value for action in context.allowed_actions)
        return "\n\n".join(
            [
                self.strategy_prompt.strip(),
                self.output_constraints.strip(),
                "当前动作选择范围：",
                f"- agent_id: {context.agent_id}",
                f"- role: {context.role.value}",
                f"- phase: {context.phase.value}",
                f"- allowed_actions: [{allowed_actions}]",
            ]
        )

    def to_spec(self) -> PromptPolicySpec:
        return PromptPolicySpec(
            prompt_policy_id=self.prompt_policy_id,
            role=self.role,
            phase=self.phase,
            metadata=dict(self.metadata),
        )


class PromptPolicyRegistry:
    """按角色和阶段获取 PromptPolicy。"""

    def __init__(self) -> None:
        self._policies: dict[tuple[Role, Phase], PromptPolicy] = _build_default_policies()

    def get(self, role: Role, phase: Phase) -> PromptPolicy:
        try:
            return self._policies[(role, phase)]
        except KeyError:
            return _generic_policy(role, phase)

    def build_prompt(self, context: AgentContext) -> str:
        return self.get(context.role, context.phase).build_prompt(context)


def _build_default_policies() -> dict[tuple[Role, Phase], PromptPolicy]:
    policies = [
        PromptPolicy(
            prompt_policy_id="werewolf_night_v1",
            role=Role.WEREWOLF,
            phase=Phase.NIGHT_WEREWOLF,
            strategy_prompt="""\
你是狼人。夜晚行动时，你的目标是帮助狼人阵营扩大优势并隐藏队友。
当前阶段只能输出 night_kill_nominate。
优先考虑公开跳预言家、强势带队、或对狼人阵营威胁高的非狼队友。
不要选择 private_events.teammates 中的狼队友。
如果信息不足，选择一个存活且不是自己的非队友目标。
""",
        ),
        PromptPolicy(
            prompt_policy_id="seer_night_v1",
            role=Role.SEER,
            phase=Phase.NIGHT_SEER,
            strategy_prompt="""\
你是预言家。夜晚行动时，你的目标是用 check 获取最大信息量。
当前阶段只能输出 check。
优先查验未查验、发言矛盾、投票异常、或影响局势判断的存活玩家。
不要查验自己；尽量不要重复查验 private_events 中已有结果的目标。
""",
        ),
        PromptPolicy(
            prompt_policy_id="witch_night_v1",
            role=Role.WITCH,
            phase=Phase.NIGHT_WITCH,
            strategy_prompt="""\
你是女巫。夜晚行动时，需要在 save、poison、skip 中谨慎选择。
如果 private_events 中提供 witch_kill_target_info，结合药品状态和局势判断是否救人。
不要随机使用毒药；没有明确高价值收益时优先 skip。
如果规则不允许同夜救毒，不要同时表达两个动作。
""",
        ),
        PromptPolicy(
            prompt_policy_id="day_speech_v1",
            role=Role.VILLAGER,
            phase=Phase.DAY_DISCUSSION,
            strategy_prompt="""\
现在是白天发言阶段。你需要输出 speak。
白天发言应围绕公开信息、投票记录、发言矛盾和你的身份视角展开。
发言要自然、简洁、符合角色信息边界；不知道的信息不要假装知道。
""",
        ),
        PromptPolicy(
            prompt_policy_id="day_vote_v1",
            role=Role.VILLAGER,
            phase=Phase.DAY_VOTE,
            strategy_prompt="""\
现在是白天投票阶段。你需要输出 vote。
优先结合 public_events、belief_top_suspects、公开查杀、发言矛盾和投票历史选择目标。
只能投存活且不是自己的玩家；如果存在公开查杀且目标合法，可以优先考虑。
""",
        ),
        PromptPolicy(
            prompt_policy_id="tie_revote_v1",
            role=Role.VILLAGER,
            phase=Phase.DAY_TIE_REVOTE,
            strategy_prompt="""\
现在是平票后的再次投票阶段。你需要输出 vote。
只能从 AgentContext.tie_candidates 中选择合法存活目标。
优先选择嫌疑更高、发言更矛盾或对己方阵营威胁更高的候选人。
""",
        ),
        PromptPolicy(
            prompt_policy_id="hunter_shoot_v1",
            role=Role.HUNTER,
            phase=Phase.HUNTER_SHOOT,
            strategy_prompt="""\
你是猎人，当前阶段需要输出 hunter_shoot。
如果没有足够把握命中狼人，可以选择不开枪，对应 target 为 null。
如果选择开枪，只能选择存活且不是自己的目标。
""",
        ),
        PromptPolicy(
            prompt_policy_id="last_words_v1",
            role=Role.VILLAGER,
            phase=Phase.EXILE_LAST_WORDS,
            strategy_prompt="""\
现在是遗言阶段。你需要输出 speak。
遗言应简短总结自己的视角、投票理由和可公开的信息，不要透露自己不应知道的内容。
""",
        ),
    ]
    return {(policy.role, policy.phase): policy for policy in policies}


def _generic_policy(role: Role, phase: Phase) -> PromptPolicy:
    action_hint = _generic_action_hint(phase)
    return PromptPolicy(
        prompt_policy_id=f"{role.value}_{phase.value.lower()}_generic_v1",
        role=role,
        phase=phase,
        strategy_prompt=f"""\
你正在扮演 {role.value}，当前阶段是 {phase.value}。
请根据 AgentContext 中的公开信息、私有信息和 allowed_actions 做一个合法决定。
当前阶段优先输出 {action_hint}，并保持信息边界，不要使用 AgentContext 之外的信息。
""",
    )


def _generic_action_hint(phase: Phase) -> str:
    return {
        Phase.NIGHT_WEREWOLF: ActionType.NIGHT_KILL_NOMINATE.value,
        Phase.NIGHT_SEER: ActionType.CHECK.value,
        Phase.NIGHT_WITCH: ActionType.SKIP.value,
        Phase.DAY_DISCUSSION: ActionType.SPEAK.value,
        Phase.DAY_TIE_DISCUSSION: ActionType.SPEAK.value,
        Phase.DAY_VOTE: ActionType.VOTE.value,
        Phase.DAY_TIE_REVOTE: ActionType.VOTE.value,
        Phase.HUNTER_SHOOT: ActionType.HUNTER_SHOOT.value,
        Phase.EXILE_LAST_WORDS: ActionType.SPEAK.value,
    }.get(phase, ActionType.SPEAK.value)
