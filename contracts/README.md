# contracts/ —— 跨模块契约（Schema + Interface）说明

> 三人共同拥有，**Day 1 冻结**。本文件解释这套契约怎么定的、为什么这么定、以及**要改的时候怎么改才不会让别人崩**。
>
> 事实来源：`finalPlan/Schema_v2_1.md`（Schema）与 `finalPlan/Interface_v2_1.md`（Interface）。代码与文档冲突时，以文档为准 + 在这里记一笔。

---

## 1. 这是什么

`contracts/` 是 A/B/C 之间唯一的通信契约。规则：

- **跨模块只通过这里的 schema / 枚举 / 接口签名**，不引用对方内部对象。
- A（`game_core/`）、B（`agent_policy/`）、C（`agent_runtime/ context/ stores/ api/ frontend/`）都依赖它，所以它一动，三个人都受影响。
- 因此：**冻结优先，能不改就不改；非改不可时走第 4 节流程。**

文件：
- `enums.py` —— 11 个枚举（Role / Camp / PlayerStatus / Phase / ActionType / EventType / AgentVersion / ClaimedAlignment / Visibility / DeathCause / DeviationOutcome）。
- `schemas.py` —— `Schema_v2_1.md` 第 35 节「必须冻结」清单的全部 Pydantic 模型。
- `__init__.py` —— 统一导出，用 `from contracts import GameConfig, Phase, ...`。

---

## 2. 契约锁：为什么所有模型都继承 `ContractModel`

```python
class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=(), populate_by_name=True)
```

- **`extra="forbid"`** —— 最重要的一层锁。谁往 schema 里塞一个没声明的字段，构造时**直接报 `ValidationError`**。这能挡住并行开发中最常见的契约腐烂：某个模块为了图方便，私自往 `AgentContext` / `AgentAction` 里加字段，结果别人不知道。**想加字段 = 必须改 `schemas.py` = 必须走流程。**
- **`protected_namespaces=()`** —— pydantic 默认禁止 `model_` 开头的字段名。我们有 `model_name` / `model_id` / `model_display_name`（来自文档），所以关掉这个保护。
- **`populate_by_name=True`** —— 配合下面 `model_config` 别名用，既能用字段名也能用 JSON 键构造。

---

## 3. 我做的关键决策与原因（队友重点看这节）

下面这些是**文档没法直接照抄、我必须做判断**的地方。改之前先理解为什么。

### 3.1 `GameConfig` 里的 `model_config` 键 → python 字段叫 `model_settings`
**原因**：`model_config` 是 pydantic v2 的保留字（就是上面那个 `ConfigDict`），不能再当字段名。
**做法**：python 字段名设为 `model_settings`，加 `alias="model_config"`。**对外 JSON 键仍然是 `model_config`，契约没变**；只是在 python 里访问要写 `config.model_settings`。

### 3.2 `agent_version` 一律收 `str`（⚠️ 待全队定）
**原因**：文档自相矛盾 —— 枚举清单（1.7 节）写长名 `v0_free_llm`，但**每个具体 JSON 示例**（GameConfig / PromptVersion / per_agent_metrics）都用短名 `v0 / v1 / v2`。
**做法**：所有 `agent_version` 字段先收 `str`，两种都能进；`AgentVersion` 枚举保留但不强制。
**需要决定**：团队统一用短名还是长名，定了之后我把字段类型收紧成枚举。见第 5 节。

### 3.3 一批字段用 `dict[str, Any]` 而不是强类型
**原因**：这些是高度异构、还会演进的「审计 blob」，过早强类型会变成**假冻结**——每加一个 metric 就要改 schema、触发一次全队同步，得不偿失。
**涉及**：`GameEvent.payload`、`AgentContext` 的 `belief_state / belief_top_suspects / strategy_memory / rule_hints / compressed_context / public_memory_summary`、`AgentAction.belief_used / metadata`、`AgentDecisionTrace` 的三个 summary、`EvaluationReport.metrics`、`BadCaseReport.affected_metrics`、`AgentTuningTrace.before/after_metrics`、`ReplayData` 的几个列表。
**边界**：稳定、跨模块强依赖的结构（GameConfig / TruthState / AgentAction 顶层 / GameEvent 顶层 / BeliefState 等）**全部强类型**，只有上面这些末梢 blob 放宽。

### 3.4 没有加 `frozen=True`（实例可变）
**原因**：`TruthState` 在一局游戏里要被 Engine 反复改写（写 `kill_target`、改 `status` 等）。全局 `frozen=True` 会逼着每次改都深拷贝，很别扭。
**说明**：我们要冻结的是**「schema 形状」**（靠 `extra="forbid"` + 快照测试 + review），不是「实例不可变」。这是两回事。若以后想给 `AgentAction` / `GameEvent` 这类纯值对象单独加 `frozen=True`，可以，但属于另一层增强，不在本次范围。

### 3.5 `PlayerState.player_id` 设为可选
**原因**：`TruthState.players` 是 `dict[str, PlayerState]`，玩家编号已经是 dict 的 key，条目里就不再带 `player_id`（文档示例也是这样）。所以 `player_id` 可选，独立使用 `PlayerState` 时再填。同理 `camp` 可选、`status` 默认 `alive`、`vote_weight` 默认 `1.0`。

### 3.6 大小写约定
`Phase` 值用**大写**（`NIGHT_WEREWOLF`），其余枚举（Role/Camp/PlayerStatus/ActionType/EventType）用**小写**，跟 `Schema_v2_1.md` 一致。`created_at` 先用 `str`（ISO 字符串），避免时区解析的坑。

