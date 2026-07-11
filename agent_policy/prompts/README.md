# Agent Prompt / Policy 对接说明

这个目录由 B 侧维护，用来沉淀不同角色、不同阶段的策略 prompt。

当前第一版 prompt 文本先放在 `agent_policy/prompt_policies.py` 中，便于测试和快速迭代。
后续如果 prompt 变长或需要版本化文件，可以把文本迁移到本目录，再让 C 的
`PromptTemplateLoader` 按 `prompt_policy_id` 加载。

## 1. 边界约定

- B 负责：角色策略、阶段策略、输出约束、prompt_policy_id。
- C 负责：渲染 `AgentContext`、调用 LLM、解析输出、兜底、记录 trace。
- A 负责：校验 `AgentAction` 并结算为 `GameEvent`。

B 不生成 `GameEvent`，不读取 `TruthState / GameSession / Store`。B 只消费
`AgentContext`，输出或帮助构造 `AgentAction`。

## 2. A 如何使用 B 的 Mock Agent

A / Supervisor 只需要依赖 `BaseAgent.act(context: dict) -> dict` 这个外部接口。

当前可选 mock：

```python
from agent_policy import LegalRandomMockAgent, HeuristicMockAgent, RoleStrategyMockAgent
```

建议 Phase2 6 人 Mock MVP 优先用：

```python
agent = RoleStrategyMockAgent()
```

它的内部链路是：

```text
act(context dict)
  ↓
AgentContext.model_validate(context)
  ↓
RoleStrategyRegistry.get(context.role)
  ↓
WerewolfStrategy / SeerStrategy / WitchStrategy / VillagerStrategy
  ↓
AgentAction dict
```

A 侧拿到 `AgentAction` 后继续走：

```text
RuleValidator
  ↓
ActionResolver
  ↓
GameEvent
```

本地验证命令：

```bash
pytest tests/supervisor/test_agent_policy_integration.py -q
```

这个测试会分别验证 `LegalRandomMockAgent` 和 `RoleStrategyMockAgent` 都能接入
`GameEngine + Supervisor` 跑完 6 人局。

## 3. C 如何使用 B 的策略组件

C 的正式 runtime 不需要直接调用 `RoleStrategy.decide`，除非是在 fallback / mock 模式。

LLM 路径建议是：

```text
C ContextAssembler 生成 AgentContext
  ↓
C Runtime 根据 role + phase 读取 B 的 PromptPolicy
  ↓
C Runtime 拼 PromptPolicy + AgentContext
  ↓
C LLMProvider 调模型
  ↓
C ActionParser / ActionCanonicalizer 解析输出
  ↓
C 可复用 B 的 actions.py builder 构造 AgentAction
  ↓
A RuleValidator 校验
```

C 可用的 B 侧入口：

```python
from agent_policy import (
    PromptPolicyRegistry,
    RoleStrategyRegistry,
    build_vote_action,
    build_speak_action,
    build_wolf_nomination_action,
    build_check_action,
    build_save_action,
    build_poison_action,
    build_skip_action,
)
```

### 3.1 PromptPolicy

```python
prompt_policy = PromptPolicyRegistry().get(context.role, context.phase)
prompt_text = prompt_policy.build_prompt(context)
prompt_spec = prompt_policy.to_spec()
```

用途：

```text
prompt_text：给 C Runtime 拼 LLM messages
prompt_spec.prompt_policy_id：给 trace / prompt version 记录策略版本
```

注意：`PromptPolicy` 不调用 LLM，不解析输出，只提供 B 的角色策略说明和输出约束。

### 3.2 RoleStrategy

```python
strategy = RoleStrategyRegistry().get(context.role)
action = strategy.decide(context)
```

用途：

```text
1. mock / baseline
2. LLM 输出坏掉时的 fallback 候选
3. 本地不接模型时验证 A/C 流程
```

### 3.3 actions.py builder

C 解析 LLM 输出后，如果已经拿到标准意图：

```text
action_type
target
public_message
role_claim
claim_result
```

可以复用 B 的 builder 构造 `AgentAction`，也可以直接 `AgentAction.model_validate`。
复用 builder 的好处是 metadata / reason_summary 风格更统一。

## 4. C 装配 Context 时需要给 B 的字段

6 人 Mock MVP 最小要求：

```text
game_id
agent_id
role
round
phase
allowed_actions
visible_players
private_events.teammates               # 狼人夜晚
private_events.seer_check_result       # 预言家夜晚历史查验
private_events.witch_kill_target_info  # 女巫夜晚刀口，若规则允许知道
public_events 或 recent_public_events  # 白天发言 / 投票
```

9 人标准局预留：

```text
tie_candidates                         # 平票发言 / 再投
belief_state / belief_top_suspects      # v1 Belief-Guided Agent
strategy_memory                         # v2 可选
HUNTER_SHOOT 相关上下文                 # 猎人阶段
```

更完整字段说明见：

```text
docs/b_context_requirements.md
```

## 5. Phase2 推荐合并顺序

```text
1. A 使用 RoleStrategyMockAgent 跑 6 人整局 smoke
2. C 的 ContextAssembler 确保输出字段满足 docs/b_context_requirements.md
3. C Runtime 暂时可用 RoleStrategyMockAgent / FakeLLMProvider 做联调
4. LLM 接入时，C Runtime 读取 PromptPolicyRegistry
5. LLM 输出解析后，交给 A RuleValidator 做最终合法性校验
```

验证命令：

```bash
pytest tests/agent_policy -q
pytest tests/supervisor/test_agent_policy_integration.py -q
pytest -q
```
