# AI 狼人杀多 Agent 实验平台

本项目获得字节跳动 AI 全栈挑战赛优秀奖。

[English README](README.md)

AI 狼人杀是一个面向信息不对称社交推理游戏的多 Agent 实验平台。它让多个 LLM Agent 在严格信息隔离下进行狼人杀博弈，并记录每一次决策对应的上下文、prompt、belief 状态、行动和结果，用于审计、复盘、评测和策略迭代。

核心实验线：

- `v0`：纯 LLM 决策，后台维护 shadow belief 供赛后分析。
- `v1`：向 Agent 上下文注入 additive belief。
- `v2`：factorized belief 加 slow-think 慢思反思。

## 功能

- 标准 9 人狼人杀：3 狼、预言家、女巫、猎人、3 民。
- 严格信息隔离：Agent 只接收序列化后的可见上下文，无法读取隐藏真相或引擎内部对象。
- 覆盖主要角色的专属 prompt 和策略。
- 支持悍跳、对跳、被质疑、平票复投、女巫救毒、猎人开枪等高级策略片段。
- 结构化记录事件流、决策轨迹、belief 状态、replay truth 和批跑报告。
- 人机混战：一名真人可以加入 AI 对局。
- 前端支持实时观战和赛后 replay。
- 审计页覆盖 timeline、belief、context、decisions、errors、network、raw 等视角。
- 支持按批次生成策略复盘草稿。
- 提供 metrics 接口，便于接入外部可视化工具。

## 四大能力

| 能力 | 实现 |
|---|---|
| 可观测 | 结构化事件流、决策轨迹、Belief 状态落盘 |
| 可评测 | Shadow belief、belief 信号指标、v0/v1/v2 批量对比 |
| 可复盘 | Replay UI、Belief 曲线、怀疑网络、多视角审计 |
| 可调优 | Prompt 版本管理、策略复盘草稿 |

## 架构

系统分为六层：

Frontend -> API/Session -> Game Core -> Supervisor/Context -> Agent Runtime/Policy -> Observability/Evaluation

核心边界：

- `game_core/` 管真相和规则。
- `supervisor/` 管调度编排。
- `context/` 管可见性和上下文装配。
- `agent_runtime/` 管 LLM 调用、解析、清洗和兜底。
- `agent_policy/` 管角色行为、prompt policy、belief 更新和策略逻辑。
- `stores/`、`evaluation/`、`api/` 提供持久化、分析和服务接口。

## 快速开始

需要 Python 3.12+。

```bash
# 1. 克隆并进入仓库
git clone <repo-url> && cd ai_wolf

# 2. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements-dev.txt

# 4. 从模板创建本地环境变量文件
cp .env.example .env

# 5. 跑一局 mock 对局，不需要 LLM 凭证
python scripts/start_game.py --mode mock --player-count 9 --arm v0 --seed 0

# 6. 启动后端 API
uvicorn api.main:app --reload

# 7. 启动前端
cd frontend && npm install && npm run dev
```

默认本地服务：

- 后端 API：`http://localhost:8000`
- 前端：`http://localhost:3000`

## Docker

构建并运行后端：

```bash
docker build -t ai_wolf:0.1 .

docker run -d --name ai_wolf --restart=always \
  -p 8000:8000 \
  --env-file .env \
  -v /var/lib/ai_wolf/data:/data \
  ai_wolf:0.1
```

构建并运行前端：

```bash
cd frontend
docker build \
  --build-arg NEXT_PUBLIC_API_BASE_URL=http://<api-host>:8000 \
  -t ai_wolf_frontend:latest .

docker run -d --name ai_wolf_frontend --restart unless-stopped \
  -p 127.0.0.1:3000:3000 ai_wolf_frontend:latest
```

## 配置

所有运行配置都通过环境变量提供。仓库只包含 `.env.example` 模板，不包含任何真实密钥或私有端点。

```bash
cp .env.example .env
```

Mock 和 fake 模式不需要真实 LLM 凭证。接入真实模型时，在本地 `.env` 中填写对应供应商配置即可。`.env` 已被 Git 忽略。

## 跑对局

```bash
# Mock 对局
python scripts/start_game.py --mode mock --player-count 9 --arm v0 --seed 0
python scripts/start_game.py --mode mock --player-count 9 --games 10 --seed 0

# 真实 LLM 对局
python scripts/start_game.py --mode llm --arm v0 --player-count 9
python scripts/start_game.py --mode llm --arm v1 --model-flavor PRO --temperature 0.7

# 批量跑局
python scripts/run_batch.py --arm v0 --games 10 --concurrency 5 --model-flavor DEEPSEEK
python scripts/run_batch.py --arm v1 --games 10 --concurrency 5
python scripts/run_batch.py --arm v2 --games 10 --concurrency 5
```

`scripts/` 目录还包含 v0 批跑、混合 belief 实验、replay 导出和策略复盘生成等脚本。

## 复现实验

论文审稿人如需复现，主要入口是批跑脚本。建议先运行快速开始里的 mock smoke test，确认本地环境无误；随后在本地 `.env` 中配置真实 LLM provider，并对需要比较的各个 arm 使用相同 seed 区间。

```bash
# 主实验：v0/v1/v2 对比
python scripts/run_batch.py --arm v0 --games 30 --seed-start 0 --concurrency 5 --model-flavor DEEPSEEK
python scripts/run_batch.py --arm v1 --games 30 --seed-start 0 --concurrency 5 --model-flavor DEEPSEEK
python scripts/run_batch.py --arm v2 --games 30 --seed-start 0 --concurrency 5 --model-flavor DEEPSEEK

# 混合 belief 消融
python scripts/run_mixed_batch.py --arm-wolves v1 --arm-villagers v0 --games 30 --seed-start 300 --concurrency 4 --model-flavor DEEPSEEK
python scripts/run_mixed_batch.py --arm-wolves v0 --arm-villagers v1 --games 30 --seed-start 400 --concurrency 4 --model-flavor DEEPSEEK
```

批跑产物会写入 `AI_WOLF_DATA_DIR`（默认 `./data`），包括 JSONL events、traces、belief states 和 batch reports。`data/` 和 `.env` 都已被 Git 忽略。由于真实 LLM 会受到供应商、模型版本、temperature 和限流重试影响，聚合数值可能有小幅波动；复现时建议固定 seeds，并在结果中记录 provider/model 配置。

## 测试

```bash
pytest
pytest tests/game_core/
pytest tests/path/to/test_file.py::test_name
```

测试不需要真实 LLM 凭证。

## 目录结构

```text
contracts/      # Pydantic schema、枚举、fixtures 和冻结快照
game_core/      # 游戏引擎、真相状态、阶段、规则校验和胜负判断
supervisor/     # 对局调度编排
context/        # 可见性规则和上下文装配
agent_policy/   # 角色策略、belief 规则和 prompt policy
agent_runtime/  # LLM provider、解析、清洗和兜底
stores/         # 事件、轨迹、belief、replay 和策略存储
evaluation/     # 策略复盘和 belief 评测
runner/         # CLI/API 共用的对局装配
api/            # FastAPI 服务
frontend/       # Next.js 前端
scripts/        # 开局、批跑、导出和复盘脚本
tests/          # 测试套件
```

## 技术栈

Python 3.12、Pydantic 2、FastAPI、uvicorn、httpx、PyYAML、Next.js 15、TypeScript、Docker。

LLM provider 可通过环境变量选择，包括 Volcengine Ark/Doubao、DeepSeek、OpenAI-compatible API 和 fake 离线模式。
