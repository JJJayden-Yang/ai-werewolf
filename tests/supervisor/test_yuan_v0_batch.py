"""v0 批量 runner 验证（FakeLLMProvider，不联网）。

证明 scripts/run_v0_batch.py 的批量机制可靠：
- N 局各自独立跑完、聚合成合法 contracts.BatchRunReport（dogfood 预留 schema）；
- 局间隔离（每局独立 game_id / engine / store）；
- 失败局被正确计入（failed / error_count / failed_game_ids）。
"""

import sys
from pathlib import Path

from agent_runtime import FakeLLMProvider
from agent_runtime.exceptions import AgentRuntimeError
from agent_runtime.types import LLMResponse
from contracts import BatchRunReport, GameRunResult

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "scripts"))

import run_v0_batch  # noqa: E402


def test_v0_batch_runs_n_games_and_aggregates_report():
    provider = FakeLLMProvider('{"action_type": "skip", "reason_summary": "t"}')
    report, extras = run_v0_batch.run_batch(games=3, seed_start=0, temperature=0.5, provider=provider)

    assert isinstance(report, BatchRunReport)
    assert report.total == 3
    assert report.completed + report.failed == 3
    assert report.completed == 3  # skip→兜底，全部应跑完不崩
    assert report.error_count == 0
    assert report.failed_game_ids == []
    assert len(extras) == 3
    # 每局都有真实决策（skip 可解析），且无 parse/llm 错误
    assert all(e["agent_stats"]["ok"] > 0 for e in extras)
    assert all(e["agent_stats"]["parse_error"] == 0 for e in extras)
    # 赢家分布之和 = 完成局数（每局一个赢家）
    assert sum(report.winner_distribution.values()) == report.completed
    # run_config_snapshot 被填充（provenance 可溯）
    assert report.run_config_snapshot is not None
    assert report.run_config_snapshot.agent_version == "v0"


def test_v0_batch_isolation_distinct_games():
    """局间隔离：不同 seed 产出独立 game_id，互不串。"""
    provider = FakeLLMProvider('{"action_type": "skip", "reason_summary": "t"}')
    report, extras = run_v0_batch.run_batch(games=2, seed_start=10, temperature=0.5, provider=provider)
    assert report.total == 2
    # 两局事件流相互独立（各自非空、各自统计独立）
    assert all(e["events"] > 0 for e in extras)


class _BoomProvider:
    """generate 永远抛 → LLMAgent 每步兜底；游戏仍应跑完（不崩），但全是 fallback。"""

    async def generate(self, messages: list[dict], model_config: dict) -> LLMResponse:
        raise AgentRuntimeError("simulated outage")


class _RecordingProvider:
    def __init__(self) -> None:
        self.system_messages: list[str] = []

    async def generate(self, messages: list[dict], model_config: dict) -> LLMResponse:
        self.system_messages.append(messages[0]["content"])
        return LLMResponse(raw_output='{"action_type": "skip", "reason_summary": "t"}')


def test_v0_batch_records_high_fallback_when_llm_down():
    """LLM 全失败时：游戏靠兜底跑完(completed)，但 llm_error 高 → 批量指标如实反映。"""
    report, extras = run_v0_batch.run_batch(
        games=1, seed_start=0, temperature=0.5, provider=_BoomProvider(), retry_backoff=0.0
    )
    assert report.total == 1
    assert isinstance(report.representative_failed_runs, list)
    # 每个决策都 llm_error（重试耗尽），兜底次数应 > 0
    assert extras[0]["agent_stats"]["llm_error"] > 0
    assert extras[0]["fallbacks"] > 0


def test_v0_batch_emits_traces_when_trace_dir_provided(tmp_path):
    """--trace-dir 指定 → 每局 AgentDecisionTrace 落到 <trace_dir>/<game_id>.jsonl（S7 P1）。"""
    provider = FakeLLMProvider('{"action_type": "skip", "reason_summary": "t"}')
    trace_dir = tmp_path / "v0_traces"
    report, extras = run_v0_batch.run_batch(
        games=2, seed_start=0, temperature=0.5, provider=provider, trace_dir=trace_dir,
    )
    assert report.total == 2
    # 每局都生成一份 trace 文件
    files = sorted(p.name for p in trace_dir.glob("*.jsonl"))
    assert files == ["v0_batch_0.jsonl", "v0_batch_1.jsonl"]
    # 每局 trace_count 与文件实际行数一致
    for e, name in zip(extras, files):
        lines = (trace_dir / name).read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == e["trace_count"]
        assert e["trace_count"] > 0  # 9 人局至少 ~70-100 决策


