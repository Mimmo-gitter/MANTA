# Webhook Duplicate-Delivery Protection Test Scenarios (GitHub, Jira & Slack)

**Tasks:** Create test scenarios for GitHub, Jira and Slack duplicates (`SCRUM-336`) & Write automated pytest tests for duplicate delivery (`SCRUM-337`)  
**Parent User Story:** `SCRUM-252` (`US-16` Duplicate delivery protection)  
**Related Tickets:** `SCRUM-335` (Delivery ID extraction & idempotent staging), `SCRUM-325` / `SCRUM-326` (HMAC-SHA256 & Slack replay verification), `SCRUM-327` (Integration-bound tenant routing), `SCRUM-331` (PostgreSQL outage resilience)  
**Target Database Table:** `webhook_events` (`uq_webhook_provider_delivery (tenant_id, provider, provider_delivery_id)`)  
**Implementation Modules:** [`backend/app/webhooks/delivery.py`](../backend/app/webhooks/delivery.py), [`backend/app/webhooks/service.py`](../backend/app/webhooks/service.py), [`backend/app/webhooks/router.py`](../backend/app/webhooks/router.py)  
**Automated Pytest Suite:** [`backend/tests/test_webhook_delivery_idempotency.py`](../backend/tests/test_webhook_delivery_idempotency.py)

---

## 1. Objective & Architectural Invariants

Webhook providers (**GitHub**, **Jira**, and **Slack**) operate on at-least-once delivery semantics and frequently redeliver identical events after network timeouts, transient retries, or client re-triggers. The MANTA platform enforces idempotent duplicate-delivery protection at the PostgreSQL storage boundary (`webhook_events`) across four core invariants:

