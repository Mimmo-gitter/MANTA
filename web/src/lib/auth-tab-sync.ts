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

const pendingRenewals = new Map<
  string,
  {
    resolve: (message: AuthSyncMessage) => void;
    timeout: ReturnType<typeof setTimeout>;
  }
>();

export function initAuthSync(
  onMessage: (message: AuthSyncMessage) => void,
): () => void {
  if (typeof window === "undefined" || !("BroadcastChannel" in window)) {
    return () => {};
  }

  channel = new BroadcastChannel(CHANNEL_NAME);

  channel.onmessage = (event: MessageEvent<AuthSyncMessage>) => {
    const message = event.data;

    if (message.type === "RENEW_COMPLETED" || message.type === "RENEW_FAILED") {
      const pending = pendingRenewals.get(message.tabId);

      if (pending) {
        clearTimeout(pending.timeout);
        pending.resolve(message);
        pendingRenewals.delete(message.tabId);
      }
    }

    onMessage(message);
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
    throw new Error("Web Locks API is not supported in this browser");
  }

  return navigator.locks.request("manta_oidc_renew_lock", async () => {
    return await callback();
  });
}
