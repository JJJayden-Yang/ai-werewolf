export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly details?: unknown
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class PendingBackendApiError extends Error {
  constructor(apiName: string) {
    super(`${apiName} is pending backend HTTP API support.`);
    this.name = "PendingBackendApiError";
  }
}

export function formatApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    const detail = error.details;
    if (
      detail &&
      typeof detail === "object" &&
      "detail" in detail &&
      typeof detail.detail === "string"
    ) {
      return detail.detail;
    }
    return error.message;
  }
  return error instanceof Error ? error.message : fallback;
}