1. **Provider-Specific Delivery ID Extraction & Normalization ([`extract_delivery_id`](../backend/app/webhooks/delivery.py#L34-L138)):**
   - **GitHub (`provider = 'github'`):**
     1. Primary header: `X-GitHub-Delivery` (UUID per webhook delivery).
     2. Payload fallbacks: `payload.delivery_id` or `gh-{payload.id}` when `id` and `repository` are present.
     3. Deterministic missing-ID fallback: `gen-github-<sha256(raw_body)[:24]>`.
   - **Jira (`provider = 'jira'`):**
     1. Primary headers: `X-Atlassian-Webhook-Identifier` (or `X-Atlassian-Token` / `X-Request-Id`).
     2. Payload fallbacks: `jira-{webhookEvent}-{timestamp}`, `jira-issue-{issue.id}-{timestamp}`, or `payload.eventId`.
     3. Deterministic missing-ID fallback: `gen-jira-<sha256(raw_body)[:24]>`.
   - **Slack (`provider = 'slack'`):**
     1. Primary payload field: `payload.event_id` (e.g., `Ev024BE92L`).
     2. Header fallback: `slack-{X-Slack-Request-Timestamp}-r{X-Slack-Retry-Num|0}`.
     3. Deterministic missing-ID fallback: `gen-slack-<sha256(raw_body)[:24]>`.

2. **Composite Tenant + Provider + Delivery ID Uniqueness (`uq_webhook_provider_delivery`):**
   The `webhook_events` table enforces a composite unique constraint on `(tenant_id, provider, provider_delivery_id)` alongside PostgreSQL `FORCE ROW LEVEL SECURITY` (`tenant_isolation_policy`):
   ```sql
   INSERT INTO webhook_events (
       id, tenant_id, integration_id, provider, provider_delivery_id,
       event_type, received_at, payload, status, attempts, created_at
   ) VALUES (
       :id, :tenant_id, :integration_id, :provider, :provider_delivery_id,
       :event_type, :received_at, CAST(:payload_json AS jsonb), 'staged', 0, :created_at
   )
   ON CONFLICT (tenant_id, provider, provider_delivery_id) DO NOTHING
   RETURNING id;
   ```
   - **Newly Staged Delivery (`INSERT` returns `id`):** Responds `HTTP 202 Accepted` with `duplicate = false`, the newly created `event_id`, and `message = "Webhook staged successfully"`.
   - **Duplicate Redelivery (`ON CONFLICT DO NOTHING` returns `NULL`):** Suppresses the duplicate write without raising a constraint error, looks up the existing `id` for `(tenant_id, provider, provider_delivery_id)`, and responds `HTTP 202 Accepted` with `duplicate = true`, the original `event_id`, and `message = "Duplicate webhook ignored (already staged)"`.

3. **Cross-Tenant Independence (Same ID in Different Tenants $\rightarrow$ Both Stored):**
   Because uniqueness is scoped to `(tenant_id, provider, provider_delivery_id)`, if **Tenant A** and **Tenant B** receive a webhook with the exact same `provider` and `provider_delivery_id`, **both events are stored** (**1 row** in Tenant A, **1 row** in Tenant B; **2 rows total** across tenants). Neither tenant can block or infer the other tenant's deliveries.

4. **Cross-Provider Independence (Same ID in Different Providers $\rightarrow$ Both Stored):**
   Because `provider` is part of `(tenant_id, provider, provider_delivery_id)`, if the same `provider_delivery_id` arrives for different providers (e.g., `github` and `jira`, `github` and `slack`, `jira` and `slack`, or all three), **every provider's event is stored** (**1 row per provider**; **2 rows** for any pair, **3 rows** across all three providers).

---

## 2. Canonical Seed Fixtures (`Tenant A`, `Tenant B` & Provider Integrations)

All test scenarios operate against two isolated tenants (`Tenant A` and `Tenant B`) and support both provider-level intake (`POST /api/v1/webhooks/{provider}`) and integration-bound intake (`POST /api/v1/webhooks/integrations/{integration_id}`):

| Fixture Entity | Tenant Ownership | UUID (`id` / `tenant_id`) | Provider | Secret / Routing Context |
| :--- | :--- | :--- | :---: | :--- |
| **Tenant A** (`tenants`) | Tenant A | `11111111-1111-1111-1111-111111111111` | — | `slug = 'tenant-alpha-dup'` |
| **Tenant B** (`tenants`) | Tenant B | `22222222-2222-2222-2222-222222222222` | — | `slug = 'tenant-beta-dup'` |
| **GitHub Integration A** | Tenant A | `aaaa1111-1111-1111-1111-111111111111` | `github` | Encrypted HMAC secret (`X-Hub-Signature-256`) |
| **Jira Integration A** | Tenant A | `aaaa2222-2222-2222-2222-222222222222` | `jira` | Encrypted HMAC secret (`X-Hub-Signature-256`) |
| **Slack Integration A** | Tenant A | `aaaa3333-3333-3333-3333-333333333333` | `slack` | Encrypted signing secret (`X-Slack-Signature` + 5m window) |
| **GitHub Integration B** | Tenant B | `bbbb1111-1111-1111-1111-111111111111` | `github` | Encrypted HMAC secret (`X-Hub-Signature-256`) |
| **Jira Integration B** | Tenant B | `bbbb2222-2222-2222-2222-222222222222` | `jira` | Encrypted HMAC secret (`X-Hub-Signature-256`) |
| **Slack Integration B** | Tenant B | `bbbb3333-3333-3333-3333-333333333333` | `slack` | Encrypted signing secret (`X-Slack-Signature` + 5m window) |

---

## 3. Master Test Scenarios Matrix (With Expected Row Counts)

### 3.1 Per-Provider Duplicate & Missing-ID Scenarios (`GitHub`, `Jira`, `Slack`)

| Scenario ID | Provider | Case Category | Delivery Inputs & Sequence | Expected HTTP Responses | Expected `webhook_events` Row Count | Automated Pytest Mapping (`backend/tests/test_webhook_delivery_idempotency.py`) |
| :--- | :---: | :--- | :--- | :--- | :--- | :--- |
| **TC-DUP-GH-001** | **GitHub** | **Same event twice** | Tenant A sends `POST /api/v1/webhooks/github` twice with `X-GitHub-Delivery: gh-del-001` and identical `push` payload. | • **1st:** `202 Accepted` (`duplicate: false`, `event_id = E1`)<br>• **2nd:** `202 Accepted` (`duplicate: true`, `event_id = E1`) | **`1 row`** in Tenant A (`provider = 'github'`, `provider_delivery_id = 'gh-del-001'`, `status = 'staged'`). | `test_webhook_github_idempotency_lifecycle`<br>`test_same_event_twice_per_provider[github]` |
| **TC-DUP-GH-002** | **GitHub** | **Same ID in different tenants (both stored)** | 1. Tenant A sends GitHub webhook with `X-GitHub-Delivery: gh-shared-ten-001`.<br>2. Tenant B sends GitHub webhook with the **same** `X-GitHub-Delivery: gh-shared-ten-001`.<br>3. Tenant A & Tenant B each send a redelivery of `gh-shared-ten-001`. | • **1st (Tenant A):** `202` (`duplicate: false`, `event_id = EA`)<br>• **2nd (Tenant B):** `202` (`duplicate: false`, `event_id = EB`, `EB != EA`)<br>• **3rd (Re-send A & B):** `202` (`duplicate: true`) | **`2 rows total`** (**`1 row`** in Tenant A + **`1 row`** in Tenant B; **both stored**). Under RLS, Tenant A queries return **`1 row`** and Tenant B queries return **`1 row`**. | `test_same_delivery_id_different_tenants_both_stored_per_provider[github]` |
| **TC-DUP-GH-003** | **GitHub** | **Same ID in different providers (both stored)** | Tenant A sends delivery ID `shared-prov-id-001` to **GitHub** (`X-GitHub-Delivery`) and then sends the **same** ID `shared-prov-id-001` to **Jira** and **Slack**. | • **GitHub:** `202` (`duplicate: false`, `event_id = E_GH`)<br>• **Jira:** `202` (`duplicate: false`, `event_id = E_JIRA`)<br>• **Slack:** `202` (`duplicate: false`, `event_id = E_SLACK`) | **`2 rows`** for GitHub + Jira (or GitHub + Slack); **`3 rows total`** across GitHub + Jira + Slack (**`1 row` per provider**, all stored). | `test_same_delivery_id_different_providers_both_stored` |
| **TC-DUP-GH-004** | **GitHub** | **Missing delivery ID** *(Payload fallback & SHA-256 digest)* | **Sub-case A (No ID anywhere):** Tenant A sends GitHub `push` payload `P1` with **no** `X-GitHub-Delivery` header twice, then sends distinct payload `P2` with no header.<br>**Sub-case B (Header omitted, payload ID present):** Omitted `X-GitHub-Delivery`, payload has `delivery_id` or `id` + `repository` (`gh-<id>`), sent twice. | • **P1 (1st):** `202` (`duplicate: false`, `delivery_id = 'gen-github-<hash1>'`)<br>• **P1 (2nd):** `202` (`duplicate: true`, `delivery_id = 'gen-github-<hash1>'`)<br>• **P2 (1st):** `202` (`duplicate: false`, `delivery_id = 'gen-github-<hash2>'`) | • After P1 1st delivery: **`1 row`**<br>• After P1 duplicate redelivery: **`1 row`** (duplicate suppressed)<br>• After distinct P2 delivery: **`2 rows`**<br>• Sub-case B after redelivery: **`1 row`**. | `test_missing_delivery_id_scenarios_per_provider[github]`<br>`test_extract_delivery_id_github`<br>`test_extract_delivery_id_fallback_digest` |
| **TC-DUP-JIRA-001** | **Jira** | **Same event twice** | Tenant A sends `POST /api/v1/webhooks/jira` twice with `X-Atlassian-Webhook-Identifier: jira-del-001` and identical `jira:issue_updated` payload. | • **1st:** `202 Accepted` (`duplicate: false`, `event_id = E1`)<br>• **2nd:** `202 Accepted` (`duplicate: true`, `event_id = E1`) | **`1 row`** in Tenant A (`provider = 'jira'`, `provider_delivery_id = 'jira-del-001'`, `status = 'staged'`). | `test_webhook_jira_and_slack_idempotency`<br>`test_same_event_twice_per_provider[jira]` |
| **TC-DUP-JIRA-002** | **Jira** | **Same ID in different tenants (both stored)** | 1. Tenant A sends Jira webhook with `X-Atlassian-Webhook-Identifier: jira-shared-ten-001`.<br>2. Tenant B sends Jira webhook with the **same** `X-Atlassian-Webhook-Identifier: jira-shared-ten-001`.<br>3. Tenant A & Tenant B each send a redelivery of `jira-shared-ten-001`. | • **1st (Tenant A):** `202` (`duplicate: false`, `event_id = EA`)<br>• **2nd (Tenant B):** `202` (`duplicate: false`, `event_id = EB`, `EB != EA`)<br>• **3rd (Re-send A & B):** `202` (`duplicate: true`) | **`2 rows total`** (**`1 row`** in Tenant A + **`1 row`** in Tenant B; **both stored**). Under RLS, Tenant A queries return **`1 row`** and Tenant B queries return **`1 row`**. | `test_same_delivery_id_different_tenants_both_stored_per_provider[jira]` |
| **TC-DUP-JIRA-003** | **Jira** | **Same ID in different providers (both stored)** | Tenant A sends delivery ID `shared-prov-id-002` to **Jira** (`X-Atlassian-Webhook-Identifier`) and then sends the **same** ID `shared-prov-id-002` to **GitHub** and **Slack**. | • **Jira:** `202` (`duplicate: false`, `event_id = E_JIRA`)<br>• **GitHub:** `202` (`duplicate: false`, `event_id = E_GH`)<br>• **Slack:** `202` (`duplicate: false`, `event_id = E_SLACK`) | **`2 rows`** for Jira + GitHub (or Jira + Slack); **`3 rows total`** across Jira + GitHub + Slack (**`1 row` per provider**, all stored). | `test_same_delivery_id_different_providers_both_stored` |
| **TC-DUP-JIRA-004** | **Jira** | **Missing delivery ID** *(Payload fallback & SHA-256 digest)* | **Sub-case A (No ID anywhere):** Tenant A sends Jira payload `P1` (without `X-Atlassian-Webhook-Identifier`, `webhookEvent`/`timestamp`, or `eventId`) twice, then sends distinct payload `P2`.<br>**Sub-case B (Header omitted, payload `webhookEvent` + `timestamp` present):** Extracts `jira-{webhookEvent}-{timestamp}`, sent twice. | • **P1 (1st):** `202` (`duplicate: false`, `delivery_id = 'gen-jira-<hash1>'`)<br>• **P1 (2nd):** `202` (`duplicate: true`, `delivery_id = 'gen-jira-<hash1>'`)<br>• **P2 (1st):** `202` (`duplicate: false`, `delivery_id = 'gen-jira-<hash2>'`) | • After P1 1st delivery: **`1 row`**<br>• After P1 duplicate redelivery: **`1 row`** (duplicate suppressed)<br>• After distinct P2 delivery: **`2 rows`**<br>• Sub-case B after redelivery: **`1 row`**. | `test_missing_delivery_id_scenarios_per_provider[jira]`<br>`test_extract_delivery_id_jira` |
| **TC-DUP-SLACK-001** | **Slack** | **Same event twice** | Tenant A sends `POST /api/v1/webhooks/slack` twice with payload `{"event_id": "Ev024BE92L", "type": "event_callback", ...}`. | • **1st:** `202 Accepted` (`duplicate: false`, `event_id = E1`)<br>• **2nd:** `202 Accepted` (`duplicate: true`, `event_id = E1`) | **`1 row`** in Tenant A (`provider = 'slack'`, `provider_delivery_id = 'Ev024BE92L'`, `status = 'staged'`). | `test_webhook_jira_and_slack_idempotency`<br>`test_same_event_twice_per_provider[slack]` |
| **TC-DUP-SLACK-002** | **Slack** | **Same ID in different tenants (both stored)** | 1. Tenant A sends Slack webhook with `event_id = "EvSharedTen001"`.<br>2. Tenant B sends Slack webhook with the **same** `event_id = "EvSharedTen001"`.<br>3. Tenant A & Tenant B each send a redelivery of `EvSharedTen001`. | • **1st (Tenant A):** `202` (`duplicate: false`, `event_id = EA`)<br>• **2nd (Tenant B):** `202` (`duplicate: false`, `event_id = EB`, `EB != EA`)<br>• **3rd (Re-send A & B):** `202` (`duplicate: true`) | **`2 rows total`** (**`1 row`** in Tenant A + **`1 row`** in Tenant B; **both stored**). Under RLS, Tenant A queries return **`1 row`** and Tenant B queries return **`1 row`**. | `test_same_delivery_id_different_tenants_both_stored_per_provider[slack]` |
| **TC-DUP-SLACK-003** | **Slack** | **Same ID in different providers (both stored)** | Tenant A sends delivery ID `shared-prov-id-003` to **Slack** (`event_id`) and then sends the **same** ID `shared-prov-id-003` to **GitHub** and **Jira**. | • **Slack:** `202` (`duplicate: false`, `event_id = E_SLACK`)<br>• **GitHub:** `202` (`duplicate: false`, `event_id = E_GH`)<br>• **Jira:** `202` (`duplicate: false`, `event_id = E_JIRA`) | **`2 rows`** for Slack + GitHub (or Slack + Jira); **`3 rows total`** across Slack + GitHub + Jira (**`1 row` per provider**, all stored). | `test_same_delivery_id_different_providers_both_stored` |
| **TC-DUP-SLACK-004** | **Slack** | **Missing delivery ID** *(Header fallback & SHA-256 digest)* | **Sub-case A (No `event_id` in payload, no timestamp header):** Tenant A sends Slack payload `P1` twice, then sends distinct payload `P2`.<br>**Sub-case B (Missing `event_id`, `X-Slack-Request-Timestamp` present):** Extracts `slack-{ts}-r{retry}`, sent twice. | • **P1 (1st):** `202` (`duplicate: false`, `delivery_id = 'gen-slack-<hash1>'`)<br>• **P1 (2nd):** `202` (`duplicate: true`, `delivery_id = 'gen-slack-<hash1>'`)<br>• **P2 (1st):** `202` (`duplicate: false`, `delivery_id = 'gen-slack-<hash2>'`) | • After P1 1st delivery: **`1 row`**<br>• After P1 duplicate redelivery: **`1 row`** (duplicate suppressed)<br>• After distinct P2 delivery: **`2 rows`**<br>• Sub-case B after redelivery: **`1 row`**. | `test_missing_delivery_id_scenarios_per_provider[slack]`<br>`test_extract_delivery_id_slack` |

---

### 3.2 Cross-Tenant & Cross-Provider Combined Matrix

| Scenario ID | Scenario Name | Providers Involved | Tenants Involved | Actions Executed | Expected Row Counts | Automated Pytest Mapping |
| :--- | :--- | :---: | :---: | :--- | :--- | :--- |
| **TC-DUP-XTEN-001** | **Cross-Tenant Same ID via Provider & Integration Endpoints** | `github`, `jira`, `slack` | Tenant A & Tenant B | Deliver identical `delivery_id` per provider to Tenant A (Integration A) and Tenant B (Integration B), followed by duplicate redelivery to each integration. | • **Per provider in Tenant A:** **`1 row`**<br>• **Per provider in Tenant B:** **`1 row`**<br>• **Per provider across both tenants:** **`2 rows`**<br>• **Across all 3 providers & 2 tenants:** **`6 rows`** (`3` in Tenant A + `3` in Tenant B). | `test_same_delivery_id_different_tenants_both_stored_per_provider`<br>`test_cross_tenant_integration_bound_same_delivery_id_both_stored` |
| **TC-DUP-XPROV-001** | **Cross-Provider Pairwise & Tri-Provider Same ID** | `github` $\leftrightarrow$ `jira` $\leftrightarrow$ `slack` | Tenant A | 1. Send `shared-gh-jira` to `github` and `jira`.<br>2. Send `shared-gh-slack` to `github` and `slack`.<br>3. Send `shared-jira-slack` to `jira` and `slack`.<br>4. Send `shared-all-three` to `github`, `jira`, and `slack`, then re-send to all 3. | • Pair 1 (`github` + `jira`): **`2 rows`** (`1` per provider)<br>• Pair 2 (`github` + `slack`): **`2 rows`** (`1` per provider)<br>• Pair 3 (`jira` + `slack`): **`2 rows`** (`1` per provider)<br>• Tri-provider (`github` + `jira` + `slack`): **`3 rows`** (`1` per provider; remains **`3 rows`** after redelivery). | `test_same_delivery_id_different_providers_both_stored` |
| **TC-DUP-MISSING-X-001** | **Missing Delivery ID Across Tenants & Providers** | `github`, `jira`, `slack` | Tenant A & Tenant B | Send identical raw JSON body `{"action": "sync"}` with **no** delivery ID headers/fields to `github`, `jira`, and `slack` in both Tenant A and Tenant B, then repeat all requests. | • Deterministic IDs include provider prefix (`gen-github-<hash>`, `gen-jira-<hash>`, `gen-slack-<hash>`).<br>• **Tenant A:** **`3 rows`** (`1` per provider).<br>• **Tenant B:** **`3 rows`** (`1` per provider).<br>• **Total across both tenants:** **`6 rows`** (remains **`6 rows`** after duplicate redeliveries). | `test_missing_delivery_id_cross_provider_and_cross_tenant` |

---

## 4. Detailed Scenario Specifications Per Provider

### 4.1 GitHub Duplicate-Delivery Scenarios (`provider = 'github'`)

#### Scenario `TC-DUP-GH-001`: GitHub — Same Event Twice
- **Preconditions:** Tenant A (`11111111-1111-1111-1111-111111111111`) has `0` rows in `webhook_events` for `provider_delivery_id = '72d3162e-cc78-11e3-81ab-4c9367dc0958'`.
- **Actions:**
  1. Send `POST /api/v1/webhooks/github` (or `POST /api/v1/webhooks/integrations/{gh_integration_a}`) with headers:
     - `X-GitHub-Delivery: 72d3162e-cc78-11e3-81ab-4c9367dc0958`
     - `X-GitHub-Event: push`
     - `X-Tenant-ID: 11111111-1111-1111-1111-111111111111`
     and JSON payload `{"ref": "refs/heads/main", "repository": {"full_name": "acme/backend"}}`.
  2. Immediately send the exact same HTTP `POST` request a second time (simulating a GitHub webhook retry).
- **Expected HTTP Outcome:**
  - **1st delivery:** `HTTP 202 Accepted` $\rightarrow$ `{"status": "accepted", "duplicate": false, "provider": "github", "delivery_id": "72d3162e-cc78-11e3-81ab-4c9367dc0958", "event_id": "<uuid_1>", "message": "Webhook staged successfully"}`.
  - **2nd delivery:** `HTTP 202 Accepted` $\rightarrow$ `{"status": "accepted", "duplicate": true, "provider": "github", "delivery_id": "72d3162e-cc78-11e3-81ab-4c9367dc0958", "event_id": "<uuid_1>", "message": "Duplicate webhook ignored (already staged)"}`.
- **Expected Database Row Count (`webhook_events`):**
  ```sql
  SELECT COUNT(*) FROM webhook_events
  WHERE tenant_id = '11111111-1111-1111-1111-111111111111'
    AND provider = 'github'
    AND provider_delivery_id = '72d3162e-cc78-11e3-81ab-4c9367dc0958';
  ```
  - After 1st delivery: **`1 row`** (`status = 'staged'`, `attempts = 0`).
  - After 2nd delivery: **`1 row`** (unchanged).

#### Scenario `TC-DUP-GH-002`: GitHub — Same Delivery ID in Different Tenants (Both Stored)
- **Preconditions:** Tenant A (`11111111-...`) and Tenant B (`22222222-...`) are provisioned.
- **Actions:**
  1. Send `POST /api/v1/webhooks/github` with `X-GitHub-Delivery: gh-shared-tenant-del-100` and `X-Tenant-ID: 11111111-1111-1111-1111-111111111111` (Tenant A).
  2. Send `POST /api/v1/webhooks/github` with `X-GitHub-Delivery: gh-shared-tenant-del-100` and `X-Tenant-ID: 22222222-2222-2222-2222-222222222222` (Tenant B).
  3. Re-send the request for Tenant A and re-send the request for Tenant B.
- **Expected HTTP Outcome:**
  - **Step 1 (Tenant A 1st):** `HTTP 202 Accepted`, `duplicate: false`, `event_id: <uuid_A>`.
  - **Step 2 (Tenant B 1st):** `HTTP 202 Accepted`, `duplicate: false`, `event_id: <uuid_B>` (`<uuid_B> != <uuid_A>`).
  - **Step 3 (Tenant A 2nd & Tenant B 2nd):** Both return `HTTP 202 Accepted`, `duplicate: true`, preserving `<uuid_A>` and `<uuid_B>` respectively.
- **Expected Database Row Count (`webhook_events`):**
  - Under Tenant A RLS context (`app.current_tenant_id = '11111111-...'`): **`1 row`** (`id = <uuid_A>`, `tenant_id = '11111111-...'`).
  - Under Tenant B RLS context (`app.current_tenant_id = '22222222-...'`): **`1 row`** (`id = <uuid_B>`, `tenant_id = '22222222-...'`).
  - Total rows stored across both tenants for `provider = 'github' AND provider_delivery_id = 'gh-shared-tenant-del-100'`: **`2 rows` (both stored)**.

#### Scenario `TC-DUP-GH-003`: GitHub — Same Delivery ID in Different Providers (Both Stored)
- **Actions:**
  1. Under Tenant A, send `POST /api/v1/webhooks/github` with `X-GitHub-Delivery: shared-id-900`.
  2. Under Tenant A, send `POST /api/v1/webhooks/jira` with `X-Atlassian-Webhook-Identifier: shared-id-900`.
  3. Under Tenant A, send `POST /api/v1/webhooks/slack` with payload `{"event_id": "shared-id-900", "type": "event_callback"}`.
- **Expected Database Row Count (`webhook_events`):**
  - After GitHub + Jira: **`2 rows`** (**`1 row`** for `github`, **`1 row`** for `jira`).
  - After Slack: **`3 rows`** (**`1 row`** for `github`, **`1 row`** for `jira`, **`1 row`** for `slack`), all with `duplicate = false`.

#### Scenario `TC-DUP-GH-004`: GitHub — Missing Delivery ID
- **Actions:**
  1. **Missing header, no payload ID (Deterministic SHA-256 fallback):**
     - Send `POST /api/v1/webhooks/github` without `X-GitHub-Delivery` and body `{"ref": "refs/heads/main", "commits": [{"id": "c1"}]}`.
     - Send the exact same request a second time without `X-GitHub-Delivery`.
     - Send a different body `{"ref": "refs/heads/dev", "commits": [{"id": "c2"}]}` without `X-GitHub-Delivery`.
  2. **Missing header, payload `id` + `repository` fallback (`gh-<id>`):**
     - Send `POST /api/v1/webhooks/github` without `X-GitHub-Delivery` and body `{"id": 884422, "repository": {"full_name": "acme/repo"}}` twice.
- **Expected Outcome & Row Counts:**
  - Sub-case 1 (1st delivery of body 1): `HTTP 202`, `duplicate: false`, `delivery_id = "gen-github-<24-hex-1>"` $\rightarrow$ **`1 row`**.
  - Sub-case 1 (2nd delivery of body 1): `HTTP 202`, `duplicate: true`, `delivery_id = "gen-github-<24-hex-1>"` $\rightarrow$ **`1 row`** (duplicate prevented despite missing header).
  - Sub-case 1 (1st delivery of body 2): `HTTP 202`, `duplicate: false`, `delivery_id = "gen-github-<24-hex-2>"` $\rightarrow$ **`2 rows`** total across the two distinct payloads.
  - Sub-case 2 (payload fallback `gh-884422`): 1st delivery stores **`1 row`** (`duplicate: false`); 2nd delivery keeps **`1 row`** (`duplicate: true`).

---

### 4.2 Jira Duplicate-Delivery Scenarios (`provider = 'jira'`)

#### Scenario `TC-DUP-JIRA-001`: Jira — Same Event Twice
- **Actions:**
  1. Send `POST /api/v1/webhooks/jira` with header `X-Atlassian-Webhook-Identifier: jira-wh-id-5501`, `X-Tenant-ID: 11111111-1111-1111-1111-111111111111`, and payload `{"webhookEvent": "jira:issue_updated", "issue": {"key": "SCRUM-336"}}`.
  2. Send the identical request a second time.
- **Expected Outcome & Row Counts:**
  - **1st delivery:** `HTTP 202 Accepted` (`duplicate: false`, `delivery_id: "jira-wh-id-5501"`, `event_id: <uuid_1>`) $\rightarrow$ **`1 row`** in `webhook_events`.
  - **2nd delivery:** `HTTP 202 Accepted` (`duplicate: true`, `delivery_id: "jira-wh-id-5501"`, `event_id: <uuid_1>`) $\rightarrow$ **`1 row`** in `webhook_events`.

#### Scenario `TC-DUP-JIRA-002`: Jira — Same Delivery ID in Different Tenants (Both Stored)
- **Actions:**
  1. Tenant A sends `POST /api/v1/webhooks/jira` with `X-Atlassian-Webhook-Identifier: jira-shared-tenant-del-200`.
  2. Tenant B sends `POST /api/v1/webhooks/jira` with the **same** `X-Atlassian-Webhook-Identifier: jira-shared-tenant-del-200`.
  3. Both tenants redeliver `jira-shared-tenant-del-200`.
- **Expected Outcome & Row Counts:**
  - Tenant A 1st delivery: `HTTP 202` (`duplicate: false`, `event_id: <uuid_A>`) $\rightarrow$ **`1 row`** in Tenant A.
  - Tenant B 1st delivery: `HTTP 202` (`duplicate: false`, `event_id: <uuid_B>`) $\rightarrow$ **`1 row`** in Tenant B.
  - Combined row count across Tenant A and Tenant B: **`2 rows` (both stored)**.
  - Redeliveries in Tenant A and Tenant B return `duplicate: true` and keep counts at **`1 row`** per tenant (**`2 rows`** total).

#### Scenario `TC-DUP-JIRA-003`: Jira — Same Delivery ID in Different Providers (Both Stored)
- **Actions:**
  1. Tenant A sends `X-Atlassian-Webhook-Identifier: cross-prov-jira-300` to `POST /api/v1/webhooks/jira`.
  2. Tenant A sends `X-GitHub-Delivery: cross-prov-jira-300` to `POST /api/v1/webhooks/github`.
  3. Tenant A sends `{"event_id": "cross-prov-jira-300"}` to `POST /api/v1/webhooks/slack`.
- **Expected Outcome & Row Counts:**
  - Pairwise (`jira` + `github` or `jira` + `slack`): **`2 rows` (both stored)**.
  - All three providers (`jira` + `github` + `slack`): **`3 rows`** (**`1 row`** per provider), all returning `HTTP 202` with `duplicate: false`.

#### Scenario `TC-DUP-JIRA-004`: Jira — Missing Delivery ID
- **Actions:**
  1. **Missing header, no payload event/timestamp (Deterministic SHA-256 fallback):**
     - Send `POST /api/v1/webhooks/jira` with no `X-Atlassian-Webhook-Identifier` header and body `{"issue": {"key": "SCRUM-336", "fields": {"summary": "No ID"}}}` twice, then send a distinct body `{"issue": {"key": "SCRUM-337", "fields": {"summary": "Second"}}}`.
  2. **Missing header, payload `webhookEvent` + `timestamp` fallback (`jira-{webhookEvent}-{timestamp}`):**
     - Send `POST /api/v1/webhooks/jira` with no header and body `{"webhookEvent": "jira:issue_created", "timestamp": 1730305000, "issue": {"key": "SCRUM-336"}}` twice.
- **Expected Outcome & Row Counts:**
  - Sub-case 1 (1st delivery of body 1): `HTTP 202`, `duplicate: false`, `delivery_id = "gen-jira-<24-hex-1>"` $\rightarrow$ **`1 row`**.
  - Sub-case 1 (2nd delivery of body 1): `HTTP 202`, `duplicate: true`, `delivery_id = "gen-jira-<24-hex-1>"` $\rightarrow$ **`1 row`**.
  - Sub-case 1 (1st delivery of body 2): `HTTP 202`, `duplicate: false`, `delivery_id = "gen-jira-<24-hex-2>"` $\rightarrow$ **`2 rows`** total across the two distinct payloads.
  - Sub-case 2 (payload fallback `jira-jira:issue_created-1730305000`): 1st delivery stores **`1 row`** (`duplicate: false`); 2nd delivery keeps **`1 row`** (`duplicate: true`).

---

### 4.3 Slack Duplicate-Delivery Scenarios (`provider = 'slack'`)

#### Scenario `TC-DUP-SLACK-001`: Slack — Same Event Twice
- **Actions:**
  1. Send `POST /api/v1/webhooks/slack` with `X-Tenant-ID: 11111111-1111-1111-1111-111111111111` and JSON payload `{"type": "event_callback", "event_id": "Ev099DUP001", "event": {"type": "app_mention", "text": "status"}}`.
  2. Send the identical request a second time.
- **Expected Outcome & Row Counts:**
  - **1st delivery:** `HTTP 202 Accepted` (`duplicate: false`, `delivery_id: "Ev099DUP001"`, `event_id: <uuid_1>`) $\rightarrow$ **`1 row`** in `webhook_events`.
  - **2nd delivery:** `HTTP 202 Accepted` (`duplicate: true`, `delivery_id: "Ev099DUP001"`, `event_id: <uuid_1>`) $\rightarrow$ **`1 row`** in `webhook_events`.

#### Scenario `TC-DUP-SLACK-002`: Slack — Same Delivery ID in Different Tenants (Both Stored)
- **Actions:**
  1. Tenant A sends `POST /api/v1/webhooks/slack` with `{"type": "event_callback", "event_id": "EvSharedTenant400", "event": {"type": "message"}}`.
  2. Tenant B sends `POST /api/v1/webhooks/slack` with the **same** `{"type": "event_callback", "event_id": "EvSharedTenant400", "event": {"type": "message"}}`.
  3. Both tenants redeliver `EvSharedTenant400`.
- **Expected Outcome & Row Counts:**
  - Tenant A 1st delivery: `HTTP 202` (`duplicate: false`, `event_id: <uuid_A>`) $\rightarrow$ **`1 row`** in Tenant A.
  - Tenant B 1st delivery: `HTTP 202` (`duplicate: false`, `event_id: <uuid_B>`) $\rightarrow$ **`1 row`** in Tenant B.
  - Combined row count across Tenant A and Tenant B: **`2 rows` (both stored)**.
  - Redeliveries in Tenant A and Tenant B return `duplicate: true` and keep counts at **`1 row`** per tenant (**`2 rows`** total).

#### Scenario `TC-DUP-SLACK-003`: Slack — Same Delivery ID in Different Providers (Both Stored)
- **Actions:**
  1. Tenant A sends `{"event_id": "cross-prov-slack-500"}` to `POST /api/v1/webhooks/slack`.
  2. Tenant A sends `X-GitHub-Delivery: cross-prov-slack-500` to `POST /api/v1/webhooks/github`.
  3. Tenant A sends `X-Atlassian-Webhook-Identifier: cross-prov-slack-500` to `POST /api/v1/webhooks/jira`.
- **Expected Outcome & Row Counts:**
  - Pairwise (`slack` + `github` or `slack` + `jira`): **`2 rows` (both stored)**.
  - All three providers (`slack` + `github` + `jira`): **`3 rows`** (**`1 row`** per provider), all returning `HTTP 202` with `duplicate: false`.

#### Scenario `TC-DUP-SLACK-004`: Slack — Missing Delivery ID
- **Actions:**
  1. **Missing `event_id` in payload and no timestamp header (Deterministic SHA-256 fallback):**
     - Send `POST /api/v1/webhooks/slack` with no `event_id` field and no `X-Slack-Request-Timestamp` header, body `{"type": "event_callback", "event": {"type": "reaction_added", "reaction": "shipit"}}` twice, then send a distinct body `{"type": "event_callback", "event": {"type": "reaction_added", "reaction": "eyes"}}`.
  2. **Missing `event_id` in payload, `X-Slack-Request-Timestamp` + `X-Slack-Retry-Num` header fallback (`slack-{ts}-r{retry}`):**
     - Send `POST /api/v1/webhooks/slack` without `event_id`, with headers `X-Slack-Request-Timestamp: 1730309999` and `X-Slack-Retry-Num: 1`, twice.
- **Expected Outcome & Row Counts:**
  - Sub-case 1 (1st delivery of body 1): `HTTP 202`, `duplicate: false`, `delivery_id = "gen-slack-<24-hex-1>"` $\rightarrow$ **`1 row`**.
  - Sub-case 1 (2nd delivery of body 1): `HTTP 202`, `duplicate: true`, `delivery_id = "gen-slack-<24-hex-1>"` $\rightarrow$ **`1 row`**.
  - Sub-case 1 (1st delivery of body 2): `HTTP 202`, `duplicate: false`, `delivery_id = "gen-slack-<24-hex-2>"` $\rightarrow$ **`2 rows`** total across the two distinct payloads.
  - Sub-case 2 (header fallback `slack-1730309999-r1`): 1st delivery stores **`1 row`** (`duplicate: false`); 2nd delivery keeps **`1 row`** (`duplicate: true`).

---

### 4.4 Unique Constraint Guardrail & Regression Scenarios (`SCRUM-337`)

| Scenario ID | Scenario Name | Verification Target | Expected Outcome | Automated Pytest Mapping |
| :--- | :--- | :--- | :--- | :--- |
| **TC-DUP-INTEG-001** | **Integration-Bound Same Event Twice** | `POST /api/v1/webhooks/integrations/{integration_id}` (`github`, `jira`, `slack`) | Sending each signed event twice returns `HTTP 202 Accepted` both times (`duplicate: false` then `duplicate: true`) and stores **`1 row`** in `webhook_events`. | `test_send_each_integration_bound_event_twice_asserts_single_row_and_202_both_times` |
| **TC-DUP-CAT-001** | **Multi-Event Catalog Sent Twice** | 9 distinct GitHub, Jira, and Slack event types sent 2x each (`18` deliveries) | Every delivery returns `HTTP 202 Accepted`; each unique event stores **`1 row`** (**`9 rows`** total across the 9 distinct events). | `test_send_multiple_event_types_twice_asserts_single_row_per_event_and_202_both_times` |
| **TC-DUP-UQ-001** | **Unique Constraint Schema & Catalog Guardrail** | `WebhookEvent.__table__` & `pg_constraint` (`uq_webhook_provider_delivery`) | Verifies `uq_webhook_provider_delivery` is present on `(tenant_id, provider, provider_delivery_id)`. Removing the constraint from models or migrations fails the test. | `test_webhook_events_unique_constraint_schema_and_catalog_guardrail` |
| **TC-DUP-UQ-002** | **Removing Unique Constraint Makes Test Fail** | Live PostgreSQL `ALTER TABLE webhook_events DROP CONSTRAINT uq_webhook_provider_delivery` | Dropping `uq_webhook_provider_delivery` causes `ON CONFLICT (tenant_id, provider, provider_delivery_id) DO NOTHING` to fail (`DBAPIError`, breaking `HTTP 202`) and allows duplicate `INSERT`s to create **`2 rows`** (breaking `len(rows) == 1`). Restoring the constraint restores `HTTP 202` + **`1 row`**. | `test_removing_unique_constraint_makes_duplicate_delivery_test_fail` |

---

## 5. Running the Automated Verification Suite

### Locally Against PostgreSQL 16
```bash
# 1. Start PostgreSQL 16 service and apply migrations to head
docker compose -f backend/docker-compose.yml up -d --wait postgres
python backend/scripts/verify_migrations.py --step upgrade

# 2. Run the webhook duplicate-delivery & idempotency test suite
python -m pytest backend/tests/test_webhook_delivery_idempotency.py -v
```

### In GitHub Actions CI (`.github/workflows/ci.yml`)
Every pull request and push executes [`backend/tests/test_webhook_delivery_idempotency.py`](../backend/tests/test_webhook_delivery_idempotency.py) inside both the `backend-test` and `pgbouncer-integration` workflow jobs against PostgreSQL 16 under `eng_app_user` (`FORCE ROW LEVEL SECURITY`), gating merges via `ci-gate`.
