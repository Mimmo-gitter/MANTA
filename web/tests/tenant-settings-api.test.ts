import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, it } from "node:test";
import { User } from "oidc-client-ts";

import { getUserManager, resetUserManagerForTesting } from "@/lib/auth";
import {
  TENANT_RETENTION_MAX_DAYS,
  TENANT_RETENTION_MIN_DAYS,
  buildTenantSettingsPatch,
  createDraftFromSettings,
  formatRetentionDuration,
  getReindexProgressPercent,
  getTenantSettings,
  hasTenantSettingsChanges,
  isReindexActive,
  normalizeAllowedDomain,
  updateTenantSettings,
  validateTenantSettingsDraft,
} from "@/lib/tenant-settings-api";
import type { TenantSettingsResponse } from "@/types/tenant-settings";

const BASE_SETTINGS: TenantSettingsResponse = {
  tenant_id: "11111111-1111-1111-1111-111111111111",
  slug: "acme-core",
  name: "Acme Core",
  allowed_domain: "acme.com",
  industry: "FinTech",
  tier: "enterprise",
  status: "active",
  max_projects: 25,
  ai_processing_mode: "cloud_zdr",
  retention_days: 90,
  active_embedding_dimensions: 1536,
  active_embedding_model: "text-embedding-3-small",
  active_llm_model: "gpt-4o-mini",
  reindexing: false,
  reindex_status: "idle",
  reindexed_count: 0,
  reindex_total_count: 0,
  reindex_job_id: null,
  reindex_error: null,
  updated_at: "2026-11-28T10:00:00Z",
};

const originalWindow = globalThis.window;
const originalFetch = globalThis.fetch;

function createMemoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.has(key) ? (store.get(key) ?? null) : null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, String(value));
    },
  };
}

function installAuthenticatedBrowser(): void {
  const parsed = new URL("https://manta.example.com/workspace-settings");
  const mockWindow = {
    location: {
      href: parsed.toString(),
      origin: parsed.origin,
      hostname: parsed.hostname,
      protocol: parsed.protocol,
    },
    localStorage: createMemoryStorage(),
    sessionStorage: createMemoryStorage(),
    dispatchEvent: () => true,
    __MANTA_RUNTIME_CONFIG__: Object.freeze({
      apiUrl: "https://api.manta.example.com",
      keycloakUrl: "https://auth.manta.example.com",
      keycloakRealm: "manta",
      keycloakClientId: "manta-portal",
    }),
  };

  Object.defineProperty(globalThis, "window", {
    configurable: true,
    writable: true,
    value: mockWindow,
  });

  const manager = getUserManager();
  const futureExp = Math.floor(Date.now() / 1000) + 3600;
  const user = new User({
    access_token: "test-admin-jwt",
    token_type: "Bearer",
    expires_at: futureExp,
    profile: {
      iss: "https://auth.manta.example.com/realms/manta",
      sub: "admin-1",
      aud: "manta-portal",
      exp: futureExp,
      iat: futureExp - 3600,
    },
  });
  manager.getUser = async () => user;
}

beforeEach(() => {
  resetUserManagerForTesting();
});

afterEach(() => {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    writable: true,
    value: originalWindow,
  });
  globalThis.fetch = originalFetch;
  resetUserManagerForTesting();
});

