# AI Wolf backend —— C / S2.5 Azure 部署用镜像。
#
# 设计：单阶段、Python 3.13-slim、不带任何测试代码。Engine + Agent
# Policy + C 的 Runtime/Context/Stores/API 全部进镜像，运行时由环境变量
# AI_WOLF_STORAGE_BACKEND 决定 InMemory↔JSONL。
#
# 构建：
#   docker build -t ai_wolf:0.1 .
#
# 运行（JSONL 落盘到 host 卷）：
#   docker run -d --name ai_wolf --restart=always \
#     -p 8000:8000 \
#     -e AI_WOLF_STORAGE_BACKEND=jsonl \
#     -e AI_WOLF_DATA_DIR=/data \
#     -v /var/lib/ai_wolf/data:/data \
#     ai_wolf:0.1

FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AI_WOLF_STORAGE_BACKEND=memory \
    AI_WOLF_DATA_DIR=/data

WORKDIR /app

# 装依赖（layer cache 友好：只有 requirements.txt 变才重建这一层）
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 复制源码
COPY contracts /app/contracts
COPY game_core /app/game_core
COPY supervisor /app/supervisor
COPY agent_policy /app/agent_policy
COPY agent_runtime /app/agent_runtime
COPY context /app/context
COPY stores /app/stores
COPY runner /app/runner
COPY api /app/api
COPY scripts /app/scripts
# evaluation/ 含策略复盘（evaluation/strategy_review）等 B 区评测组件，后端 import 需要。
COPY evaluation /app/evaluation

# 默认创建数据目录（JSONL 后端用）
RUN mkdir -p /data/events /data/traces

EXPOSE 8000

# Docker level 健康检查（容器内 curl 不一定在；用 python urlopen 最稳）
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()" || exit 1

# 用 uvicorn 直接起；保留 proxy headers 支持，便于放在任意反向代理后。
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
