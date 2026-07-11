"""A(Game Core)自己拥有的内部类型。

这些类型不在 contracts/ 冻结清单内，由 A 定义和维护：
- GameSession：单局运行时状态容器，持有 TruthState。
- ValidationResult：RuleValidator 的返回。
- WinCheckResult：WinChecker 的返回。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts.enums import Phase
    from contracts.schemas import GameConfig, GameEvent, TruthState


@dataclass
class GameSession:
    """单局游戏的运行时状态容器。

    每个 game_id 一个独立实例，禁止全局单例。Engine 通过它读写 TruthState；
    Agent 永远拿不到这个对象（信息隔离红线）。

    phase / round 的**唯一真相**是 `truth_state`；`current_phase` / `round` 是只读转发。
    要改 phase/round 必须写 `truth_state.phase` / `truth_state.round`，杜绝双来源不一致。
    """

    game_id: str
    config: GameConfig
    truth_state: TruthState
    # A 自有运行时字段，不属于 contracts/TruthState。
    # 猎人从不同阶段触发时，HUNTER_SHOOT 结束后的返回路径不同。
    hunter_shoot_return_phase: Phase | None = None
    # 发牌所用 RNG seed（None=未用定种）。仅供赛后 replay/export 直接读取以复现发牌；
    # 绝不写进 role_assigned 等会进 AgentContext 的通道（否则可反推 pid→role，破坏信息隔离）。
    seed: int | None = None

    @property
    def current_phase(self) -> Phase:
        return self.truth_state.phase

    @property
    def round(self) -> int:
        return self.truth_state.round


@dataclass
class ValidationResult:
    """RuleValidator.validate 的返回。"""

    is_valid: bool
    violation_type: str | None = None
    message: str | None = None


@dataclass
class WinCheckResult:
    """WinChecker.check 的返回。

    winner 取值：'villagers' / 'werewolves' / None（未结束）。
    """

    game_over: bool
    winner: str | None = None
    reason: str | None = None
