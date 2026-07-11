from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_runtime import FakeLLMProvider
from contracts import BatchRunReport, Phase, RunConfigSnapshot
from scripts import run_mixed_batch as mixed


_SLOW_THINK_RESPONSE = (
    '{"assessments":[{"player_id":"P1","werewolf_suspicion":0.8}],'
    '"self_reasoning":"synthetic reflection"}'
)


def test_inject_scopes_baseline_v0():
    plan = mixed.resolve_mixed_arm(arm_wolves="v0", arm_villagers="v0")

    assert plan.inject_scopes == frozenset()
    assert plan.arm == "v0"
    assert plan.belief_inject_filter_factory is None


def test_inject_scopes_baseline_v1():
    plan = mixed.resolve_mixed_arm(arm_wolves="v1", arm_villagers="v1")

    assert plan.inject_scopes == frozenset({"wolves", "gods", "civilians"})
    assert plan.arm == "v1"
    assert plan.belief_inject_filter_factory is None


def test_inject_scopes_wolves_only_v1():
    plan = mixed.resolve_mixed_arm(arm_wolves="v1", arm_villagers="v0")

    assert plan.inject_scopes == frozenset({"wolves"})
    assert plan.arm == "v1"
    assert plan.belief_inject_filter_factory is not None


def test_inject_scopes_villagers_only_v1():
    plan = mixed.resolve_mixed_arm(arm_wolves="v0", arm_villagers="v1")

    assert plan.inject_scopes == frozenset({"gods", "civilians"})
    assert plan.arm == "v1"
    assert plan.belief_inject_filter_factory is not None


def test_explicit_arm_gods_overrides_villagers():
    plan = mixed.resolve_mixed_arm(
        arm_wolves="v1",
        arm_villagers="v1",
        arm_gods="v0",
    )

    assert plan.inject_scopes == frozenset({"wolves", "civilians"})
    assert plan.arm == "v1"
    assert plan.belief_inject_filter_factory is not None


def test_resolve_mixed_arm_rejects_invalid_python_api_arm():
    with pytest.raises(ValueError, match="arm_wolves"):
        mixed.resolve_mixed_arm(arm_wolves="bad", arm_villagers="v0")

    with pytest.raises(ValueError, match="arm_gods"):
        mixed.resolve_mixed_arm(
            arm_wolves="v0",
            arm_villagers="v0",
            arm_gods="bad",
        )


@pytest.mark.parametrize(
    ("arm", "inject_scopes", "expected"),
    [
        ("v0", frozenset(), "v0"),
        ("v1", mixed.ALL_SCOPES, "v1"),
        ("v1", frozenset({"wolves"}), "v1+belief:wolves"),
        ("v1", frozenset({"gods", "civilians"}), "v1+belief:civilians+gods"),
        ("v1", frozenset({"wolves", "gods"}), "v1+belief:gods+wolves"),
        ("v1", frozenset({"civilians"}), "v1+belief:civilians"),
    ],
)
def test_agent_version_encoding(arm, inject_scopes, expected):
    assert mixed.encode_agent_version(arm, inject_scopes) == expected


def test_build_game_called_with_correct_arm_and_factory(monkeypatch):
    calls: list[dict] = []

    class _FakeSupervisor:
        async def run_game(self, _game_id: str) -> None:
            return None

    class _FakeEventStore:
        def list_by_game(self, _game_id: str):
            return []

    class _FakeEngine:
        def get_session(self, _game_id: str):
            return SimpleNamespace(
                current_phase=Phase.GAME_OVER,
                round=1,
                truth_state=SimpleNamespace(players={"P1": object()}),
            )

    def fake_build_game(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            engine=_FakeEngine(),
            supervisor=_FakeSupervisor(),
            stores=SimpleNamespace(event_store=_FakeEventStore()),
        )

    monkeypatch.setattr(mixed, "build_game", fake_build_game)
    monkeypatch.setattr(mixed, "_resolve_injected_agents", lambda *_args: ["P1"])
    # 这条只验证 build_game 被以正确 arm/factory 调用，fake built 没有真 truth_state，
    # 故跳过派生 metrics（compute_mixed_metrics 单独在 test__mixed_metrics 里测）。
    monkeypatch.setattr(mixed, "compute_mixed_metrics", lambda **_kwargs: {})

    report, extras = mixed.run_batch(
        arm_wolves="v1",
        arm_villagers="v0",
        games=1,
        seed_start=300,
        agent_factory=mixed._make_mock_agent_factory(),
    )

    assert report.total == 1
    assert extras[0]["injected_agent_count"] == 1
    assert calls[0]["arm"] == "v1"
    assert calls[0]["belief_inject_filter_factory"] is not None
    assert calls[0]["belief_kernel"] == "additive_v1"
    assert calls[0]["slow_think_policy"] is None
    assert extras[0]["belief_kernel"] == "additive_v1"
    assert extras[0]["slow_think"] == "off"
    assert extras[0]["reflect_max"] == 8
    assert extras[0]["slow_think_stats"] == {
        "reflections": 0,
        "reflect_errors": 0,
        "reflect_llm_errors": 0,
        "reflect_parse_errors": 0,
        "applied": 0,
    }


