"""策略复盘 + 人审闭环（B 的 evaluation 组件）。

设计见 ``docs/strategy_review_loop.md``。本包只做**赛后离线分析**：
读已落盘的 event / trace / replay_truth / belief_states，聚合成按角色的复盘材料，
交 LLM 产出 *策略层 prompt* 的候选改进建议（draft），并并排算 belief 命中率指标。

红线：只读历史数据、不碰 runtime、不改 ``contracts/``、不泄漏真相态、AI 不自动改 prompt。
"""
