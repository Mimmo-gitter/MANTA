const CHANNEL_NAME = "manta_auth_sync";
const tabId = typeof window !== "undefined" ? crypto.randomUUID() : "";

export function getTabId(): string {
  return tabId;
}

export type AuthSyncMessage =
  | { type: "RENEW_STARTED"; tabId: string }
  | { type: "RENEW_COMPLETED"; tabId: string }
  | { type: "RENEW_FAILED"; tabId: string }
  | { type: "LOGIN_COMPLETED"; tabId: string }
  | { type: "LOGOUT"; tabId: string };

let channel: BroadcastChannel | null = null;

export function initAuthSync(
  onMessage: (message: AuthSyncMessage) => void,
): () => void {
  if (typeof window === "undefined" || !("BroadcastChannel" in window)) {
    return () => {};
  }

  channel = new BroadcastChannel(CHANNEL_NAME);

  channel.onmessage = (event: MessageEvent<AuthSyncMessage>) => {
    onMessage(event.data);
  };

  return () => {
    channel?.close();
    channel = null;
  };
}

export function broadcastAuthMessage(message: AuthSyncMessage): void {
  channel?.postMessage(message);
}

export async function withRenewLock<T>(callback: () => Promise<T>): Promise<T> {
  if (typeof navigator === "undefined" || !navigator.locks) {
    // Fallback: no cross-tab lock available; in-tab single-flight still applies.
    return callback();
  }

  return navigator.locks.request("manta_oidc_renew_lock", async () => {
    return await callback();
  });
}