def test_run_batch_forwards_factorized_belief_kernel(monkeypatch):
    calls: list[dict] = []

    class _FakeSupervisor:
        async def run_game(self, _game_id: str) -> None:
            return None

    class _FakeEventStore:
        def list_by_game(self, _game_id: str):
            return []

    class _FakeEngine:
        def get_session(self, _game_id: str):
            return SimpleNamespace(
                current_phase=Phase.GAME_OVER,
                round=1,
                truth_state=SimpleNamespace(players={"P1": object()}),
            )

    def fake_build_game(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            engine=_FakeEngine(),
            supervisor=_FakeSupervisor(),
            stores=SimpleNamespace(event_store=_FakeEventStore()),
        )

    monkeypatch.setattr(mixed, "build_game", fake_build_game)
    monkeypatch.setattr(mixed, "_resolve_injected_agents", lambda *_args: ["P1"])
    monkeypatch.setattr(mixed, "compute_mixed_metrics", lambda **_kwargs: {})

    _report, extras = mixed.run_batch(
        arm_wolves="v1",
        arm_villagers="v0",
        games=1,
        seed_start=301,
        agent_factory=mixed._make_mock_agent_factory(),
        belief_kernel="factorized_v2",
    )

    assert calls[0]["belief_kernel"] == "factorized_v2"
    assert extras[0]["belief_kernel"] == "factorized_v2"


def test_run_batch_slow_think_forwarded(monkeypatch):
    calls: list[dict] = []
    sentinel_policy = object()

    class _FakeSupervisor:
        async def run_game(self, _game_id: str) -> None:
            return None

    class _FakeEventStore:
        def list_by_game(self, _game_id: str):
            return []

    class _FakeEngine:
        def get_session(self, _game_id: str):
            return SimpleNamespace(
                current_phase=Phase.GAME_OVER,
                round=1,
                truth_state=SimpleNamespace(players={"P1": object()}),
            )

    def fake_build_game(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            engine=_FakeEngine(),
            supervisor=_FakeSupervisor(),
            stores=SimpleNamespace(event_store=_FakeEventStore()),
        )

    monkeypatch.setattr(mixed, "build_game", fake_build_game)
    monkeypatch.setattr(mixed, "_resolve_injected_agents", lambda *_args: ["P1"])
    monkeypatch.setattr(mixed, "compute_mixed_metrics", lambda **_kwargs: {})

    _report, extras = mixed.run_batch(
        arm_wolves="v1",
        arm_villagers="v1",
        games=1,
        seed_start=302,
        agent_factory=mixed._make_mock_agent_factory(),
        slow_think="on",
        reflector_factory=lambda: sentinel_policy,
    )

    assert calls[0]["slow_think_policy"] is sentinel_policy
    assert extras[0]["slow_think"] == "on"
    assert extras[0]["reflect_max"] == 8


def test_slow_think_off_ignores_reflector_factory(monkeypatch):
    calls: list[dict] = []
    factory_calls = 0

    class _FakeSupervisor:
        async def run_game(self, _game_id: str) -> None:
            return None

    class _FakeEventStore:
        def list_by_game(self, _game_id: str):
            return []

    class _FakeEngine:
        def get_session(self, _game_id: str):
            return SimpleNamespace(
                current_phase=Phase.GAME_OVER,
                round=1,
                truth_state=SimpleNamespace(players={"P1": object()}),
            )

    def fake_build_game(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            engine=_FakeEngine(),
            supervisor=_FakeSupervisor(),
            stores=SimpleNamespace(event_store=_FakeEventStore()),
        )

    def reflector_factory():
        nonlocal factory_calls
        factory_calls += 1
        return object()

    monkeypatch.setattr(mixed, "build_game", fake_build_game)
    monkeypatch.setattr(mixed, "_resolve_injected_agents", lambda *_args: ["P1"])
    monkeypatch.setattr(mixed, "compute_mixed_metrics", lambda **_kwargs: {})

    _report, extras = mixed.run_batch(
        arm_wolves="v1",
        arm_villagers="v1",
        games=1,
        seed_start=302,
        agent_factory=mixed._make_mock_agent_factory(),
        reflector_factory=reflector_factory,
    )

    assert factory_calls == 0
    assert calls[0]["slow_think_policy"] is None
    assert extras[0]["slow_think"] == "off"
    assert extras[0]["reflect_max"] == 8
    assert extras[0]["slow_think_stats"] == {
        "reflections": 0,
        "reflect_errors": 0,
        "reflect_llm_errors": 0,
        "reflect_parse_errors": 0,
        "applied": 0,
    }