---

## 4. 怎么改契约（变更流程，必须遵守）

**任何 schema 字段 / 枚举值 / 接口签名的增删改，都算契约变更。** 不要在自己分支里悄悄改完就合。

流程：

1. **先沟通**：开 issue / 群里说，讲清楚改什么、为什么、影响谁。
2. **三人确认**：A/B/C 都同意（这是 `finalPlan` 里写死的规矩）。
3. **同一个 MR 里一起改**：
   - 改 `enums.py` / `schemas.py`；
   - 更新对应的 JSON fixtures（`contracts/fixtures/`，建好后）；
   - 更新 schema 快照（`test_contracts_frozen.py`，建好后——它会让「未经更新快照的改动」在 CI 直接红）；
   - 在本文件第 3 节追加一条「决策与原因」，或在下面第 6 节变更记录里记一笔。
4. **bump 版本**：在变更记录写明日期 + 改了什么 + 谁同意的。

**什么算 breaking（会让别人崩，尤其小心）**：删字段、改字段名、改类型、收紧枚举、改方法签名。加可选字段（带默认值）相对安全，但仍要走流程让大家知道。

---

## 5. 待全队对齐的开放问题

- [ ] **`agent_version` 短名 vs 长名**（见 3.2）。定了我把类型从 `str` 收紧成 `AgentVersion` 枚举。
- [x] `contracts/fixtures/` JSON 样本（A0 已建：6p/9p config、truth_state、action、event）。
- [x] schema 快照测试 `tests/contracts/test_contracts_frozen.py` + CI `pytest` 门禁（跑全部 `tests/`，已建）。
- [ ] `BeliefRules.yaml`（B 负责）虽列在冻结清单，但属领域逻辑，不在本目录。

---

## 6. Interface（接口签名）同样受冻结约束

除了数据 schema，**模块对外的方法签名也是契约**，定义在 `finalPlan/Interface_v2_1.md`，改动走第 4 节同样的流程。各 owner 的对外接口（不可随意改签名）：

- **A `game_core/`**：`GameEngine.{get_current_phase, get_required_actors, apply_action, check_win}`、`PhaseController.{get_required_actors, next_phase, should_skip_phase}`、`RuleValidator.validate`、`ActionResolver.resolve_*`、`HunterShootResolver.maybe_enter_hunter_shoot`、`WinChecker.check`、`EventEmitter.emit`。
- **A 主导 `supervisor/`**（B/C 对接）：`Supervisor.{run_game, run_phase, call_agent_with_retry, validate_or_fallback, apply_actions, append_events, trigger_belief_update}`。
- **B `agent_policy/`**：`BaseAgent.act`、`RoleAgent`、`MockAgent`（LegalRandomMockAgent / HeuristicMockAgent）。
- **C**：`ContextAssembler.build_context`、`BeliefStateStore.{get, save, get_history}`、`EventStore.{append, append_many, list_by_game, get}`、`LLMProvider.generate`、`ActionParser.parse`、`ActionCanonicalizer.canonicalize`、`FallbackPolicy.apply`。
- **B/C**：`RealtimeBeliefUpdater.update`、`PostGameAnalyzer.analyze`、`DeviationOutcomeResolver.resolve`、`BadCaseDetector.detect`、`LeaderboardRunner.aggregate`。

注意 `GameSession` / `ValidationResult` / `WinCheckResult` **不在冻结清单**，是 A 自有类型（见 `game_core/types.py`），A 可自行调整。

---

## 7. 变更记录

| 日期 | 改动 | 原因 | 同意人 |
|---|---|---|---|
| 2026-05-21 | 初版：按 Schema_v2_1 第 35 节建立全部冻结 schema + 11 枚举；决策见第 3 节 | Day 1 契约冻结 | （待补） |
| 2026-05-21 | 接口归属对齐：Interface §1 表补全 + 加 `supervisor/`（A 主导）、§5 标注 Agent Policy(B)/Agent Runtime(C)；同步 Architecture §10.2、CLAUDE.md、本文件 §6。草案见内部设计记录 | 修复目录表与详细接口对不上（队友提出） | A / B / C |
| 2026-05-25 | `PrivateEvent` 新增可选字段 `round: int \| None = None`（additive，非 breaking）。同步更新冻结快照 `frozen_contracts.json` | 私密事件需轮次戳让消费方区分"当前轮 vs 历史"：女巫取 max round 拿当晚刀口、预言家忽略 round 读全量查验史、狼队友 roster 跨轮共用。方案选"加字段"而非"ContextAssembler 按轮过滤"——后者会误伤跨轮的 WOLF_NOMINATION/SEER_CHECK_RESULT，需白名单维护 | A / B / C |
| 2026-05-25 | **v2.2 契约预留 pass（9P + Observability + Learning，全 additive，非 breaking）**：① 新增枚举 `FailureCategory`/`VisibilityLevel`；② 新增 schema `ClaimRecord`/`VoteRecord`/`ReplayPlayer`/`ContextSnapshot`/`BeliefUpdateDelta`/`BeliefUpdateBatch`/`BeliefCurvePoint`/`DecisionQualityFlags`/`OutcomeAttribution`/`RunConfigSnapshot`/`GameRunResult`/`BatchRunReport`/`RunStatus`；③ 已有 schema 加可选字段（`AgentContext.claim_records/vote_records`、`AgentDecisionTrace.context_ref/typed_decision_quality_flags/run_config_snapshot_id/schema_version`、`BadCaseReport.primary_failure_category/contributing_factors/source_type/schema_version`、`PromptVersion.derived_from`、`EvaluationReport.outcome_attribution`、`ReplayData.typed_players/typed_belief_curves`）；④ 新增 `CONTRACT_VERSION="2.2"`。同步重生成冻结快照。**不纳入**：高不确定项 `TimelineItem`/`StrategyInsight`/`RoleStrategyVersion`（造时再加，仍 additive）、ops 遥测 `SystemMetricSample`（走日志/metrics 不进契约）。内部设计记录 | 9 人前一次性预留完整复盘/角色策略/self-construct/自身学习/Bayes/UI/压测所需"数据形状"，让后续阶段"只填数据、不反复改契约"；全 additive 不破 6 人/9 人主链路 | A / B / C |