describe("TASK-313-2: Tenant settings draft diffing & validation", () => {
  it("builds minimal diff-only PATCH payloads without extra fields or null required fields", () => {
    const draft = createDraftFromSettings(BASE_SETTINGS);
    assert.deepEqual(buildTenantSettingsPatch(BASE_SETTINGS, draft), {});
    assert.equal(hasTenantSettingsChanges(BASE_SETTINGS, draft), false);

    // Changing only retention_days must not send ai_processing_mode (avoids re-index trigger)
    const retentionDraft = { ...draft, retention_days_input: "180" };
    assert.deepEqual(buildTenantSettingsPatch(BASE_SETTINGS, retentionDraft), {
      retention_days: 180,
    });
    assert.equal(hasTenantSettingsChanges(BASE_SETTINGS, retentionDraft), true);

    // Switching AI compliance mode sends only ai_processing_mode
    const modeDraft = { ...draft, ai_processing_mode: "local_only" as const };
    assert.deepEqual(buildTenantSettingsPatch(BASE_SETTINGS, modeDraft), {
      ai_processing_mode: "local_only",
    });

    // Clearing optional fields sends explicit null; leading @ in domain normalizes
    const clearedDraft = {
      ...draft,
      name: "  Acme Core Ltd  ",
      allowed_domain: "   ",
      industry: "",
    };
    assert.deepEqual(buildTenantSettingsPatch(BASE_SETTINGS, clearedDraft), {
      name: "Acme Core Ltd",
      allowed_domain: null,
      industry: null,
    });

    // Equivalent domain with leading @ and uppercase is not treated as dirty
    const equivalentDomainDraft = { ...draft, allowed_domain: " @ACME.COM " };
    assert.equal(normalizeAllowedDomain(" @ACME.COM "), "acme.com");
    assert.equal(hasTenantSettingsChanges(BASE_SETTINGS, equivalentDomainDraft), false);
    assert.deepEqual(
      buildTenantSettingsPatch(BASE_SETTINGS, equivalentDomainDraft),
      {},
    );
  });

  it("validates retention bounds (7..3650), integer format, org name, and allowed_domain", () => {
    const validDraft = createDraftFromSettings(BASE_SETTINGS);
    assert.deepEqual(validateTenantSettingsDraft(validDraft), {});

    assert.deepEqual(
      validateTenantSettingsDraft({
        ...validDraft,
        retention_days_input: String(TENANT_RETENTION_MIN_DAYS),
      }),
      {},
    );
    assert.deepEqual(
      validateTenantSettingsDraft({
        ...validDraft,
        retention_days_input: String(TENANT_RETENTION_MAX_DAYS),
      }),
      {},
    );

    const lowErr = validateTenantSettingsDraft({
      ...validDraft,
      retention_days_input: "6",
    });
    assert.match(lowErr.retention_days ?? "", /between 7 and 3650/);

    const highErr = validateTenantSettingsDraft({
      ...validDraft,
      retention_days_input: "3651",
    });
    assert.match(highErr.retention_days ?? "", /between 7 and 3650/);

    const floatErr = validateTenantSettingsDraft({
      ...validDraft,
      retention_days_input: "90.5",
    });
    assert.match(floatErr.retention_days ?? "", /whole number/);

    const badFields = validateTenantSettingsDraft({
      ...validDraft,
      name: "A",
      allowed_domain: "https://acme.com/path",
    });
    assert.match(badFields.name ?? "", /at least 2 characters/);
    assert.match(badFields.allowed_domain ?? "", /valid domain/);
  });

  it("evaluates live re-index activity, progress percentage, and duration labels", () => {
    assert.equal(
      isReindexActive({ reindexing: false, reindex_status: "idle" }),
      false,
    );
    assert.equal(
      isReindexActive({ reindexing: true, reindex_status: "queued" }),
      true,
    );
    assert.equal(
      isReindexActive({ reindexing: false, reindex_status: "running" }),
      true,
    );
    assert.equal(
      isReindexActive({ reindexing: false, reindex_status: "completed" }),
      false,
    );
    assert.equal(
      isReindexActive({ reindexing: false, reindex_status: "failed" }),
      false,
    );

    assert.equal(getReindexProgressPercent(0, 0), null);
    assert.equal(getReindexProgressPercent(16, 32), 50);
    assert.equal(getReindexProgressPercent(40, 32), 100);

    assert.equal(formatRetentionDuration(14), "14 days (~2 weeks)");
    assert.equal(formatRetentionDuration(90), "90 days (~3 months)");
    assert.equal(formatRetentionDuration(365), "365 days (~1 year)");
  });
});

describe("TASK-313-2: Tenant settings HTTP client integration", () => {
  it("calls GET and PATCH /api/v1/tenants/settings with Bearer token and returns parsed settings", async () => {
    installAuthenticatedBrowser();

    const calls: Array<{ url: string; method: string; auth: string | null; body: string | null }> = [];
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      calls.push({
        url: String(input),
        method: init?.method ?? "GET",
        auth: headers.get("Authorization"),
        body: typeof init?.body === "string" ? init.body : null,
      });

      if (init?.method === "PATCH") {
        return new Response(
          JSON.stringify({
            ...BASE_SETTINGS,
            ai_processing_mode: "local_only",
            active_embedding_dimensions: 384,
            active_embedding_model: "bge-small-en-v1.5",
            active_llm_model: "llama3.1:8b",
            reindexing: true,
            reindex_status: "queued",
            reindex_job_id: "job-1234",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      return new Response(JSON.stringify(BASE_SETTINGS), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch;

    const loaded = await getTenantSettings();
    assert.equal(loaded.ai_processing_mode, "cloud_zdr");
    assert.equal(loaded.retention_days, 90);

    const updated = await updateTenantSettings({
      ai_processing_mode: "local_only",
    });
    assert.equal(updated.ai_processing_mode, "local_only");
    assert.equal(updated.reindexing, true);
    assert.equal(updated.reindex_status, "queued");
    assert.equal(updated.active_embedding_dimensions, 384);

    assert.equal(calls.length, 2);
    assert.equal(calls[0]?.url, "https://api.manta.example.com/api/v1/tenants/settings");
    assert.equal(calls[0]?.method, "GET");
    assert.equal(calls[0]?.auth, "Bearer test-admin-jwt");
    assert.equal(calls[1]?.url, "https://api.manta.example.com/api/v1/tenants/settings");
    assert.equal(calls[1]?.method, "PATCH");
    assert.equal(calls[1]?.body, JSON.stringify({ ai_processing_mode: "local_only" }));
  });
});