def test_slow_think_on_wires_reflector_and_records_stats():
    _report, extras = mixed.run_batch(
        arm_wolves="v1",
        arm_villagers="v1",
        games=1,
        seed_start=303,
        agent_factory=mixed._make_mock_agent_factory(),
        provider=FakeLLMProvider(_SLOW_THINK_RESPONSE),
        slow_think="on",
    )

    stats = extras[0]["slow_think_stats"]
    assert extras[0]["slow_think"] == "on"
    assert extras[0]["reflect_max"] == 8
    assert stats["reflections"] >= 1
    assert stats["reflections"] <= 8
    assert stats["applied"] >= 1
    assert stats["reflect_errors"] == 0
    assert stats["reflect_llm_errors"] == 0
    assert stats["reflect_parse_errors"] == 0


def test_slow_think_stats_include_error_breakdown():
    stats = mixed._slow_think_stats(
        SimpleNamespace(
            stats={
                "reflections": 3,
                "reflect_errors": 2,
                "reflect_llm_errors": 1,
                "reflect_parse_errors": 1,
                "applied": 1,
            }
        )
    )

    assert stats == {
        "reflections": 3,
        "reflect_errors": 2,
        "reflect_llm_errors": 1,
        "reflect_parse_errors": 1,
        "applied": 1,
    }


def test_reflect_max_recorded_in_sidecar():
    _report, extras = mixed.run_batch(
        arm_wolves="v1",
        arm_villagers="v1",
        games=1,
        seed_start=305,
        agent_factory=mixed._make_mock_agent_factory(),
        provider=FakeLLMProvider(_SLOW_THINK_RESPONSE),
        slow_think="on",
        reflect_max=2,
    )

    assert extras[0]["slow_think"] == "on"
    assert extras[0]["reflect_max"] == 2
    assert extras[0]["slow_think_stats"]["reflections"] <= 2


def test_slow_think_per_game_stats_not_shared():
    reflectors = []

    def reflector_factory():
        reflector = mixed.LLMSlowThinkReflector(FakeLLMProvider(_SLOW_THINK_RESPONSE))
        reflectors.append(reflector)
        return reflector

    _report, extras = mixed.run_batch(
        arm_wolves="v1",
        arm_villagers="v1",
        games=2,
        seed_start=304,
        agent_factory=mixed._make_mock_agent_factory(),
        slow_think="on",
        reflector_factory=reflector_factory,
    )

    assert len(reflectors) == 2
    assert extras[0]["slow_think_stats"] == reflectors[0].stats
    assert extras[1]["slow_think_stats"] == reflectors[1].stats
    assert extras[0]["slow_think_stats"]["reflections"] >= 1
    assert extras[1]["slow_think_stats"]["reflections"] >= 1


def test_slow_think_on_v0_warns_no_belief_lane():
    with pytest.warns(RuntimeWarning, match="no belief lane"):
        _report, extras = mixed.run_batch(
            arm_wolves="v0",
            arm_villagers="v0",
            games=1,
            seed_start=306,
            agent_factory=mixed._make_mock_agent_factory(),
            provider=FakeLLMProvider(_SLOW_THINK_RESPONSE),
            slow_think="on",
        )

    assert extras[0]["slow_think"] == "on"


def test_smoke_2_games_mock_completes():
    report, extras = mixed.run_batch(
        arm_wolves="v1",
        arm_villagers="v0",
        games=2,
        seed_start=400,
        agent_factory=mixed._make_mock_agent_factory(),
    )

    assert report.total == 2
    assert report.completed == 2
    assert report.failed == 0
    assert report.run_config_snapshot is not None
    assert report.run_config_snapshot.agent_version == "v1+belief:wolves"
    assert extras[0]["game_id"] == "mixed_batch_00400"
    assert extras[0]["seed"] == 400
    assert extras[0]["agent_version"] == "v1+belief:wolves"
    assert extras[0]["belief_kernel"] == "additive_v1"
    assert extras[0]["inject_scopes"] == ["wolves"]
    assert extras[0]["injected_agent_count"] == 3
    assert extras[0]["player_count"] == 9
    assert "trace_count" in extras[0]
    assert extras[0]["belief_update_errors"] == 0


