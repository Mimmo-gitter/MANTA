# Webhook Authentication & Replay Protection Test Scenarios (GitHub, Jira & Slack)

**Tasks:** Create test scenarios per provider (tampered body, wrong secret, replay) (`SCRUM-351`) & Write automated pytest tests for webhook authentication (`SCRUM-328`)  
**Parent User Story:** `SCRUM-250` (`US-14` Authenticated webhook intake)  
**Related Tickets:** `SCRUM-325` (GitHub & Jira HMAC-SHA256 signature verification), `SCRUM-326` (Slack HMAC-SHA256 signature & 5-minute replay window), `SCRUM-327` (Integration-bound tenant resolution & unknown integration rejection), `SCRUM-328` (Automated pytest suite), `SCRUM-331` (PostgreSQL outage resilience), `SCRUM-335` (Idempotent event staging)  
**Target Database Tables:** `integrations` (`webhook_secret_hash` encrypted via AES-256-GCM), `webhook_events` (`FORCE ROW LEVEL SECURITY`)  
**Implementation Modules:** [`backend/app/webhooks/verification.py`](../backend/app/webhooks/verification.py), [`backend/app/webhooks/router.py`](../backend/app/webhooks/router.py), [`backend/app/integrations/crypto.py`](../backend/app/integrations/crypto.py)  
**Automated Pytest Suite:** [`backend/tests/test_webhook_authentication.py`](../backend/tests/test_webhook_authentication.py)

---

## 1. Objective & Security Invariants

Before any incoming webhook is staged into `webhook_events`, the MANTA platform authenticates the sender, verifies payload integrity, prevents replay attacks, and resolves tenant ownership strictly from the target integration record.

This document specifies the positive baseline and authentication failure test scenarios across **GitHub**, **Jira**, and **Slack** (`valid`, `tampered body`, `wrong secret`, `missing signature`, `replayed request (Slack)`, and `unknown integration ID`) along with their expected HTTP status codes, response headers/details, and database persistence guarantees:

