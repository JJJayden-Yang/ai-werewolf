<div align="center">

# 🐺 AI 狼人杀 · 多 Agent 实验平台

**让多个 LLM Agent 在严格信息隔离下博弈，并让每一次决策都可观测、可评测、可复盘、可调优。**

Python 3.12+ · Pydantic 2 · FastAPI · Next.js 15 · Docker

[功能](#-功能) · [架构](#-架构) · [快速开始](#-快速开始) · [Docker](#-docker) · [配置](#-配置) · [跑对局](#-跑对局) · [测试](#-测试)

</div>

---

## 简介

本项目不是一款"AI 狼人杀小游戏"，而是一个面向**信息不对称博弈**的多 Agent 实验平台。多个 LLM Agent 在隐藏身份、信息不完全、阵营对抗的狼人杀中完成推理、发言、投票与夜间行动——而平台的核心价值，是把每一个 Agent 决策背后的**上下文、prompt、belief 与结果**全部结构化记录下来，形成一条可追溯的证据链。

核心实验：**v0（纯 LLM）vs v1（Belief 引导）vs v2（Factorized Belief + 慢思）**。

> **实验臂**：`v0` 无 belief 输入（后台仍维护 shadow belief 供赛后分析）；`v1` 将 additive belief 注入上下文引导决策；`v2` 在 v1 基础上换用 factorized belief 内核并启用 slow-think 慢思反思。

## ✨ 功能

- **标准 9 人局**：3 狼 / 预言家 / 女巫 / 猎人 / 3 民，含猎人开枪与平票二次投票的完整规则。
- **严格信息隔离**：Agent 只接收 JSON 序列化的上下文，物理上拿不到真相、角色表或引擎内部对象。
- **多角色策略**：6 种角色各自的 prompt 与策略，叠加可注入的高级策略片段（悍跳、对跳、女巫用药等场景）。
- **结构化事件流**：每局的事件、决策轨迹、belief 状态全部落盘（InMemory / JSONL 可切）。
- **人机混战**：支持一名真人占座加入对局，与 AI Agent 同局对抗（`HumanInputChannel` + 前端实时操作页）。
- **实时观战**：通过 REST 轮询观看进行中的对局。
- **Replay 复盘**：时间线、玩家卡片、Belief 曲线、怀疑网络的赛后回放。
- **多视角审计**：timeline / belief / context / decisions / errors / network / raw 等审计页，定位每一步的输入输出。
- **策略复盘**：聚合 N 局自动产出复盘草稿，供人工审阅。
- **指标接口**：提供批跑和在跑对局的 metrics 数据，便于接入外部可视化工具。

## 🎯 四大能力

| 能力 | 实现 |
|---|---|
| **可观测** | 全程结构化事件流 + 决策轨迹 + Belief 状态落盘 |
| **可评测** | shadow belief + belief 信号指标（一致率 / 准确率）+ v0·v1·v2 批量对比 |
| **可复盘** | Replay + Belief 曲线 + 怀疑网络 + 多视角审计页 |
| **可调优** | prompt 版本管理 + 策略复盘（LLM 聚合草稿 + 人工审阅） |

## 🏗 架构

六层自上而下：Frontend → API/Session → Game Core → Supervisor/Context → Agent Runtime/Policy → Observability/Evaluation。

一句话原则：**Engine 管真相和规则；Supervisor 管调度；ContextAssembler 管信息边界；Agent 管策略；EventLog 管观测；Evaluator 管评测。**

本 README 保留了开源使用所需的核心架构、快速开始、Docker 和测试说明。

## 🚀 快速开始

需要 Python 3.12+。

```bash
# 1. 克隆并进入
git clone <repo-url> && cd ai_wolf

# 2. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate          # Windows: source .venv/Scripts/activate

# 3. 安装依赖
pip install -r requirements-dev.txt

# 4. 配置环境变量（模板不含真实密钥；不填可用 mock/fake）
cp .env.example .env

# 5. 跑一局 mock（不消耗 token，验证链路）
python scripts/start_game.py --mode mock --player-count 9 --arm v0 --seed 0

# 6. 起 API（Replay / 审计 / metrics）
uvicorn api.main:app --reload      # http://localhost:8000

# 7. 起前端
cd frontend && npm install && npm run dev   # http://localhost:3000
```

## 📦 Docker

如需容器化运行，可以分别构建后端和前端镜像。

### 后端

```bash
docker build -t ai_wolf:0.1 .

docker run -d --name ai_wolf --restart=always \
  -p 8000:8000 \
  --env-file .env \
  -v /var/lib/ai_wolf/data:/data \
  ai_wolf:0.1
```

后端镜像内置 `/health` 健康检查。

### 前端

`NEXT_PUBLIC_*` 在 **构建时**烤进浏览器包，因此后端地址需在 `docker build` 时通过 `--build-arg` 传入：

```bash
cd frontend
docker build \
  --build-arg NEXT_PUBLIC_API_BASE_URL=http://<api-host>:8000 \
  -t ai_wolf_frontend:latest .

docker run -d --name ai_wolf_frontend --restart unless-stopped \
  -p 127.0.0.1:3000:3000 ai_wolf_frontend:latest
```

## ⚙️ 配置

所有配置走环境变量。仓库只提供 [`.env.example`](.env.example) 模板，不提交任何真实密钥或私有端点。

```bash
cp .env.example .env
```

本地运行 mock/fake 模式不需要填真实 LLM 凭证；接入真实模型时，在自己的 `.env` 里填入对应供应商配置。`.env` 已被 `.gitignore` 忽略。

## 🎮 跑对局

```bash
# Mock（不消耗 token）
python scripts/start_game.py --mode mock --player-count 9 --arm v0 --seed 0
python scripts/start_game.py --mode mock --player-count 9 --games 10 --seed 0

# 真实 LLM（消耗 token，需本地 .env 有凭证）
python scripts/start_game.py --mode llm --arm v0 --player-count 9
python scripts/start_game.py --mode llm --arm v1 --model-flavor PRO --temperature 0.7

# 批量跑局（落盘供审计平台扫描）；arm 可选 v0 / v1 / v2
python scripts/run_batch.py --arm v0 --games 10 --concurrency 5 --model-flavor DEEPSEEK
python scripts/run_batch.py --arm v1 --games 10 --concurrency 5
python scripts/run_batch.py --arm v2 --games 10 --concurrency 5   # factorized belief + 慢思
```

更多脚本见 [`scripts/`](scripts/)（本地 v0 批量、混合 belief 批量、策略复盘等）。

## 🧪 测试

```bash
pytest                  # 全量（契约冻结快照 + 全模块，不依赖真实 LLM）
pytest tests/game_core/ # 单模块
pytest tests/path/to/test_file.py::test_name   # 单个测试
```

CI（`.gitlab-ci.yml`）启用 SAST + Secret Detection + `pytest` 门禁。

## 📁 目录结构

```text
contracts/      # Pydantic schema、枚举、fixtures（跨模块唯一契约）
game_core/      # 引擎：真相与规则的权威
supervisor/     # 调度编排
context/        # 信息边界：可见性规则 + 上下文装配
agent_policy/   # 角色策略 + Belief 更新规则
agent_runtime/  # LLM 调用 + 解析 + 清洗 + 兜底
stores/         # 事件 / 轨迹 / belief 落盘
evaluation/     # 策略复盘（聚合 + LLM）/ belief 准确率评测
runner/         # CLI 与 API 共用的装配 root
api/            # FastAPI：对局 / replay / 审计 / metrics
frontend/       # Next.js：Replay / Dashboard / 实时观战
scripts/        # 开局 / 批跑 / 评测脚本
tests/          # 全模块测试
```

## 🛠 技术栈

Python 3.12 · Pydantic 2 · FastAPI + uvicorn · httpx · PyYAML · Next.js 15 + TypeScript · Docker

LLM 经 Volcengine Ark / Doubao、DeepSeek 或 OpenAI-compatible API 接入（`LLM_PROVIDER` 可切，含 `fake` 离线档）。