def test_v0_report_marks_no_belief_fallback_profile():
    report, extras = mixed.run_batch(
        arm_wolves="v0",
        arm_villagers="v0",
        games=1,
        seed_start=500,
        agent_factory=mixed._make_mock_agent_factory(),
    )

    assert report.run_config_snapshot is not None
    assert report.run_config_snapshot.agent_version == "v0"
    assert (
        report.run_config_snapshot.strategy_profile_id
        == "v1_belief_llm:no-belief-fallback"
    )
    assert extras[0]["prompt_template_name"] == "v1_belief_llm"
    assert extras[0]["prompt_profile"] == "v1_belief_llm:no-belief-fallback"


def test_extras_includes_all_metric_blocks():
    _report, extras = mixed.run_batch(
        arm_wolves="v1",
        arm_villagers="v0",
        games=1,
        seed_start=700,
        agent_factory=mixed._make_mock_agent_factory(),
    )
    e = extras[0]
    for key in ("decision_stats", "context_stats", "pipeline", "key_scenes", "belief_audit"):
        assert key in e, f"missing metric block {key}"
    assert "winner" in e and "rounds" in e and "runtime_ms" in e
    # belief lane 后台对所有 9 人维护（updater 不受 inject filter 影响）
    assert e["belief_audit"]["observers"] == 9
    assert e["belief_audit"]["saves"] > 0


def test_belief_signal_injected_agents_match_wolves():
    _report, extras = mixed.run_batch(
        arm_wolves="v1",
        arm_villagers="v0",
        games=1,
        seed_start=710,
        agent_factory=mixed._make_mock_agent_factory(),
    )
    sig = extras[0]["belief_signal"]
    assert sig is not None
    # wolves-only-v1：注入的恰好是 3 个狼
    assert len(sig["injected_agents"]) == 3
    assert extras[0]["injected_agent_count"] == 3


def test_concurrency_preserves_order_and_completes():
    # 并发跑 4 局，结果按 seed 顺序稳定落位、数量正确
    report, extras = mixed.run_batch(
        arm_wolves="v1",
        arm_villagers="v0",
        games=4,
        seed_start=730,
        concurrency=3,
        agent_factory=mixed._make_mock_agent_factory(),
    )
    assert report.total == 4
    assert report.completed == 4
    assert [e["seed"] for e in extras] == [730, 731, 732, 733]
    assert [e["game_id"] for e in extras] == [
        "mixed_batch_00730",
        "mixed_batch_00731",
        "mixed_batch_00732",
        "mixed_batch_00733",
    ]


def test_concurrency_rejects_zero():
    with pytest.raises(ValueError, match="concurrency"):
        mixed.run_batch(
            arm_wolves="v0",
            arm_villagers="v0",
            games=1,
            concurrency=0,
            agent_factory=mixed._make_mock_agent_factory(),
        )


def test_baseline_v0_has_no_belief_signal_or_audit():
    _report, extras = mixed.run_batch(
        arm_wolves="v0",
        arm_villagers="v0",
        games=1,
        seed_start=720,
        agent_factory=mixed._make_mock_agent_factory(),
    )
    e = extras[0]
    assert e["belief_signal"] is None
    assert e["belief_audit"] == {"saves": 0, "curve_points": 0, "observers": 0}
    # 工程指标在 baseline 也照常产出
    assert "decision_stats" in e and "key_scenes" in e


def test_default_extras_path_follows_report_path(tmp_path):
    assert (
        mixed._default_extras_path(tmp_path / "mixed_batch_report.json")
        == tmp_path / "mixed_batch_report.extras.json"
    )
    assert mixed._default_extras_path(tmp_path / "report") == tmp_path / "report.extras.json"


def test_main_writes_report_and_extras_sidecar(monkeypatch, tmp_path):
    report_path = tmp_path / "nested" / "report.json"
    report = BatchRunReport(
        total=1,
        completed=1,
        failed=0,
        run_config_snapshot=RunConfigSnapshot(agent_version="v1+belief:wolves"),
    )
    extras = [
        {
            "game_id": "mixed_batch_00600",
            "seed": 600,
            "agent_version": "v1+belief:wolves",
            "inject_scopes": ["wolves"],
            "injected_agent_count": 2,
            "player_count": 9,
            "trace_count": 0,
            "belief_update_errors": 0,
        }
    ]

    monkeypatch.setattr(mixed, "run_batch", lambda **_kwargs: (report, extras))

    exit_code = mixed.main(
        [
            "--arm-wolves",
            "v1",
            "--arm-villagers",
            "v0",
            "--games",
            "1",
            "--out",
            str(report_path),
        ]
    )

    assert exit_code == 0
    assert report_path.is_file()
    extras_path = tmp_path / "nested" / "report.extras.json"
    assert extras_path.is_file()
    assert json.loads(extras_path.read_text(encoding="utf-8")) == extras


