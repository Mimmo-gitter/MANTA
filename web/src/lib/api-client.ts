import { getUserManager } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface FastApiValidationItem {
  msg?: string;
}

interface ApiErrorPayload {
  detail?: string | FastApiValidationItem[];
}

export class ApiHttpError extends Error {
  readonly status: number;
  readonly detail?: string;

  constructor(status: number, message: string, detail?: string) {
    super(message);
    this.name = "ApiHttpError";
    this.status = status;
    this.detail = detail;
  }
}

export function isApiHttpError(error: unknown): error is ApiHttpError {
  return error instanceof ApiHttpError;
}

function extractErrorDetail(payload: ApiErrorPayload): string | undefined {
  if (typeof payload.detail === "string" && payload.detail.trim()) {
    return payload.detail;
  }
  if (Array.isArray(payload.detail)) {
    const messages = payload.detail
      .map((item) => item?.msg)
      .filter((msg): msg is string => typeof msg === "string" && msg.length > 0);
    if (messages.length > 0) {
      return messages.join("; ");
    }
  }
  return undefined;
}

async function parseApiResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `Request failed: ${response.status}`;
    let detail: string | undefined;

    try {
      const error = (await response.json()) as ApiErrorPayload;
      detail = extractErrorDetail(error);
      if (detail) {
        message = detail;
      }
    } catch {
      // Keep the default status error message when response body is not JSON.
    }

    throw new ApiHttpError(response.status, message, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<T> {
  const manager = getUserManager();
  let user = await manager.getUser();

  if (!user || user.expired) {
    user = await manager.signinSilent();
  }

  if (!user?.access_token || user.expired) {
    throw new Error("Your session has expired. Please log in again.");
  }

  const response = await fetch(`${API_URL}${endpoint}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
      Authorization: `Bearer ${user.access_token}`,
    },
  });

  return parseApiResponse<T>(response);
}

/**
 * Execute an unauthenticated JSON request against the backend API (for public flows
 * such as accepting workspace invitations before a portal session exists).
 */
export async function publicApiRequest<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${API_URL}${endpoint}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  return parseApiResponse<T>(response);
}
