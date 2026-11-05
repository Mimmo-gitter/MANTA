import { getUserManager } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface ApiError {
  detail?: string;
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

  if (!response.ok) {
    let message = `Request failed: ${response.status}`;

    try {
      const error: ApiError = await response.json();
      message = error.detail || message;
    } catch {
      // Keep the default error message.
    }

    throw new Error(message);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}
