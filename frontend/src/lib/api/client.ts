import { ApiError } from "./errors";

const DEFAULT_API_BASE_URL = "http://localhost:8000";

export function getApiBaseUrl(): string {
  // SSR（服务端）走内网直连后端，避免从容器绕公网 IP hairpin 回来。
  // 运行容器时传 API_BASE_URL_INTERNAL=http://host.docker.internal:8000
  // 并加 --add-host=host.docker.internal:host-gateway（Linux 宿主机必须）。
  if (typeof window === "undefined") {
    return (
      process.env.API_BASE_URL_INTERNAL?.replace(/\/$/, "") ??
      process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ??
      process.env.NEXT_PUBLIC_AI_WOLF_API_BASE_URL?.replace(/\/$/, "") ??
      DEFAULT_API_BASE_URL
    );
  }
  // 浏览器端走公网地址（构建时烤入 NEXT_PUBLIC_API_BASE_URL）
  return (
    process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ??
    process.env.NEXT_PUBLIC_AI_WOLF_API_BASE_URL?.replace(/\/$/, "") ??
    DEFAULT_API_BASE_URL
  );
}

type FetchJsonOptions = {
  method?: "GET" | "POST" | "DELETE";
  body?: unknown;
};

export async function fetchJson<T>(
  path: string,
  options: FetchJsonOptions = {}
): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: options.method ?? "GET",
    headers: {
      accept: "application/json",
      ...(options.body === undefined ? {} : { "content-type": "application/json" })
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    cache: "no-store"
  });

  if (!response.ok) {
    let details: unknown;
    try {
      details = await response.json();
    } catch {
      details = await response.text();
    }
    throw new ApiError(`Request failed: ${path}`, response.status, details);
  }

  return response.json() as Promise<T>;
}
