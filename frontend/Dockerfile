# 前端镜像 —— Next.js 15 多阶段构建（服务器无需系统装 node）
# 构建：docker build --build-arg NEXT_PUBLIC_API_BASE_URL=http://<api-host>:8000 -t ai_wolf_frontend:latest .
# 运行：docker run -d --name ai_wolf_frontend --restart unless-stopped -p 127.0.0.1:3000:3000 ai_wolf_frontend:latest
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* 在 build 阶段烤进浏览器包，必须在构建时传入
ARG NEXT_PUBLIC_API_BASE_URL
ARG NEXT_PUBLIC_GRAFANA_BASE_URL
RUN test -n "$NEXT_PUBLIC_API_BASE_URL" || (echo "ERROR: --build-arg NEXT_PUBLIC_API_BASE_URL is required" && exit 1)
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_GRAFANA_BASE_URL=$NEXT_PUBLIC_GRAFANA_BASE_URL
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.mjs ./next.config.mjs
EXPOSE 3000
CMD ["node", "node_modules/next/dist/bin/next", "start", "-p", "3000"]
