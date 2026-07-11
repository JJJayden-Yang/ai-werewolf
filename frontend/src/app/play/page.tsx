"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import type { CreateGameRequest } from "@/lib/api/gameApi";

const MODEL_OPTIONS: { value: CreateGameRequest["model_flavor"]; label: string }[] = [
  { value: "PRO", label: "Doubao Pro" },
  { value: "CODE", label: "Doubao Code" },
  { value: "DEEPSEEK", label: "DeepSeek V4 Flash" }
];

const ARM_OPTIONS: { value: CreateGameRequest["arm"]; label: string; desc: string }[] = [
  { value: "v0", label: "V0", desc: "无 Belief" },
  { value: "v1", label: "V1", desc: "Additive" },
  { value: "v2", label: "V2", desc: "Factorized" },
];

function randomSeed(): number {
  return Math.floor(Math.random() * 1_000_000_000);
}

export default function PlaySetupPage() {
  const router = useRouter();
  const [playerCount, setPlayerCount] = useState<CreateGameRequest["player_count"]>(9);
  const [arm, setArm] = useState<CreateGameRequest["arm"]>("v0");
  const [mode, setMode] = useState<CreateGameRequest["mode"]>("llm");
  const [modelFlavor, setModelFlavor] = useState<CreateGameRequest["model_flavor"]>("PRO");
  const [seed, setSeed] = useState(() => randomSeed());
  const [temperature, setTemperature] = useState(0.8);
  const [error, setError] = useState<string | null>(null);

  const formattedTemperature = useMemo(
    () => temperature.toFixed(1),
    [temperature]
  );

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      const params = new URLSearchParams({
        player_count: String(playerCount),
        arm,
        seed: String(seed),
        temperature: String(temperature),
        mode,
        model_flavor: modelFlavor
      });
      router.push(`/play/agents?${params.toString()}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "进入角色大厅失败");
    }
  }

  return (
    <section className="screen play-screen">
      <div className="play-shell">
        <header className="play-header">
          <div>
            <p className="eyebrow">游戏设置</p>
            <h1 className="page-title">开始游戏</h1>
          </div>
          <Link className="secondary-button play-back" href="/">
            返回主菜单
          </Link>
        </header>

        <form className="play-form" onSubmit={handleSubmit}>
          <fieldset className="setup-panel">
            <legend>玩家人数</legend>
            <div className="segmented-control">
              {[6, 9].map((count) => (
                <button
                  className={playerCount === count ? "active" : ""}
                  key={count}
                  onClick={() => setPlayerCount(count as 6 | 9)}
                  type="button"
                >
                  {count} 人
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="setup-panel">
            <legend>实验版本</legend>
            <div className="segmented-control">
              {ARM_OPTIONS.map(({ value, label, desc }) => (
                <button
                  className={arm === value ? "active" : ""}
                  key={value}
                  onClick={() => setArm(value)}
                  type="button"
                >
                  {label}<span className="arm-desc">{desc}</span>
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="setup-panel">
            <legend>对局引擎</legend>
            <div className="segmented-control">
              {([
                ["llm", "真实 LLM"],
                ["mock", "Mock"]
              ] as const).map(([value, label]) => (
                <button
                  className={mode === value ? "active" : ""}
                  key={value}
                  onClick={() => setMode(value)}
                  type="button"
                >
                  {label}
                </button>
              ))}
            </div>
          </fieldset>

          {mode === "llm" ? (
            <fieldset className="setup-panel">
              <legend>模型</legend>
              <div className="segmented-control">
                {MODEL_OPTIONS.map(({ value, label }) => (
                  <button
                    className={modelFlavor === value ? "active" : ""}
                    key={value}
                    onClick={() => setModelFlavor(value)}
                    type="button"
                  >
                    {label}
                  </button>
                ))}
              </div>
            </fieldset>
          ) : null}

          <label className="setup-panel setup-label">
            <span>随机种子</span>
            <div className="seed-row">
              <input
                className="text-input"
                min={0}
                onChange={(event) => setSeed(Math.max(0, Number(event.target.value)))}
                type="number"
                value={seed}
              />
              <button
                className="secondary-button"
                onClick={() => setSeed(randomSeed())}
                type="button"
              >
                随机
              </button>
            </div>
          </label>

          <label className="setup-panel setup-label">
            <span>温度：{formattedTemperature}</span>
            <input
              className="temperature-slider"
              max={1}
              min={0}
              onChange={(event) => setTemperature(Number(event.target.value))}
              step={0.1}
              type="range"
              value={temperature}
            />
          </label>

          {error ? <p className="form-error">{error}</p> : null}

          <button className="play-button setup-submit" type="submit">
            下一步：选择角色模板
          </button>
        </form>
      </div>
    </section>
  );
}
