import { env } from "@/lib/env";

type QueryValue = string | number | boolean | null | undefined;

export interface ApiFetchOptions extends Omit<RequestInit, "body" | "headers"> {
  headers?: Record<string, string>;
  /** Query-string parameters; null/undefined/empty values are dropped. */
  searchParams?: Record<string, QueryValue>;
  /** JSON request body. */
  json?: unknown;
}

interface ErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    details?: Array<{ field?: string; message?: string }>;
    request_id?: string | null;
  };
}

/** Mirrors the backend's error envelope: `{ error: { code, message, details, request_id } }`. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Array<{ field?: string; message?: string }>;
  readonly requestId: string | null;

  constructor(status: number, envelope: ErrorEnvelope["error"], fallbackMessage: string) {
    super(envelope?.message ?? fallbackMessage);
    this.name = "ApiError";
    this.status = status;
    this.code = envelope?.code ?? "unknown_error";
    this.details = envelope?.details ?? [];
    this.requestId = envelope?.request_id ?? null;
  }

  static async fromResponse(response: Response): Promise<ApiError> {
    let envelope: ErrorEnvelope["error"];
    try {
      envelope = ((await response.json()) as ErrorEnvelope).error;
    } catch {
      envelope = undefined;
    }
    return new ApiError(response.status, envelope, `Request failed with status ${response.status}`);
  }
}

function baseUrl(): string {
  return typeof window === "undefined" ? env.serverApiUrl : env.publicApiUrl;
}

export async function apiFetch<T>(
  path: string,
  { searchParams, json, headers, ...init }: ApiFetchOptions = {},
): Promise<T> {
  const url = new URL(path, baseUrl());
  for (const [key, value] of Object.entries(searchParams ?? {})) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }

  const response = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: json !== undefined ? JSON.stringify(json) : undefined,
  });

  if (!response.ok) throw await ApiError.fromResponse(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
