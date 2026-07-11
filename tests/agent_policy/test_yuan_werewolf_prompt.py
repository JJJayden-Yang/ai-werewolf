"""W4（Yuan）：狼人 v0 prompt 对齐守卫。

prompts/werewolf/v0_free_llm.md 是 A 独占的狼人 v0 行为说明（全阶段）。

⚠️ 当前现实（S7 前必须解决的 B/C 协调项）：B 的 `PromptPolicyRegistry` 只为狼定义了
`(WEREWOLF, NIGHT_WEREWOLF)` 专属 policy；白天/平票/遗言都落到 `_generic_policy` 通用
兜底，**既不读本 prompt 文件，也没有狼专属的"避队友/伪装"白天策略**。本测试**显式断言
这个现实**（而非假装"全阶段已接入"），将来 B 补齐狼白天 policy 或 C 改走 prompt 文件时，
本测试会失败、强制同步更新。
"""

from pathlib import Path

from contracts import Phase, Role

from agent_policy.prompt_policies import PromptPolicy, PromptPolicyRegistry

WEREWOLF_PROMPT = (
    Path(__file__).resolve().parents[2]
    / "agent_policy"
    / "prompts"
    / "werewolf"
    / "v0_free_llm.md"
)


def test_werewolf_v0_prompt_file_covers_core_contract():
    text = WEREWOLF_PROMPT.read_text(encoding="utf-8")

    # 队友信息源（W1）：队友来自 private_events.teammates
    assert "teammates" in text
    # 全阶段动作覆盖
    for marker in ("night_kill_nominate", "speak", "vote"):
        assert marker in text
    # 红线：只在 allowed_actions 内选、不输出思维链。
    # （"v0 不注入 belief" 的红线已从 prompt 文本移除——狼视角物理上看不到 belief，写进 prompt
    #  对 LLM 是废话；信息隔离由代码层 + CLAUDE.md 红线保证，不靠 prompt 自述。）
    assert "allowed_actions" in text
    assert "chain-of-thought" in text or "推理链" in text


def test_only_night_has_dedicated_werewolf_policy_day_phases_are_generic_fallback():
    """显式记录现状：仅 NIGHT_WEREWOLF 是狼专属 policy，白天/平票/遗言都是 generic 兜底。

    不要把这理解成"C 已能按全阶段取到 A 的狼 prompt"——白天等阶段走的是
    `_generic_policy`（prompt_policy_id 以 `_generic_v1` 结尾），不读 v0_free_llm.md。
    """
    registry = PromptPolicyRegistry()

    night = registry.get(Role.WEREWOLF, Phase.NIGHT_WEREWOLF)
    assert night.prompt_policy_id == "werewolf_night_v1"  # 专属
    assert "teammates" in night.strategy_prompt  # 落实避队友（W1/W2）

    generic_fallback_phases = [
        Phase.DAY_DISCUSSION,
        Phase.DAY_VOTE,
        Phase.DAY_TIE_DISCUSSION,
        Phase.DAY_TIE_REVOTE,
        Phase.EXILE_LAST_WORDS,
    ]
    for phase in generic_fallback_phases:
        policy = registry.get(Role.WEREWOLF, phase)
        assert isinstance(policy, PromptPolicy)
        assert policy.role == Role.WEREWOLF
        assert policy.phase == phase
        # 走的是通用兜底，不是狼专属 policy（缺"伪装/投票避队友"等白天策略）
        assert policy.prompt_policy_id.endswith("_generic_v1")
        assert "teammates" not in policy.strategy_prompt