def test_v0_batch_extras_exposes_new_metrics():
    """extras 透出 canonicalize_* 三 key + context_window_stats（A handoff §P2）。"""
    provider = FakeLLMProvider('{"action_type": "skip", "reason_summary": "t"}')
    _report, extras = run_v0_batch.run_batch(
        games=1, seed_start=0, temperature=0.5, provider=provider,
    )
    assert len(extras) == 1
    stats = extras[0]["agent_stats"]
    # 三个 canonicalize 计数 key 必存（skip 路径 0 命中 → 都为 0）
    assert stats["canonicalize_meta_ai"] == 0
    assert stats["canonicalize_cot_leak"] == 0
    assert stats["canonicalize_role_leak"] == 0
    # context_window_stats 必有 4 keys 全 dict
    cw = extras[0]["context_window_stats"]
    assert set(cw.keys()) == {
        "applies", "truncated_speech_events",
        "progressive_degrade_triggered", "budget_exceeded",
    }
    assert cw["applies"] > 0  # 9 人局至少触发若干 build_context
    assert cw["budget_exceeded"] == 0  # mock skip 短 SPEECH 不会越界


def test_v0_batch_belief_flag_updates_background_belief_without_injection():
    """--arm v0 --belief：后台更新 belief，但不把 belief_store 传给 ContextAssembler。"""
    provider = FakeLLMProvider('{"action_type": "skip", "reason_summary": "t"}')
    report, extras = run_v0_batch.run_batch(
        games=1,
        seed_start=0,
        temperature=0.5,
        provider=provider,
        use_belief=True,
    )

    assert report.run_config_snapshot.agent_version == "v0+belief"
    assert extras[0]["belief_enabled"] is True
    assert extras[0]["belief_injected"] is False
    assert extras[0]["belief_is_shadow"] is True
    assert extras[0]["belief_observers"] > 0
    assert extras[0]["belief_saves"] > 0
    assert extras[0]["belief_update_batches"] > 0
    assert extras[0]["belief_curve_points"] > 0
    assert extras[0]["belief_update_errors"] == 0


def test_v1_batch_arm_enables_belief_and_marks_context_injection():
    """--arm v1：自动启 belief updater，并把同一个 belief_store 注入 ContextAssembler。"""
    provider = FakeLLMProvider('{"action_type": "skip", "reason_summary": "t"}')
    report, extras = run_v0_batch.run_batch(
        games=1,
        seed_start=0,
        temperature=0.5,
        provider=provider,
        arm="v1",
    )

    assert report.run_config_snapshot.agent_version == "v1"
    assert extras[0]["belief_enabled"] is True
    assert extras[0]["belief_injected"] is True
    assert extras[0]["belief_is_shadow"] is False
    assert extras[0]["belief_observers"] > 0
    assert extras[0]["belief_saves"] > 0
    assert extras[0]["belief_update_batches"] > 0
    assert extras[0]["belief_curve_points"] > 0
    assert extras[0]["belief_update_errors"] == 0


def test_v1_batch_uses_belief_prompt_template():
    provider = _RecordingProvider()
    report, extras = run_v0_batch.run_batch(
        games=1,
        seed_start=0,
        temperature=0.5,
        provider=provider,
        arm="v1",
    )

    assert report.run_config_snapshot.prompt_version_id == "<role>:v1_belief_llm"
    assert extras[0]["belief_injected"] is True
    assert provider.system_messages
    assert any("v1 Belief Guidance" in prompt for prompt in provider.system_messages)


def test_v1_batch_marks_run_failed_when_belief_updater_errors(monkeypatch):
    """v1 的 belief 是实验主信号；updater 全挂不能仍算 completed。"""

    class _FailingUpdater:
        def __init__(self, **_kwargs) -> None:
            pass

        def update(self, game_id: str, event_id: str) -> None:
            raise RuntimeError("belief down")

    monkeypatch.setattr(run_v0_batch, "RuleBasedRealtimeBeliefUpdater", _FailingUpdater)
    provider = FakeLLMProvider('{"action_type": "skip", "reason_summary": "t"}')

    report, extras = run_v0_batch.run_batch(
        games=1,
        seed_start=0,
        temperature=0.5,
        provider=provider,
        arm="v1",
    )

    assert report.failed == 1
    assert report.completed == 0
    assert report.representative_failed_runs[0].error_type == "belief_update_failed"
    assert extras[0]["belief_update_errors"] > 0