1. **Provider-Specific Cryptographic Signature Verification ([`backend/app/webhooks/verification.py`](../backend/app/webhooks/verification.py)):**
   - **GitHub (`provider = 'github'`, [`verify_github_signature`](../backend/app/webhooks/verification.py#L40-L80)):**
     - Computes `HMAC-SHA256(secret, raw_body)` over the exact unparsed HTTP request bytes before JSON decoding.
     - Validates against `X-Hub-Signature-256: sha256=<hex>` (with fallback support for legacy `X-Hub-Signature`).
   - **Jira (`provider = 'jira'`, [`verify_jira_signature`](../backend/app/webhooks/verification.py#L83-L122)):**
     - Computes `HMAC-SHA256(secret, raw_body)` over the exact unparsed HTTP request bytes.
     - Validates against `X-Hub-Signature-256: sha256=<hex>` or `X-Atlassian-Webhook-Signature` (or fallback `X-Hub-Signature`).
   - **Slack (`provider = 'slack'`, [`verify_slack_signature`](../backend/app/webhooks/verification.py#L125-L186)):**
     - Validates `X-Slack-Request-Timestamp` and enforces a strict **5-minute (`300` seconds) replay window**:
       $$\left|\text{now} - \text{timestamp}\right| \le 300\text{ s}$$
     - Constructs the canonical Slack basestring `v0:{X-Slack-Request-Timestamp}:{raw_body}` and computes `v0=` + `HMAC-SHA256(secret, basestring)`.
     - Validates against `X-Slack-Signature: v0=<hex>`.

2. **Constant-Time Digest Comparison:**
   All signature checks use Python's constant-time `hmac.compare_digest` to prevent byte-by-byte timing side-channel attacks.

3. **Strict Integration-Bound Tenant Resolution ([`ingest_webhook_for_integration`](../backend/app/webhooks/router.py#L280-L369)):**
   - `POST /api/v1/webhooks/integrations/{integration_id}` resolves `tenant_id`, `provider`, `webhook_secret_hash`, and `is_active` strictly from `{integration_id}` via `get_webhook_integration_route(:id)`.
   - Unknown or inactive (`is_active = false`) integration UUIDs are immediately rejected with **`HTTP 404 Not Found`** (`{"detail": "Integration not found or inactive"}`) without leaking tenant metadata.
   - Any client-supplied `X-Tenant-ID` header on the integration endpoint is ignored for routing so an attacker cannot redirect a webhook into another tenant's partition.

4. **Zero-Persistence on Authentication or Routing Failure:**
   Every rejected webhook (`HTTP 401 Unauthorized` or `HTTP 404 Not Found`) aborts before `stage_webhook_event_idempotent`, guaranteeing **`0 rows`** are written to `webhook_events`.

---

## 2. Canonical Test Fixtures & Endpoints

Scenarios are verified against both webhook intake routes:
- **Integration-bound intake (Primary):** `POST /api/v1/webhooks/integrations/{integration_id}` (decrypts AES-256-GCM secret stored on the integration row).
- **Provider-level intake:** `POST /api/v1/webhooks/{provider}` (verifies signature when `X-Webhook-Secret` is supplied).

| Fixture Entity | Tenant Ownership | Example Identifier | Provider | Secret / Configuration |
| :--- | :--- | :--- | :---: | :--- |
| **Tenant A** (`tenants`) | Tenant A | `11111111-1111-1111-1111-111111111111` | — | Primary tenant partition under RLS |
| **Tenant B** (`tenants`) | Tenant B | `22222222-2222-2222-2222-222222222222` | — | Secondary tenant partition for cross-tenant secret isolation |
| **GitHub Integration A** | Tenant A | `aaaa1111-1111-1111-1111-111111111111` | `github` | `is_active = true`, AES-256-GCM encrypted `gh_secret_token_abcdef123456` |
| **Jira Integration A** | Tenant A | `aaaa2222-2222-2222-2222-222222222222` | `jira` | `is_active = true`, AES-256-GCM encrypted `jira_secret_xyz_789` |
| **Slack Integration A** | Tenant A | `aaaa3333-3333-3333-3333-333333333333` | `slack` | `is_active = true`, AES-256-GCM encrypted `slack_signing_secret_998877` |
| **Inactive Integration** | Tenant A | `aaaa0000-0000-0000-0000-000000000000` | `github` / `jira` / `slack` | `is_active = false`, valid encrypted secret |
| **Unknown Integration ID** | *(None)* | `99999999-9999-4999-8999-999999999999` | `github` / `jira` / `slack` | Unregistered UUID not present in `integrations` |

---

## 3. Master Test Scenarios Matrix Per Provider (With Expected Status Codes)

### 3.1 GitHub (`provider = 'github'`) Scenarios

| Scenario ID | Scenario Case | Request Construction (Headers, Payload & Secret) | Expected Status Code | Expected Response Body & Headers | Expected `webhook_events` Rows | Automated Pytest Mapping (`backend/tests/test_webhook_authentication.py`) |
| :--- | :--- | :--- | :---: | :--- | :---: | :--- |
| **TC-WHAUTH-GH-001** | **Valid** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{gh_integration_id}`<br>• **Body:** Raw JSON bytes `{"ref":"refs/heads/main","commits":[{"id":"c123"}]}`<br>• **Headers:** `X-GitHub-Delivery: <uuid>`, `X-GitHub-Event: push`, `X-Hub-Signature-256: sha256=<HMAC_SHA256(gh_secret, raw_body)>` (also supports legacy `X-Hub-Signature` and pretty-printed/Unicode raw bytes). | **`202 Accepted`** | `{"status": "accepted", "duplicate": false, "provider": "github", "delivery_id": "<uuid>", "event_id": "<uuid>", "message": "Webhook staged successfully"}` | **`1 row`** (`status = 'staged'`) | `test_github_webhook_signature_verification_lifecycle`<br>`test_github_webhook_header_variants_and_unparsed_raw_bytes`<br>`test_provider_scenario_matrix_status_codes[github]` |
| **TC-WHAUTH-GH-002** | **Tampered Body** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{gh_integration_id}`<br>• **Signature:** Computed over original payload `{"commits":[{"id":"c123"}]}` using valid `gh_secret`.<br>• **Body Sent:** Modified in transit to `{"commits":[{"id":"TAMPERED"}]}` (or re-serialized with altered whitespace) without updating `X-Hub-Signature-256`. | **`401 Unauthorized`** | • **Body:** `{"detail": "Invalid GitHub webhook signature"}`<br>• **Header:** `WWW-Authenticate: WebhookSignature` | **`0 rows`** | `test_github_webhook_signature_verification_lifecycle`<br>`test_github_webhook_header_variants_and_unparsed_raw_bytes`<br>`test_provider_scenario_matrix_status_codes[github]` |
| **TC-WHAUTH-GH-003** | **Wrong Secret** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{gh_integration_id}`<br>• **Body:** Unmodified raw JSON bytes.<br>• **Signature:** `X-Hub-Signature-256: sha256=<HMAC_SHA256("wrong_gh_secret_999", raw_body)>` (or signed with another tenant's secret). | **`401 Unauthorized`** | • **Body:** `{"detail": "Invalid GitHub webhook signature"}`<br>• **Header:** `WWW-Authenticate: WebhookSignature` | **`0 rows`** | `test_github_webhook_signature_verification_lifecycle`<br>`test_cross_tenant_secret_isolation_and_spoofed_tenant_header_ignored`<br>`test_provider_scenario_matrix_status_codes[github]` |
| **TC-WHAUTH-GH-004** | **Missing Signature** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{gh_integration_id}`<br>• **Body:** Valid JSON payload.<br>• **Headers:** `X-Hub-Signature-256` and `X-Hub-Signature` omitted (or set to empty/whitespace string `"   "`). | **`401 Unauthorized`** | • **Body:** `{"detail": "Missing GitHub webhook signature header"}`<br>• **Header:** `WWW-Authenticate: WebhookSignature` | **`0 rows`** | `test_github_webhook_signature_verification_lifecycle`<br>`test_github_webhook_header_variants_and_unparsed_raw_bytes`<br>`test_provider_scenario_matrix_status_codes[github]` |
| **TC-WHAUTH-GH-005** | **Unknown Integration ID** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{unknown_or_inactive_uuid}`<br>• **Body & Headers:** Validly signed GitHub payload (`X-Hub-Signature-256: sha256=<hex>`, `X-GitHub-Delivery: <uuid>`) sent to a non-existent UUID (or inactive integration `is_active = false`). | **`404 Not Found`** | • **Body:** `{"detail": "Integration not found or inactive"}` | **`0 rows`** | `test_unknown_and_inactive_integration_rejection`<br>`test_provider_scenario_matrix_status_codes[github]` |

---

### 3.2 Jira (`provider = 'jira'`) Scenarios

| Scenario ID | Scenario Case | Request Construction (Headers, Payload & Secret) | Expected Status Code | Expected Response Body & Headers | Expected `webhook_events` Rows | Automated Pytest Mapping (`backend/tests/test_webhook_authentication.py`) |
| :--- | :--- | :--- | :---: | :--- | :---: | :--- |
| **TC-WHAUTH-JIRA-001** | **Valid** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{jira_integration_id}`<br>• **Body:** Raw JSON bytes `{"webhookEvent":"jira:issue_created","timestamp":1730300000}`<br>• **Headers:** `X-Atlassian-Webhook-Identifier: <id>`, `X-Hub-Signature-256: sha256=<HMAC_SHA256(jira_secret, raw_body)>` (or `X-Atlassian-Webhook-Signature`). | **`202 Accepted`** | `{"status": "accepted", "duplicate": false, "provider": "jira", "delivery_id": "<id>", "event_id": "<uuid>", "message": "Webhook staged successfully"}` | **`1 row`** (`status = 'staged'`) | `test_jira_webhook_signature_verification`<br>`test_provider_scenario_matrix_status_codes[jira]` |
| **TC-WHAUTH-JIRA-002** | **Tampered Body** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{jira_integration_id}`<br>• **Signature:** Computed over `{"webhookEvent":"jira:issue_created",...}` using valid `jira_secret`.<br>• **Body Sent:** Modified in transit to `{"webhookEvent":"jira:issue_deleted",...}` with original signature header. | **`401 Unauthorized`** | • **Body:** `{"detail": "Invalid Jira webhook signature"}`<br>• **Header:** `WWW-Authenticate: WebhookSignature` | **`0 rows`** | `test_jira_webhook_signature_verification`<br>`test_provider_scenario_matrix_status_codes[jira]` |
| **TC-WHAUTH-JIRA-003** | **Wrong Secret** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{jira_integration_id}`<br>• **Body:** Unmodified raw JSON bytes.<br>• **Signature:** `X-Hub-Signature-256: sha256=<HMAC_SHA256("wrong_jira_secret_000", raw_body)>` (or signed with another tenant's secret). | **`401 Unauthorized`** | • **Body:** `{"detail": "Invalid Jira webhook signature"}`<br>• **Header:** `WWW-Authenticate: WebhookSignature` | **`0 rows`** | `test_jira_webhook_signature_verification`<br>`test_provider_scenario_matrix_status_codes[jira]` |
| **TC-WHAUTH-JIRA-004** | **Missing Signature** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{jira_integration_id}`<br>• **Body:** Valid Jira JSON payload.<br>• **Headers:** `X-Hub-Signature-256`, `X-Atlassian-Webhook-Signature`, and `X-Hub-Signature` all omitted (or empty/whitespace). | **`401 Unauthorized`** | • **Body:** `{"detail": "Missing Jira webhook signature header"}`<br>• **Header:** `WWW-Authenticate: WebhookSignature` | **`0 rows`** | `test_jira_webhook_signature_verification`<br>`test_provider_scenario_matrix_status_codes[jira]` |
| **TC-WHAUTH-JIRA-005** | **Unknown Integration ID** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{unknown_or_inactive_uuid}`<br>• **Body & Headers:** Validly signed Jira payload (`X-Hub-Signature-256` / `X-Atlassian-Webhook-Signature`) sent to a non-existent UUID (or inactive Jira integration `is_active = false`). | **`404 Not Found`** | • **Body:** `{"detail": "Integration not found or inactive"}` | **`0 rows`** | `test_unknown_and_inactive_integration_rejection`<br>`test_provider_scenario_matrix_status_codes[jira]` |

---

### 3.3 Slack (`provider = 'slack'`) Scenarios (Including Replay Protection)

| Scenario ID | Scenario Case | Request Construction (Headers, Payload, Timestamp & Secret) | Expected Status Code | Expected Response Body & Headers | Expected `webhook_events` Rows | Automated Pytest Mapping (`backend/tests/test_webhook_authentication.py`) |
| :--- | :--- | :--- | :---: | :--- | :---: | :--- |
| **TC-WHAUTH-SLACK-001** | **Valid** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{slack_integration_id}`<br>• **Body:** Raw JSON bytes `{"event_id":"Ev01","event":{"text":"hello"}}`<br>• **Headers:** `X-Slack-Request-Timestamp: <now>` ($\left|\text{now} - \text{ts}\right| \le 300\text{s}$), `X-Slack-Signature: v0=<HMAC_SHA256(slack_secret, "v0:{ts}:{raw_body}")>`. | **`202 Accepted`** | `{"status": "accepted", "duplicate": false, "provider": "slack", "delivery_id": "Ev01", "event_id": "<uuid>", "message": "Webhook staged successfully"}` | **`1 row`** (`status = 'staged'`) | `test_slack_signature_and_replay_window_enforcement`<br>`test_slack_replay_window_exact_300_second_boundary`<br>`test_provider_scenario_matrix_status_codes[slack]` |
| **TC-WHAUTH-SLACK-002** | **Tampered Body** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{slack_integration_id}`<br>• **Signature & Timestamp:** Fresh `X-Slack-Request-Timestamp: <now>` and valid `X-Slack-Signature` computed for `"hello"`.<br>• **Body Sent:** Modified in transit to `{"event_id":"Ev01","event":{"text":"tampered"}}`. | **`401 Unauthorized`** | • **Body:** `{"detail": "Invalid Slack webhook signature"}`<br>• **Header:** `WWW-Authenticate: WebhookSignature` | **`0 rows`** | `test_slack_signature_and_replay_window_enforcement`<br>`test_provider_scenario_matrix_status_codes[slack]` |
| **TC-WHAUTH-SLACK-003** | **Wrong Secret** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{slack_integration_id}`<br>• **Body & Timestamp:** Unmodified raw body with fresh `X-Slack-Request-Timestamp: <now>`.<br>• **Signature:** Computed over `"v0:{ts}:{raw_body}"` using `"wrong_slack_secret_000"` (or unsupported version prefix `v1=<hex>`). | **`401 Unauthorized`** | • **Body:** `{"detail": "Invalid Slack webhook signature"}`<br>• **Header:** `WWW-Authenticate: WebhookSignature` | **`0 rows`** | `test_slack_signature_and_replay_window_enforcement`<br>`test_provider_scenario_matrix_status_codes[slack]` |
| **TC-WHAUTH-SLACK-004** | **Missing Signature** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{slack_integration_id}`<br>• **Sub-case 4a:** `X-Slack-Signature` omitted (only `X-Slack-Request-Timestamp` present).<br>• **Sub-case 4b:** `X-Slack-Request-Timestamp` omitted (only `X-Slack-Signature` present) or both omitted.<br>• **Sub-case 4c:** Malformed non-numeric `X-Slack-Request-Timestamp: not-a-unix-timestamp`. | **`401 Unauthorized`** | • **Body (4a/4b):** `{"detail": "Missing Slack signature or timestamp header"}`<br>• **Body (4c):** `{"detail": "Invalid Slack timestamp format"}`<br>• **Header:** `WWW-Authenticate: WebhookSignature` | **`0 rows`** | `test_slack_signature_and_replay_window_enforcement`<br>`test_provider_scenario_matrix_status_codes[slack]` |
| **TC-WHAUTH-SLACK-005** | **Replayed Request (Slack)** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{slack_integration_id}`<br>• **Sub-case 5a (Stale Replay $> 300\text{s}$):** Validly signed request with `X-Slack-Request-Timestamp = now - 305` (or `now - 301`).<br>• **Sub-case 5b (Future Timestamp $> +300\text{s}$):** Validly signed request with `X-Slack-Request-Timestamp = now + 310` (or `now + 301`).<br>• **Sub-case 5c (Timestamp-Swap Replay):** Attacker intercepts an expired request (`old_ts`), updates `X-Slack-Request-Timestamp` to `now`, and replays the original `X-Slack-Signature`. | **`401 Unauthorized`** | • **Body (5a & 5b):** `{"detail": "Slack request timestamp expired (replay attack detected)"}`<br>• **Body (5c):** `{"detail": "Invalid Slack webhook signature"}`<br>• **Header:** `WWW-Authenticate: WebhookSignature` | **`0 rows`** | `test_slack_signature_and_replay_window_enforcement`<br>`test_slack_replay_window_exact_300_second_boundary`<br>`test_provider_scenario_matrix_status_codes[slack]` |
| **TC-WHAUTH-SLACK-006** | **Unknown Integration ID** | • **Endpoint:** `POST /api/v1/webhooks/integrations/{unknown_or_inactive_uuid}`<br>• **Body & Headers:** Validly signed Slack payload with fresh `X-Slack-Request-Timestamp` and `X-Slack-Signature: v0=<hex>` sent to a non-existent UUID (or inactive Slack integration `is_active = false`). | **`404 Not Found`** | • **Body:** `{"detail": "Integration not found or inactive"}` | **`0 rows`** | `test_unknown_and_inactive_integration_rejection`<br>`test_provider_scenario_matrix_status_codes[slack]` |

---

### 3.4 Cross-Cutting Routing, Provider-Level & Resilience Scenarios

| Scenario ID | Scenario Name | Providers Covered | Request / Fault Condition | Expected Status Code | Expected Outcome | Automated Pytest Mapping |
| :--- | :--- | :---: | :--- | :---: | :--- | :--- |
| **TC-WHAUTH-SEC-001** | **Cross-Tenant Secret Isolation & Spoofed `X-Tenant-ID` Ignored** | `github`, `jira`, `slack` | 1. Sign payload with Tenant B's secret and send to Tenant A's `integration_id`.<br>2. Sign payload with Tenant A's secret, attach spoofed `X-Tenant-ID: <tenant_b>`, and send to Tenant A's `integration_id`. | • **1:** **`401 Unauthorized`**<br>• **2:** **`202 Accepted`** | 1. Cross-tenant secret rejected; `0 rows` in both tenants.<br>2. Spoofed `X-Tenant-ID` ignored; `1 row` staged in Tenant A and `0 rows` in Tenant B. | `test_cross_tenant_secret_isolation_and_spoofed_tenant_header_ignored` |
| **TC-WHAUTH-SEC-002** | **Provider-Level Endpoint Authentication (`/api/v1/webhooks/{provider}`)** | `github`, `jira`, `slack`, `generic` | Send valid, tampered, wrong-secret, missing-signature, and replayed requests to `POST /api/v1/webhooks/{provider}` with `X-Webhook-Secret`. | • Valid: **`202 Accepted`**<br>• Failure: **`401 Unauthorized`**<br>• Bad provider: **`400 Bad Request`** | Valid signature stages `1 row`; tampered/invalid/replayed requests return `401` and stage `0 rows`. | `test_provider_level_endpoint_signature_and_replay_scenarios` |
| **TC-WHAUTH-SEC-003** | **PostgreSQL Outage Resilience (`SCRUM-331`)** | `github`, `jira`, `slack` | PostgreSQL connection pool is unreachable (`OperationalError` / `PostgresError`) during route lookup or event staging. | **`503 Service Unavailable`** | Returns `Retry-After: 30` header and `{"detail": "Database storage service unavailable. Please retry delivery."}`; never returns `202`. | `test_postgresql_unavailable_returns_http_503` |
| **TC-WHAUTH-SEC-004** | **Guardrail: Removing Signature Verification Fails Tests (`SCRUM-328` AC 2)** | `github`, `jira`, `slack` | Monkeypatch `verify_signature_for_provider` or provider verifiers to no-ops and run the failure assertions. | Raises `AssertionError` | Proves that disabling signature verification causes tampered, unsigned, and replayed requests to return `202` instead of `401`, failing the test suite. | `test_removing_signature_verification_makes_tests_fail` |

---

## 4. Detailed Scenario Specifications Per Provider

### 4.1 GitHub Authentication Scenarios (`provider = 'github'`)

#### `TC-WHAUTH-GH-001` — GitHub: Valid Signature (`202 Accepted`)
- **Preconditions:** Active GitHub integration `integration_id` registered for Tenant A with encrypted secret `gh_secret_token_abcdef123456`.
- **Signing Formula:**
  $$\text{signature} = \text{"sha256="} \mathbin{\Vert} \text{hex}\!\left(\text{HMAC-SHA256}(\text{key}=\text{secret},\ \text{msg}=\text{raw\_body})\right)$$
- **Request:**
  - `POST /api/v1/webhooks/integrations/{integration_id}`
  - Headers:
    - `Content-Type: application/json`
    - `X-GitHub-Delivery: <uuid>`
    - `X-GitHub-Event: push`
    - `X-Hub-Signature-256: sha256=<64-char-hex-digest>`
  - Raw Body: `{"ref": "refs/heads/main", "commits": [{"id": "c123"}]}` (including non-canonical pretty-printed JSON with extra whitespace and Unicode characters).
- **Expected Result:**
  - Status Code: **`202 Accepted`**
  - JSON Response: `{"status": "accepted", "duplicate": false, "provider": "github", ...}`
  - Database State: **`1 row`** inserted in `webhook_events` under Tenant A (`status = 'staged'`).

#### `TC-WHAUTH-GH-002` — GitHub: Tampered Body (`401 Unauthorized`)
- **Attack Vector:** An intermediary or attacker intercepts a validly signed GitHub webhook and modifies the JSON body in transit (e.g., changing commit ID `"c123"` to `"TAMPERED"` or re-serializing JSON whitespace) while keeping the original `X-Hub-Signature-256` header.
- **Expected Result:**
  - Status Code: **`401 Unauthorized`**
  - Response Header: `WWW-Authenticate: WebhookSignature`
  - JSON Response: `{"detail": "Invalid GitHub webhook signature"}`
  - Database State: **`0 rows`** added to `webhook_events`.

#### `TC-WHAUTH-GH-003` — GitHub: Wrong Secret (`401 Unauthorized`)
- **Attack Vector:** Sender computes a syntactically valid `X-Hub-Signature-256: sha256=<hex>` header over the exact request body, but signs it using an incorrect secret (`"wrong_gh_secret_999"`) or another tenant's integration secret.
- **Expected Result:**
  - Status Code: **`401 Unauthorized`**
  - Response Header: `WWW-Authenticate: WebhookSignature`
  - JSON Response: `{"detail": "Invalid GitHub webhook signature"}`
  - Database State: **`0 rows`** added to `webhook_events`.

#### `TC-WHAUTH-GH-004` — GitHub: Missing Signature (`401 Unauthorized`)
- **Attack Vector:** Request is sent to the GitHub integration endpoint without `X-Hub-Signature-256` and without `X-Hub-Signature` (or with a blank/whitespace-only header value `"   "`).
- **Expected Result:**
  - Status Code: **`401 Unauthorized`**
  - Response Header: `WWW-Authenticate: WebhookSignature`
  - JSON Response: `{"detail": "Missing GitHub webhook signature header"}`
  - Database State: **`0 rows`** added to `webhook_events`.

#### `TC-WHAUTH-GH-005` — GitHub: Unknown Integration ID (`404 Not Found`)
- **Attack Vector:** A validly signed GitHub webhook request is sent to `POST /api/v1/webhooks/integrations/{unknown_id}` where `{unknown_id}` does not exist in `integrations` (or references an integration with `is_active = false`).
- **Expected Result:**
  - Status Code: **`404 Not Found`**
  - JSON Response: `{"detail": "Integration not found or inactive"}`
  - Database State: **`0 rows`** added to `webhook_events`; no tenant information is leaked.

---

### 4.2 Jira Authentication Scenarios (`provider = 'jira'`)

#### `TC-WHAUTH-JIRA-001` — Jira: Valid Signature (`202 Accepted`)
- **Preconditions:** Active Jira integration `integration_id` registered for Tenant A with encrypted secret `jira_secret_xyz_789`.
- **Signing Formula:**
  $$\text{signature} = \text{"sha256="} \mathbin{\Vert} \text{hex}\!\left(\text{HMAC-SHA256}(\text{key}=\text{secret},\ \text{msg}=\text{raw\_body})\right)$$
- **Request:**
  - `POST /api/v1/webhooks/integrations/{integration_id}`
  - Headers:
    - `Content-Type: application/json`
    - `X-Atlassian-Webhook-Identifier: jira-del-001`
    - `X-Atlassian-Webhook-Event: jira:issue_created`
    - `X-Hub-Signature-256: sha256=<64-char-hex-digest>` (or `X-Atlassian-Webhook-Signature: sha256=<64-char-hex-digest>`)
  - Raw Body: `{"webhookEvent": "jira:issue_created", "timestamp": 1730300000}`
- **Expected Result:**
  - Status Code: **`202 Accepted`**
  - JSON Response: `{"status": "accepted", "duplicate": false, "provider": "jira", ...}`
  - Database State: **`1 row`** inserted in `webhook_events` under Tenant A (`status = 'staged'`).

#### `TC-WHAUTH-JIRA-002` — Jira: Tampered Body (`401 Unauthorized`)
- **Attack Vector:** Request body is altered after signing (e.g., `"webhookEvent": "jira:issue_deleted"`) while preserving the original `X-Hub-Signature-256` or `X-Atlassian-Webhook-Signature` header.
- **Expected Result:**
  - Status Code: **`401 Unauthorized`**
  - Response Header: `WWW-Authenticate: WebhookSignature`
  - JSON Response: `{"detail": "Invalid Jira webhook signature"}`
  - Database State: **`0 rows`** added to `webhook_events`.

#### `TC-WHAUTH-JIRA-003` — Jira: Wrong Secret (`401 Unauthorized`)
- **Attack Vector:** Payload is signed using `"wrong_jira_secret_000"` (or another tenant's secret) instead of the integration's decrypted secret.
- **Expected Result:**
  - Status Code: **`401 Unauthorized`**
  - Response Header: `WWW-Authenticate: WebhookSignature`
  - JSON Response: `{"detail": "Invalid Jira webhook signature"}`
  - Database State: **`0 rows`** added to `webhook_events`.

#### `TC-WHAUTH-JIRA-004` — Jira: Missing Signature (`401 Unauthorized`)
- **Attack Vector:** Jira webhook request omits `X-Hub-Signature-256`, `X-Atlassian-Webhook-Signature`, and `X-Hub-Signature` (or provides a whitespace-only signature header).
- **Expected Result:**
  - Status Code: **`401 Unauthorized`**
  - Response Header: `WWW-Authenticate: WebhookSignature`
  - JSON Response: `{"detail": "Missing Jira webhook signature header"}`
  - Database State: **`0 rows`** added to `webhook_events`.

#### `TC-WHAUTH-JIRA-005` — Jira: Unknown Integration ID (`404 Not Found`)
- **Attack Vector:** Validly signed Jira webhook is sent to `POST /api/v1/webhooks/integrations/{unknown_id}` where `{unknown_id}` is non-existent or inactive (`is_active = false`).
- **Expected Result:**
  - Status Code: **`404 Not Found`**
  - JSON Response: `{"detail": "Integration not found or inactive"}`
  - Database State: **`0 rows`** added to `webhook_events`.

---

### 4.3 Slack Authentication & Replay Scenarios (`provider = 'slack'`)

#### `TC-WHAUTH-SLACK-001` — Slack: Valid Signature Within 5-Minute Window (`202 Accepted`)
- **Preconditions:** Active Slack integration `integration_id` registered for Tenant A with encrypted signing secret `slack_signing_secret_998877`.
- **Signing Formula:**
  $$\text{basestring} = \text{"v0:"} \mathbin{\Vert} \text{timestamp} \mathbin{\Vert} \text{":"} \mathbin{\Vert} \text{raw\_body}$$
  $$\text{signature} = \text{"v0="} \mathbin{\Vert} \text{hex}\!\left(\text{HMAC-SHA256}(\text{key}=\text{secret},\ \text{msg}=\text{basestring})\right)$$
- **Request:**
  - `POST /api/v1/webhooks/integrations/{integration_id}`
  - Headers:
    - `Content-Type: application/json`
    - `X-Slack-Request-Timestamp: <unix_epoch_seconds>` (where $\left|\text{now} - \text{timestamp}\right| \le 300$)
    - `X-Slack-Signature: v0=<64-char-hex-digest>`
  - Raw Body: `{"event_id": "Ev01", "event": {"text": "hello"}}`
- **Expected Result:**
  - Status Code: **`202 Accepted`**
  - JSON Response: `{"status": "accepted", "duplicate": false, "provider": "slack", ...}`
  - Database State: **`1 row`** inserted in `webhook_events` under Tenant A (`status = 'staged'`).

#### `TC-WHAUTH-SLACK-002` — Slack: Tampered Body (`401 Unauthorized`)
- **Attack Vector:** Request carries a fresh `X-Slack-Request-Timestamp` and a valid `X-Slack-Signature` computed over the original body, but the request body bytes are modified in transit (`"text": "tampered"`).
- **Expected Result:**
  - Status Code: **`401 Unauthorized`**
  - Response Header: `WWW-Authenticate: WebhookSignature`
  - JSON Response: `{"detail": "Invalid Slack webhook signature"}`
  - Database State: **`0 rows`** added to `webhook_events`.

#### `TC-WHAUTH-SLACK-003` — Slack: Wrong Secret (`401 Unauthorized`)
- **Attack Vector:** Request has a fresh `X-Slack-Request-Timestamp` and matching payload, but `X-Slack-Signature` is computed using `"wrong_slack_secret_000"` (or uses an unsupported version prefix such as `v1=<hex>`).
- **Expected Result:**
  - Status Code: **`401 Unauthorized`**
  - Response Header: `WWW-Authenticate: WebhookSignature`
  - JSON Response: `{"detail": "Invalid Slack webhook signature"}`
  - Database State: **`0 rows`** added to `webhook_events`.

#### `TC-WHAUTH-SLACK-004` — Slack: Missing Signature or Timestamp Header (`401 Unauthorized`)
- **Attack Vector:**
  - **Sub-case 4a:** `X-Slack-Signature` header is missing or empty while `X-Slack-Request-Timestamp` is present.
  - **Sub-case 4b:** `X-Slack-Request-Timestamp` header is missing or empty while `X-Slack-Signature` is present (or both headers are omitted).
  - **Sub-case 4c:** `X-Slack-Request-Timestamp` is non-numeric (`"not-a-unix-timestamp"`).
- **Expected Result:**
  - Status Code: **`401 Unauthorized`**
  - Response Header: `WWW-Authenticate: WebhookSignature`
  - JSON Response:
    - Sub-cases 4a & 4b: `{"detail": "Missing Slack signature or timestamp header"}`
    - Sub-case 4c: `{"detail": "Invalid Slack timestamp format"}`
  - Database State: **`0 rows`** added to `webhook_events`.

#### `TC-WHAUTH-SLACK-005` — Slack: Replayed Request (`401 Unauthorized`)
- **Attack Vectors & Replay Window Boundary (`SLACK_MAX_REPLAY_AGE_SECONDS = 300`):**
  1. **Sub-case 5a — Expired Past Request ($> 5\text{ minutes}$ old):**
     - Attacker replays a captured Slack request whose `X-Slack-Request-Timestamp` is older than `300` seconds (e.g., `now - 305` or exact boundary `now - 301`), even though `X-Slack-Signature` is cryptographically valid for that timestamp and body.
     - Rejected with **`401 Unauthorized`** and `{"detail": "Slack request timestamp expired (replay attack detected)"}`.
  2. **Sub-case 5b — Future-Skewed Request ($> 5\text{ minutes}$ ahead):**
     - Request carries a pre-signed future timestamp exceeding the 5-minute window (`now + 310` or exact boundary `now + 301`).
     - Rejected with **`401 Unauthorized`** and `{"detail": "Slack request timestamp expired (replay attack detected)"}`.
  3. **Sub-case 5c — Exact 300-Second Boundary Verification:**
     - `now - 300`, `now`, and `now + 300` are accepted (**`202 Accepted`**).
     - `now - 301` and `now + 301` are rejected (**`401 Unauthorized`**).
  4. **Sub-case 5d — Timestamp-Swap Replay Attack:**
     - Attacker intercepts an expired Slack webhook (`old_ts`, `old_sig`), replaces `X-Slack-Request-Timestamp` with a fresh timestamp (`now`) to bypass the 300-second check, and sends the original `X-Slack-Signature: <old_sig>`.
     - Because `X-Slack-Request-Timestamp` is bound inside the HMAC basestring `v0:{timestamp}:{raw_body}`, the recomputed digest mismatches `<old_sig>` and the request is rejected with **`401 Unauthorized`** and `{"detail": "Invalid Slack webhook signature"}`.
- **Database State:** **`0 rows`** added to `webhook_events` across all replay variants.

#### `TC-WHAUTH-SLACK-006` — Slack: Unknown Integration ID (`404 Not Found`)
- **Attack Vector:** Validly signed Slack webhook with a fresh `X-Slack-Request-Timestamp` is sent to `POST /api/v1/webhooks/integrations/{unknown_id}` where `{unknown_id}` does not exist or is inactive (`is_active = false`).
- **Expected Result:**
  - Status Code: **`404 Not Found`**
  - JSON Response: `{"detail": "Integration not found or inactive"}`
  - Database State: **`0 rows`** added to `webhook_events`.

---

## 5. Running the Automated Verification Suite

### Locally Against PostgreSQL 16
```bash
# 1. Start PostgreSQL 16 service and apply migrations to head
docker compose -f backend/docker-compose.yml up -d --wait postgres
python backend/scripts/verify_migrations.py --step upgrade

# 2. Run the webhook authentication & replay protection test suite
python -m pytest backend/tests/test_webhook_authentication.py -v
```

### In GitHub Actions CI (`.github/workflows/ci.yml`)
Every pull request and push executes [`backend/tests/test_webhook_authentication.py`](../backend/tests/test_webhook_authentication.py) in both the `backend-test` and `pgbouncer-integration` jobs against PostgreSQL 16 under `eng_app_user` (`FORCE ROW LEVEL SECURITY`), gating merges via `ci-gate`.
