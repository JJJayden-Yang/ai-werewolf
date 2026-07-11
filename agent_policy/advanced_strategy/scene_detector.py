"""SceneDetector —— 从运行时 AgentContext 推出当前激活的 scene_tag 集合。

红线（同 RealtimeBeliefUpdater）：**禁读 TruthState / 隐藏身份**，只读 AgentContext
已暴露的字段。每个 tag 的触发都是确定性的（靠 phase / allowed_actions / tie_candidates /
存活人数），杜绝漏检——因为高级策略是"抽取式"（从角色 prompt 移走），漏检 = 比 baseline 更差。

首批四个 tag：

- ``tie_revote``     : ``tie_candidates`` 非空（平票二次发言/投票场景）。
- ``hunter_shoot``   : ``hunter_shoot ∈ allowed_actions``（只在 HUNTER_SHOOT 阶段为真）。
- ``witch_poison``   : ``poison ∈ allowed_actions``（只在 NIGHT_WITCH 且毒药可用时为真）。
- ``endgame_close``  : 存活玩家数 ≤ ``ENDGAME_ALIVE_THRESHOLD``（近似残局；单 agent 不知狼数，
                       但纯增量片段 → 漏检只是没加成、不退步）。
- ``day1_peaceful_night`` : 首日白天讨论（``round==1`` 且 ``phase==DAY_DISCUSSION``）且夜里无人
                       出局（``visible_players`` 无 DEAD）。首夜女巫常救人 → 平安夜 → 场上零真实
                       信息，agent 容易在"谁点名谁太早""发言顺序"这种元信息上集体回音、制造假深度。
                       该片段把发言能量引回有产出的方向（标记划水位、铺垫归票计划），而不是常驻进
                       每个 prompt 占预算。只读 AgentContext，纯增量、漏检只是没加成。
- ``seer_clash``     : 公开发言里 ≥2 个不同 actor 自报 ``role_claim==SEER``（多预言家对跳/悍跳已
                       发生）。狼/预言家 base prompt 各有自己视角，但好人方非神职"我该信哪个、怎么
                       站边归票"在 base 完全没写——这条 generic 片段补这个读盘缺口。只读 ``public_events``
                       （typed ``claim_records`` 默认关、不可依赖），靠 SPEECH 事件的 role_claim 计数。
- ``witch_save``     : ``save ∈ allowed_actions``（今晚有刀口可救、解药仍在）。与 ``witch_poison`` 对称；
                       base 只有一行救药指引，本片段补救药价值排序/留药/自救判断。
- ``under_accusation`` : 公开发言里有人把自己（``agent_id``）``claim_result`` 成 werewolf（被公开查杀/指认）。
                       阵营中立（狼好人都会被指）；好人被冤时的自辩在 base 没专门写。靠 ``public_events``
                       的 ``claim_result.target == agent_id`` 检测，不依赖默认关闭的 claim_records。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from contracts import ActionType, ClaimedAlignment, Phase, PlayerStatus, Role

if TYPE_CHECKING:
    from contracts import AgentContext

# 残局阈值：9 人局打到 ≤4 人存活时基本进入票数定生死的阶段。
ENDGAME_ALIVE_THRESHOLD = 4

TAG_TIE_REVOTE = "tie_revote"
TAG_HUNTER_SHOOT = "hunter_shoot"
TAG_WITCH_POISON = "witch_poison"
TAG_ENDGAME_CLOSE = "endgame_close"
TAG_DAY1_PEACEFUL_NIGHT = "day1_peaceful_night"
TAG_SEER_CLASH = "seer_clash"
TAG_WITCH_SAVE = "witch_save"
TAG_UNDER_ACCUSATION = "under_accusation"

# 触发 seer_clash 所需的最少 distinct 预言家声明数（对跳=至少两个）。
SEER_CLASH_MIN_CLAIMERS = 2


def detect(context: "AgentContext") -> set[str]:
    """返回当前 context 激活的 scene_tag 集合（纯函数、无副作用）。"""
    tags: set[str] = set()

    if context.tie_candidates:
        tags.add(TAG_TIE_REVOTE)

    allowed = set(context.allowed_actions)
    if ActionType.HUNTER_SHOOT in allowed:
        tags.add(TAG_HUNTER_SHOOT)
    if ActionType.POISON in allowed:
        tags.add(TAG_WITCH_POISON)
    if ActionType.SAVE in allowed:
        tags.add(TAG_WITCH_SAVE)

    # 多预言家对跳：公开发言里 ≥2 个不同 actor 自报预言家身份。
    # 只读 public_events 的 role_claim（typed claim_records 默认关、不可依赖）。
    seer_claimers = {
        ev.actor
        for ev in context.public_events
        if ev.role_claim == Role.SEER and ev.actor is not None
    }
    if len(seer_claimers) >= SEER_CLASH_MIN_CLAIMERS:
        tags.add(TAG_SEER_CLASH)

    # 被公开指认为狼：公开发言里有人 claim 自己（agent_id）= werewolf。
    # 同样只读 public_events 的 claim_result（不依赖默认关闭的 claim_records）。
    accused = any(
        ev.claim_result is not None
        and ev.claim_result.target == context.agent_id
        and ev.claim_result.claimed_alignment == ClaimedAlignment.WEREWOLF
        for ev in context.public_events
    )
    if accused:
        tags.add(TAG_UNDER_ACCUSATION)

    alive = sum(1 for p in context.visible_players if p.status == PlayerStatus.ALIVE)
    if 0 < alive <= ENDGAME_ALIVE_THRESHOLD:
        tags.add(TAG_ENDGAME_CLOSE)

    # 首日平安夜：round==1 的白天讨论 + 没有任何人出局（夜里被女巫救了/空刀被救）。
    # 用 visible_players 无 DEAD 判定，确定性、只读 AgentContext。
    if (
        context.round == 1
        and context.phase == Phase.DAY_DISCUSSION
        and not any(p.status == PlayerStatus.DEAD for p in context.visible_players)
    ):
        tags.add(TAG_DAY1_PEACEFUL_NIGHT)

    return tags