def test_batch_dir_resolves_under_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_WOLF_BATCH_DIR", str(tmp_path))
    assert mixed._batch_dir("run_a") == tmp_path / "run_a"


def test_batch_dir_default_root(monkeypatch, tmp_path):
    monkeypatch.delenv("AI_WOLF_BATCH_DIR", raising=False)
    monkeypatch.setenv("AI_WOLF_DATA_DIR", str(tmp_path))
    assert mixed._batch_dir("run_a") == tmp_path / "batches" / "run_a"


@pytest.mark.parametrize("evil", ["../secret", "a/b", "a\\b", "", ".", ".."])
def test_batch_dir_rejects_unsafe_id(monkeypatch, tmp_path, evil):
    # 写入侧用与 get_batch_report 同一套安全目录名规则，防写到根外。
    monkeypatch.setenv("AI_WOLF_BATCH_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="invalid --batch-id"):
        mixed._batch_dir(evil)


def _fake_build_game_capture(calls):
    """共享的 fake build_game，捕获 kwargs，返回最小可跑 BuiltGame 替身。"""
    class _FakeSupervisor:
        async def run_game(self, _game_id):
            return None

    class _FakeEventStore:
        def list_by_game(self, _game_id):
            return []

    class _FakeEngine:
        def get_session(self, _game_id):
            return SimpleNamespace(
                current_phase=Phase.GAME_OVER,
                round=1,
                truth_state=SimpleNamespace(players={"P1": object()}),
            )

    def fake_build_game(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            engine=_FakeEngine(),
            supervisor=_FakeSupervisor(),
            stores=SimpleNamespace(event_store=_FakeEventStore()),
        )

    return fake_build_game


def test_belief_dir_injects_jsonl_store_for_v1(monkeypatch, tmp_path):
    from stores.belief_state_store import JsonlBeliefStateStore

    calls = []
    monkeypatch.setattr(mixed, "build_game", _fake_build_game_capture(calls))
    monkeypatch.setattr(mixed, "_resolve_injected_agents", lambda *_a: ["P1"])
    monkeypatch.setattr(mixed, "compute_mixed_metrics", lambda **_k: {})

    belief_dir = tmp_path / "belief_states"
    mixed.run_batch(
        arm_wolves="v1",
        arm_villagers="v1",
        games=1,
        seed_start=400,
        agent_factory=mixed._make_mock_agent_factory(),
        belief_dir=belief_dir,
    )

    assert belief_dir.is_dir()  # run_batch mkdir
    assert isinstance(calls[0]["belief_store"], JsonlBeliefStateStore)


def test_belief_dir_none_for_v0_arm(monkeypatch, tmp_path):
    # v0 无 belief lane，builder 守卫禁止注 store → 必须传 None（即便给了 belief_dir）。
    calls = []
    monkeypatch.setattr(mixed, "build_game", _fake_build_game_capture(calls))
    monkeypatch.setattr(mixed, "_resolve_injected_agents", lambda *_a: [])
    monkeypatch.setattr(mixed, "compute_mixed_metrics", lambda **_k: {})

    with pytest.warns(RuntimeWarning, match="no belief lane"):
        mixed.run_batch(
            arm_wolves="v0",
            arm_villagers="v0",
            games=1,
            seed_start=401,
            agent_factory=mixed._make_mock_agent_factory(),
            belief_dir=tmp_path / "belief_states",
        )

    assert calls[0]["belief_store"] is None


def test_no_belief_dir_leaves_store_none(monkeypatch):
    calls = []
    monkeypatch.setattr(mixed, "build_game", _fake_build_game_capture(calls))
    monkeypatch.setattr(mixed, "_resolve_injected_agents", lambda *_a: ["P1"])
    monkeypatch.setattr(mixed, "compute_mixed_metrics", lambda **_k: {})

    mixed.run_batch(
        arm_wolves="v1",
        arm_villagers="v1",
        games=1,
        seed_start=402,
        agent_factory=mixed._make_mock_agent_factory(),
    )

    assert calls[0]["belief_store"] is None