---

## 8. 各 Owner 专属区（每人写自己固定的一块）

每个 owner 把**自己相关的契约事项**（需求、想提议的字段/接口改动、待确认点、已知坑）写在自己这块，集中、规范、好追溯。

**用法**：
1. 自己区先记「提议 / 进行中 / 待确认」的事项 —— 这只是登记，**不等于改了契约**。
2. 真要动 schema/接口，仍按第 4 节流程（三人确认）。
3. 一旦确认并合并，把该事项**汇总一行到第 7 节全局变更记录**，并把自己区状态标成「已合并」。
4. 状态取值：`提议` / `待确认` / `已确认` / `已合并` / `搁置`。

### 8.A　A —— Game Core（`game_core/`）

**主要关注的契约**：`GameConfig`、`TruthState`、`PlayerState`、`Witch/Hunter/Night/RoundState`、`AgentAction`、`GameEvent`、`PublicEvent`/`PrivateEvent`；枚举 `Phase`/`ActionType`/`EventType`/`Role`/`Camp`/`PlayerStatus`。
**A 自有、不冻结**（可自行改，不必走流程）：`GameSession`/`ValidationResult`/`WinCheckResult`（`game_core/types.py`）。

| 日期 | 事项（需求/改动/待确认） | 关联 schema / 接口 | 状态 |
|---|---|---|---|
| 2026-05-21 | A0 基座：Engine 相关 schema/枚举、fixtures、game_core 骨架、EventEmitter.emit | Engine 相关全部 | 已合并 |
| 2026-05-21 | 接口归属对齐（Supervisor 缺目录、agent_policy 接口归错节、§1 摘要行漏类）；D1=`supervisor/`(A 主导)，D2=RealtimeBeliefUpdater 暂归 `evaluation/`(B 规则) | Interface §1 表、§5 标注、Supervisor 归属 | 已合并 |
| 2026-05-22 | `role_assigned` 暂用方案 A：开局发"无身份锚点"，payload 仅 `player_count`+`role_counts`（公开设定），**不含 pid→role**。真身份赛后由 Replay/PostGameAnalyzer 直接读 TruthState。待确认（给 C）：未来若要"事件流纯重建角色"，需给 `Visibility` 加 `truth_only` 值并约定 ContextAssembler 永不转发——届时走变更流程 | `EventType.role_assigned`、`Visibility` 枚举 | 待确认 |
| 2026-05-22 | A↔C 对接接口（非 contracts schema 改动、非 breaking）：C 经 `SessionProvider.get_session(game_id)` **只读** GameSession 装配 context（禁止透传进 AgentContext）；`allowed_actions` 由 `RuleValidator.allowed_actions(phase)` 单一真相源提供（C 不复制逻辑）；`tie_candidates` 直接读 `round_state.tie_candidates`，不从 events 反推 | `game_core`：`SessionProvider`、`GameEngine.get_session`、`RuleValidator.allowed_actions` | 已确认 |
| 2026-05-23 | 狼队友名单传递（W1，非 schema 改动）：开局 `GameEngine.emit_wolf_teammates` 播一条 `EventType.WOLF_NOMINATION` + `visibility=PRIVATE_TO_WOLVES`，`payload.teammates=[狼 pid]`。**载体特意选 WOLF_NOMINATION**——它是 C `VisibilityRuleSpec` 里唯一「对全狼公开」的私密事件类型；`role_assigned` 被 C 的 AI 可见白名单挡住（防泄漏），不能传队友。C 的 `_to_private_event` 已读 `payload.teammates` → `AgentContext.private_events[*].teammates`。开局发一次即可（ContextAssembler 读全量历史、ContextWindowPolicy 不裁 private_events，跨轮持续可见）。**C 已确认（2026-05-23）**：可见性过滤就绪，C 侧端到端测试 `tests/context/test_visibility_rules.py:115`（`test_wolf_sees_teammate_nomination`）覆盖此链路；A 侧回归 `tests/supervisor/test_yuan_wolf_teammates_visibility.py`。**接线不变量（A/supervisor 侧保证）**：真实链路里传给 `Supervisor` 的 EventSink 与 `ContextAssembler(event_store=...)` 必须是**同一个 EventStore 实例**，否则狼读不到开局那条 teammates 事件（C 侧无需改代码）。配对见 §8.C C 登记的"setup 事件聚合过滤约定"（`event_type=WOLF_NOMINATION & actor is None & payload.teammates 非空 → 跳过`） | `EventType.WOLF_NOMINATION`、`Visibility.PRIVATE_TO_WOLVES`、`PrivateEvent.teammates`、`GameEngine.emit_wolf_teammates` | 已确认 |
| 2026-05-23 | 狼人 v0 prompt 接线缺口（**S7 前必须解决，非阻塞**，owner=B/C）：A 已把全阶段狼行为写进 `agent_policy/prompts/werewolf/v0_free_llm.md`（含白天伪装、投票避队友、平票优先非队友、遗言不暴露），但 B 的 `PromptPolicyRegistry` 目前只有 `(WEREWOLF, NIGHT_WEREWOLF)` 专属 policy，**白天/平票/遗言都落 `_generic_policy` 兜底**，既不读该 prompt 文件、也缺狼专属白天策略。S7 前二选一：① B 补 `(WEREWOLF, DAY_DISCUSSION/DAY_VOTE/DAY_TIE_REVOTE/EXILE_LAST_WORDS)` PromptPolicy；② C Runtime 直接消费 prompt 文件作狼行为权威。否则真实 LLM 白天投票不会继承 W2 的"公开查杀队友时不跟投"规则。现状已被 `tests/agent_policy/test_yuan_werewolf_prompt.py` 显式钉住（补齐后该测试会失败、强制同步） | `agent_policy/prompt_policies.py`、`agent_policy/prompts/werewolf/v0_free_llm.md` | 待确认 |
| 2026-05-25 | `PrivateEvent` 加可选 `round: int \| None = None`（女巫刀口"当前轮"消歧 P0#1，方案2）。B 拍板、C 同意。配套（非本次契约提交，各自分支）：C `_to_private_event` 加 `round=ev.round`；B `witch.py _witch_kill_target` 取 max round；A 待这些落地后把 `emit_witch_kill_info` 接进 run_game。已更新冻结快照 | `PrivateEvent.round`、`schemas.py`、`frozen_contracts.json` | 已合并 |
| 2026-05-25 | **A 代表三方执行 v2.2 契约预留 pass**（三方已认可方案）：新增 13 schema + 2 枚举 + 若干已有 schema 可选字段 + `CONTRACT_VERSION`，全 additive。详见 §7 本日第二行 + 内部设计记录。各 owner 后续在自己模块"填数据"（B：BeliefUpdateBatch/DecisionQualityFlags/OutcomeAttribution；C：ReplayPlayer/ContextSnapshot/Claim·VoteRecord/RunStatus/GameRunResult/BatchRunReport/RunConfigSnapshot/BeliefCurvePoint），形状已冻结、无需再改 schema | 见 §7 | 已合并 |
| 2026-05-26 | **v0 接线三件（A 侧已就绪 @ `Phase4/Yuan 0ddf675`，待 B/C 接）**：① **狼人 v0 prompt 升中高**已提交；但**确认现状：prompt 文件无任何代码消费**——`PromptPolicyRegistry` 用硬编码 `werewolf_night_v1`/`_generic_policy`、`agent_runtime/prompt_template_loader.py` 还是骨架。推荐**方案①：C 的 `PromptTemplateLoader` 读 `prompts/<role>/v0_free_llm.md` + 注入序列化 AgentContext 数据**（探针实证：不注入数据 LLM 会瞎选/刀队友，注入后才刀对；样例 `scripts/Yuan_local/yuan_llm_probe.py::_render_context_data`）。owner=B/C 二选一。② **shadow belief hook**：`Supervisor.trigger_belief_update` 已接 `BeliefUpdater` Protocol（逐事件 `update(game_id, event_id)`），默认 None=no-op，**待 B 的 `RealtimeBeliefUpdater` drop-in**（shadow 模式、不注入 context）。③ **女巫刀口接线 + 第一夜自救规则**：`emit_witch_kill_info` 在 NIGHT_WITCH、build_context 前下发（PRIVATE_TO_WITCH, actor=None），受 `Supervisor(deliver_witch_kill_info=)` 开关、**默认关**→mock baseline 零影响。**配套引擎改动（A 已做）**：`RuleValidator` 放开"女巫仅第一夜可自救"（`round==1` 时 save 允许 target==自己；结算侧 `kill_target==saved_target` 即不死，零额外改动）。**这是既定规则、不再迭代**——故 **B 的 mock `decide_witch_night` 不要加"绝不自救"守卫**，而应：round 1 被刀→自救（合法且正确）；round≥2 无解药 / 唯一选项是非法自救→`skip`（避免 fallback）。待 **C**：v0 LLM wiring 显式传 `deliver_witch_kill_info=True` + EventSink 与 `ContextAssembler(event_store=)` 用同一 EventStore 实例 | `supervisor`(trigger_belief_update / deliver_witch_kill_info)、`game_core/rule_validator.py`、`agent_runtime.PromptTemplateLoader`、`agent_policy.{prompt_policies, roles/witch}` | 待对接 |
| 2026-05-30 | **Phase 5 三方向并行地基（PR-FD-A，A 已落）**：`Supervisor.__init__` 新增可选 kwarg `event_observer: Callable[[GameEvent], None] \| None = None`；`append_events` 落盘后逐条回调 observer，**回调前 `model_copy(deep=True)` 深拷贝**（InMemoryEventStore 保引用，不深拷贝就会让恶意 observer 污染 sink 落盘事件 → 污染 trigger_belief_update / ContextAssembler / Replay），异常被吞掉。**默认 None=零开销零回归**（80 passed）。**红线**：observer 只读 GameEvent，不能修改事件、不能影响游戏走向；旁观者异常**永远**不影响游戏。**用方**：C 的实时上帝视角 SSE 端点把 observer 实现为"塞进异步队列 → 推给 SSE 连接"；MVP 阶段可先不实现端点，hook 已经留好。**对接不变量**：observer 收到的事件**顺序与 sink.append_many 一致**，含 setup 事件（role_assigned / wolf_teammates 等）。配套测试 `tests/supervisor/test_yuan_event_observer_hook.py`（默认 noop / 顺序一致 / 异常吞 / mutation 隔离）4 条全过 | `supervisor.Supervisor(event_observer=)`、`supervisor.Supervisor.append_events` | 已合并 |
| 2026-05-30 | **Phase 5 三方向并行地基（PR-FD-A2，A 已落 —— 分工修正后由 A 顺手做，原本规划 owner=B）**：`agent_runtime/prompt_template_loader.py` 的 `load(<role>:v1_belief_llm)` 在 metadata 加 `system_prompt_empty_belief_fallback`（= shared/output_contract + v1 剥离过 v0 belief 禁令的 role 文本，**不附加 `shared/v1_belief_guidance.md`**；命名特意是 "空-belief fallback" 而不是 "v0 fallback"，因为这不是完整 v0 路径 —— 它跑的是剥离过 v0 belief 禁令的 role 文本，是 v0-like 不严格等同 v0）。`render()` 检测 `context.belief_state == {} 且 belief_top_suspects == []` 时切到 fallback，渲染出**不含 "belief" 关键词**的 system —— 服务 A 混合实验"按 player 注入 belief"的**公平性前置**（混合时非狼/非民拿到空 belief 时做无 belief 干扰的自由推理；不会因为 v1 prompt 残留 belief 描述而行为不一致）。**默认行为（全 v1 全注 belief / 纯 v0）零变化**（494 passed 零回归）。**红线**：fallback 只切换 prompt，不动 context 序列化（belief block 本来就是 "if belief: append"，与 fallback 一致）；不动 contracts schemas。**对接不变量**：v0 模板 `metadata["system_prompt_empty_belief_fallback"]` 不存在 → render 走原路径不退化（防止 v0 路径意外被影响）。配套测试 `tests/agent_runtime/test_prompt_template_loader.py::TestV1BeliefFallback`（fallback 缓存×5角色参数化 / v0 不缓存×5角色参数化 / 空退化 / 非空保留 / 单字段非空不退化 / v0 不受影响）14 条全过 | `agent_runtime.PromptTemplateLoader.load`、`agent_runtime.PromptTemplateLoader.render`、`PromptTemplate.metadata["system_prompt_empty_belief_fallback"]` | 已合并 |
| 2026-05-30 | **Phase 5 三方向并行地基（§10 `build_game` 共享装配函数，A 已落）**：新增 `runner/` 包；`runner.build_game(config, agent, *, arm="v0", use_belief=False, seed=None, event_observer=None, belief_inject_filter=None, deliver_witch_kill_info=True, trace_store=None, event_store=None, belief_store=None, belief_observability_store=None) -> BuiltGame` 一次性装配 Engine + Stores + ContextAssembler + BeliefUpdater + Supervisor。**A 的批跑**（mixed experiment 时会替换 `scripts/run_v0_batch.py` 里的本地装配）和 **B 的实时观战 `POST /games`** 都调它，避免两份装配代码漂移。**关键设计**：① ``agent`` 由 caller 构造（``LLMAgent`` / ``MockAgent`` / mixed-exp 的 per-player 路由 adapter 都行），``agent_version`` / ``template_name`` 不由本函数管；② ``belief_inject_filter`` 为 None 时**不向 ContextAssembler 传 kwarg** → PR-FD-B 未合并时 build_game 仍能用；非 None 时传 kwarg，需要 PR-FD-B 合并后才工作（在那之前会 TypeError，预期清晰失败）；③ ``event_observer`` 直接转给 ``Supervisor`` 的 PR-FD-A hook；④ ``arm="v0" + use_belief=True`` = shadow 模式，belief lane 启用但 ContextAssembler 拿 None。**入口校验**（复审收紧）：`arm not in {"v0","v1"}` → ValueError；belief lane 未启用（arm='v0' + use_belief=False）却注 belief_store / belief_observability_store → ValueError（**禁止隐式启用**，否则 updater 会偷偷写 real lane 污染 v0 baseline）。**红线**：不动 contracts schemas / enums；engine 职责纯净；信息隔离不变（ContextAssembler 同样的契约）。配套测试 `tests/runner/test_builder.py`（v0 基础 / v1 belief lane / shadow 模式 / event_observer 转发 / filter 转发 / filter=None 不传 / seed 决定性 / GameStores 默认值 / 拒绝未知 arm / 拒绝 v0 注 belief_store / 拒绝 v0 注 belief_obs / v1 允许注复用 store）**12 条全过；全量 506 passed**（runner + agent_runtime + agent_policy + supervisor + contracts）| `runner.build_game`、`runner.BuiltGame`、`runner.GameStores` | 已合并 |
| 2026-05-30 | **Phase 6 A 线 Stage 1（PR-A-1，A 已落）**：`runner.build_game` 新增可选 `belief_inject_filter_factory(engine, game_id) -> Callable[[str], bool]`，在 `engine.sessions.create_game(config)` 后调用，用 factory 替代外部传 `GameEngine` kwarg，让混合 belief 实验能基于刚发好牌的 `truth_state` 构造 snapshot 注入过滤器；与既有 `belief_inject_filter` 互斥（同时传则 ValueError），直接传 filter 的路径保持不变。新增 `runner.arm_filter`：`make_arm_filter(scope, engine, game_id)` / `make_arm_filter_factory(scope)`，scope 固定为 `wolves` / `villagers` / `gods` / `civilians` / `all` / `none`，其中 `none` 仅表示“不向任何 agent 注入 belief”，**不等价于关闭 belief lane**（若上层仍走 `arm="v1"`，updater 仍会写 real belief；纯 v0 必须由 CLI/runner 上层选择 `arm="v0"` 且不传 factory）；构造时只读 `truth_state.players[*].role/camp` 并冻结为 `frozenset`，filter 后续只查 membership、不再触碰 truth/events/belief。**红线**：不动 contracts schemas / enums / snapshot；不改 Supervisor / ContextAssembler / Engine / Stores / Agent 模块。| `runner.build_game`、`runner.arm_filter` | 已合并 |
| 2026-05-30 | **Phase 6 A 线 Stage 2（PR-A-2，A 已落）**：新增 `scripts/run_mixed_batch.py` 作为混合 belief 实验批跑入口，CLI 参数为 `--arm-wolves` / `--arm-villagers` / `--arm-gods` / `--arm-civilians` / `--games` / `--seed-start` / `--out` / `--extras-out` / `--trace-dir` / `--temperature` / `--retry-backoff`；`--arm-gods`、`--arm-civilians` 未传时跟随 `--arm-villagers`，显式传入则覆盖好人阵营默认。CLI 层展开最小 scope 集合 `wolves` / `gods` / `civilians`，`inject_scopes == ALL_SCOPES` 使用**严格相等**判断才退化为纯 `arm="v1"`（全员注入），空集合走 `arm="v0"` + no factory，部分集合走 `arm="v1"` + combined factory。`RunConfigSnapshot.agent_version` 规范化为 `v0` / `v1` / `v1+belief:<sorted scopes>`，明确**不用 `villagers` 组合别名**（例：好人侧 v1 写 `v1+belief:civilians+gods`）。LLMAgent 固定 `template_name="v1_belief_llm"`，依赖 PR-FD-A2 空 belief fallback 保持 v0-like no-belief 语义；纯 `agent_version="v0"` 的报告用 `strategy_profile_id="v1_belief_llm:no-belief-fallback"` 明确口径。`BatchRunReport` 不塞 extra 字段，混合审计数据（每局 `game_id`/`seed`/`inject_scopes`/`injected_agent_count`/`trace_count`/`belief_update_errors` 等）落 `--extras-out` sidecar，默认 `<stem>.extras<suffix>`。**红线**：不动 contracts schemas / enums / snapshot；不改 `scripts/run_v0_batch.py`；不改 runner / Supervisor / Context / Agent / Store / Engine 模块。| `scripts/run_mixed_batch.py`、`RunConfigSnapshot.agent_version` | 已合并 |
| 2026-05-31 | **Phase 6 A 线 Stage 2 followup（PR-A-2 followup 指标补强，A 已落）**：新增 `scripts/_mixed_metrics.py`（纯函数派生指标）+ `run_mixed_batch.py` 每局 sidecar `extra` 扩字段，把 Stage 5 跑批前缺失的 metrics 补齐（详见本条指标说明）。新增 sidecar 字段（**全在 extras sidecar，不进任何 schema**）：`status`/`winner`/`rounds`/`runtime_ms`；`decision_stats`（读 `LLMAgent.stats`：ok/parse_error/llm_error/retry/canonicalize_* + ok_rate）；`context_stats`（读 `ContextWindowPolicy.stats`：truncate/degrade/exceed/degrade_rate）；`pipeline`（数 `FALLBACK_USED`/`RULE_VALIDATION`/degraded/failed）；`key_scenes`（tie/hunter_shot/double_death/seer_killed_n1/witch 用药/预言家报查，派生于 `GameEvent`+`TruthState`）；`belief_audit`（`BeliefObservabilityStore` saves/curve_points/observers）；`belief_signal`（注入玩家专属：决策一致率 `decision_top_suspect_consistency_rate`、命中率 `top_suspect_accuracy_rate`/`top2_accuracy_rate`、`by_action_type` 分桶、`deviation_count`，及数学质量 `belief_quality`/`final_belief_quality` 的 Brier/熵/margin/狼民判别力）。**时序正确性（复审 P0/P1 修正）**：① belief 快照取**严格早于**决策 `(round, phase)` 的最近一条（Supervisor 先决策后落事件再 update，同 phase 快照属未来信息，必须排除；无更早快照则计入 `no_belief_decisions`，绝不退回未来快照）；② top suspect / Brier / 熵 / margin 的候选存活集**按事件流 DEATH_CONFIRMED 在决策时点重建**，不用赛末 `TruthState.status`（避免决策后才死的玩家被错误剔除）；baseline-v0 为 `null`。**红线**：一行不动 contracts schemas / enums / snapshot；只读冻结枚举值 + 现有 store/agent 接口；belief 派生在游戏**结束后**做（PostGameAnalyzer 性质），不碰 `RealtimeBeliefUpdater`；全在 A 的 `scripts/` 域。配套测试 `tests/scripts/test_mixed_metrics.py`（14 条纯函数，含时序泄漏 / 存活集重建回归）+ `tests/scripts/test_run_mixed_batch.py`（+3 条集成）；**全量 893 passed**。| `scripts/_mixed_metrics.py`、`scripts/run_mixed_batch.py` sidecar extras（无 schema 影响）| 已合并 |

### 8.B　B —— Agent Policy / Belief（`agent_policy/`）

**主要关注的契约**：`AgentContext`(消费)、`AgentAction`(产出)、`BeliefState`/`RoleBelief`、`DeviationEvent`、`StrategyMemoryItem`、`PromptVersion`、`AgentDecisionTrace`、`BadCaseReport`、`AgentTuningTrace`。

| 日期 | 事项（需求/改动/待确认） | 关联 schema / 接口 | 状态 |
|---|---|---|---|
| | | | |

### 8.C　C —— Runtime / Context / Store / API（`agent_runtime/ context/ stores/ api/ frontend/`）

**主要关注的契约**：`AgentContext`(构造)、`ContextBudgetConfig`、`FactStreamSummary`/`PlayerFactSummary`、`GameEvent`(落盘)、`BeliefState`(存储)、`EvaluationReport`/`LeaderboardRow`/`ReplayData`、API 响应 schema、`PromptVersion`(注册)。

| 日期 | 事项（需求/改动/待确认） | 关联 schema / 接口 | 状态 |
|---|---|---|---|
| 2026-05-22 | C 基座：agent_runtime 三件套（ActionParser / ActionCanonicalizer / FallbackPolicy）+ context/ 全量（ContextAssembler / VisibilityRuleSpec / ContextWindowPolicy / SpeechSummarizer）+ stores（EventStore / BeliefStateStore）+ agent_runtime（LLMProvider / FakeLLMProvider）。`SessionProvider` 通过 `from game_core import SessionProvider` 注入；`allowed_actions` 复用 `RuleValidator.allowed_actions(phase)`。 | `AgentContext`、`GameEvent`、`BeliefState`、`SessionProvider` 接口 | 已合并 |
| 2026-05-23 | Phase 2.5 (S1) + S2 起步：完善 SeerStrategy（按 docs/phase2_5_role_development.md §8.2）；实装 `AgentDecisionTrace` 持久化层（`TraceStore` ABC + InMemory + JSONL，给 Supervisor 接入用）；ReplayData 装配 + `/replay/{game_id}` `/health` FastAPI 端点；Dockerfile + 环境变量模板。requirements.txt 加 `fastapi[standard]>=0.115`（群里已对齐）。567 测试全过。 | `AgentDecisionTrace`、`ReplayData`、`AgentAction.claim_result`/`role_claim` | 在 `Phase2.5/Yao` 待 MR |
| 2026-05-23 | WOLF_NOMINATION setup 事件过滤约定（呼应 §8.A 待确认）：A 用 WOLF_NOMINATION 承载开局狼队友名单（payload.teammates 非空、actor/target=None）。C 现阶段 `ReplayData.timeline` 按事件顺序 flat list，不做 event_type 聚合 → 不受污染。未来 Evaluation/统计 wolf_nomination 提名次数时，过滤规则：`event_type=WOLF_NOMINATION AND actor is None AND payload.teammates not None` 跳过 setup。`context/visibility_rules.py:79/89/237/276` + `tests/context/test_visibility_rules.py:118-127` 已覆盖"狼读 setup teammates"的转换链路。 | `EventType.WOLF_NOMINATION` payload 约定 | 待确认 |
| 2026-05-23 | ReplayData v0 字段策略（A 已同意 2026-05-23 18:34-18:37 群聊）：现阶段只填 `game_id`/`players`/`timeline`/`events`；`belief_curves`/`deviation_points` 留 schema 默认空（v1/S8 才填）；`bad_cases` 留空（S10 PostGameAnalyzer 产）；`evaluation_summary` 留空（S2 baseline 跑完才有）。A 提示 S2→S3（9P Contract Freeze）时 Replay 会"整体大变"，本实现是 S2 临时版。 | `ReplayData` schema | 已确认 |
| 2026-05-25 | Phase 3 P0 #2：`VisibilityRuleSpec.allowed_actions` 按 `witch_state` 收窄。回应 A 23:30 群里 P0/P1/P2 拍板：`NIGHT_WITCH` 阶段读 `session.truth_state.witch_state`，`antidote_used` 移除 `SAVE`，`poison_used` 移除 `POISON`，两药皆尽退化为 `{SKIP}`。`RuleValidator.allowed_actions(phase)` docstring 明确"按 agent 收窄由调用方叠加"，C 是调用方。`truth_state` 仅本处计算用，不进 `AgentContext`、不泄漏给 agent。tests/context/test_visibility_rules.py 新增 4 个 case（antidote/poison/both/non-witch-unaffected）。pytest 全量 584 通过。 | `RuleValidator.allowed_actions`、`VisibilityRuleSpec.allowed_actions`、`WitchState` | 在 `Phase3/Yao` 待 MR |
| 2026-05-25 | Phase 3 占位：`stores/context_snapshot_store.py` 加 `ContextSnapshotStore` ABC（同 TraceStore 模式：append-only、唯一 context_id、双维索引 game/agent、按 `<game_id>/<agent_id>/<round>_<phase>.json` 落盘）。当前 `ContextSnapshot` schema 尚未在 contracts merge（待 A 起 contract MR），先留占位实现抛 `NotImplementedError`，schema merge 后补 InMemory + JSONL 真实实装。落库策略已与 A 对齐（C 拍板）：**LLM 局每决策点全落 ~500KB/局；Mock 压测默认不落、BadCase 触发时回填最近 3 个 snapshot**。 | `ContextSnapshot`(待 A merge)、`stores.context_snapshot_store` | 提议 |
| 2026-05-25 | Phase 3 P0 #1 待执行（依赖 A 起 contract MR）：A 已拍板 `PrivateEvent` 加 `round: int \| None = None`（additive）。C 这边等 schema merge 后改一行 `_to_private_event` 加 `round=ev.round`（[context/visibility_rules.py:229](../context/visibility_rules.py)），并补一个端到端测试验证 round passthrough。B 同步改 `_witch_kill_target` 取 max round。三件齐了 A 把 `emit_witch_kill_info` 接进 `run_game`。 | `PrivateEvent.round`(待 A merge) | 待依赖 |
| 2026-05-25 | Phase 3 P1/P2 sign-off：① `ReplayData` typed_ 加法（`typed_players`/`typed_belief_curves`/`timeline_items`），旧 `players`/`belief_curves` 保留一版以保护现有 [api/replay_service.py](../api/replay_service.py) + 18 项 endpoint 测试；UI 切换延后到 S9。② `RunConfigSnapshot` 13 字段够复现一局（contract_version/model/temperature/max_tokens/top_p/seed/retry/fallback/belief 策略全齐）。③ `ClaimRecord`/`VoteRecord` 投影由 C 在 ContextAssembler 产出，带 `source_event_id` + `derived_by="context_assembler"`，作为 typed 台账加进 `AgentContext.claim_records[]` / `vote_records[]`（与字符串 FactStream 并存）。④ `BeliefCurvePoint` 是 B 的领地。 | `ReplayData` typed_*、`RunConfigSnapshot`、`ClaimRecord`/`VoteRecord`、`AgentContext.claim_records[]`/`vote_records[]` | 待 A merge |
| 2026-05-26 | Phase 4 v0 前置：`PromptTemplateLoader` 实装（[agent_runtime/prompt_template_loader.py](../agent_runtime/prompt_template_loader.py)），按 A 5/26 00:50 @_all 三段确认项 选①：`prompts/<role>/v0_free_llm.md` 当唯一真相源（A=狼 / B=女巫+平民+猎人 / C=预言家 各自维护），`load("<role>:<template>")` 解析 → 读文件 → `PromptTemplate`；`render` 把策略文本作为 system 消息，`AgentContext` 字段序列化成中文 markdown 作为 user 消息，输出 OpenAI 风格 `list[dict]`。覆盖 5 个 prompt 文件点名引用字段：`visible_players` / `private_events.teammates`(WOLF_NOMINATION 狼队友) / `SEER_CHECK_RESULT` / `tie_candidates` / `allowed_actions` / `claim_records` / `vote_records`。40 测试 + 端到端 Loader+FakeLLM+ActionParser 链路 → 合法 `AgentAction`。全量 684 通过。**P0#1 `PrivateEvent.round` + P0#2 `allowed_actions` 按 `witch_state` 收窄已在 main 被消费（A 5/26 00:47 确认）**。 | `PromptTemplate`、`prompts/<role>/v0_free_llm.md` | 在 `Phase4/Yao` 待 MR |
| 2026-05-26 | Phase 4 v0 Supervisor 接线不变量（A 5/26 00:47 @C 提醒，C 侧无代码改动，仅登记给后人）：① v0 LLM `Supervisor` 构造时必须显式 `deliver_witch_kill_info=True`，否则 NIGHT_WITCH 看不到 `WITCH_KILL_TARGET_INFO`，女巫只能盲 skip。② 传给 `Supervisor` 的 `EventSink` 与 `ContextAssembler(event_store=...)` 必须是**同一个 `EventStore` 实例**（女巫刀口与狼队友事件都靠这条不变量传递；与 §8.A 2026-05-23 那条狼队友的接线不变量等价）。Owner = A/supervisor 侧负责接线，C 提供 `ContextAssembler` 接口兼容。 | `Supervisor.deliver_witch_kill_info`、`EventStore` 实例同一性 | 已确认（A 5/26 00:47） |
| 2026-05-26 | Phase 4 P2：`ClaimRecord` / `VoteRecord` typed 台账投影实装（[context/context_assembler.py](../context/context_assembler.py)），呼应 5/24 A P2 拍板的"typed 投影由 C 在 ContextAssembler 产出"。从可见 `SPEECH`（带 `role_claim` / `claim_result`）/ `VOTE_CAST` 公开事件流派生，每条带 `source_event_id` + `derived_by="context_assembler"`；`is_counter_claim` 启发式：同一 game 内已有更早不同 actor 跳同 role → 当前算对跳。`stage`/`is_revote`/`is_tie_candidate_vote` 按 phase 区分 primary vs revote。**默认 off（`ContextAssembler(enable_typed_records=False)`）**：9 人 D3 HUNTER_SHOOT 等满载阶段加上 typed records 会越过 `max_input_tokens_per_agent=4000`（实测 4041/4000）→ 触发 `ContextBudgetExceededError`。`ContextWindowPolicy` 加 `_MAX_TYPED_RECORDS=15` 硬上限（与 `max_recent_public_events` 同档）防止失控。**下一步**（需 contract MR）：给 `ContextBudgetConfig` 加 `max_claim_records` / `max_vote_records` 字段 + 上调 `max_input_tokens_per_agent` 到 5000 → 默认开启。9 测试覆盖 ClaimRecord/VoteRecord 字段、counter_claim 启发式、stage 区分、budget cap。 | `ClaimRecord`、`VoteRecord`、`AgentContext.claim_records[]`/`vote_records[]`、`ContextBudgetConfig`（待 A merge） | 在 `Phase4/Yao`（opt-in 默认 off） |
