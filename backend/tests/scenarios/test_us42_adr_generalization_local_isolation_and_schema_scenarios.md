# US-42: ADR Generalization — Zero Outbound WAN Calls (`local_only`), `cloud_zdr` Refusal Guard & Strict JSON Schema Scenarios

**Task:** `SCRUM-571` (`TASK-316-2`: Write scenarios & pytest tests verifying 0 outbound calls in `local_only`, `cloud_zdr` refusal guard & schema output)  
**Parent Story:** `SCRUM-516` (`STORY-316` — `US-42`: Privacy-Preserving ADR Generalization)  
**Assignee:** Matvii Yeromenko  
**Estimate:** 0.75 SP (`1h 30m` / `90m`)  
**Target Modules:** [`backend/app/adr/generalizer.py`](../../app/adr/generalizer.py), [`backend/app/ai/client_factory.py`](../../app/ai/client_factory.py), [`backend/app/adr/schemas.py`](../../app/adr/schemas.py)  
**Authoritative Pytest Suites:** [`backend/tests/test_adr_generalization_isolation_and_schema.py`](../test_adr_generalization_isolation_and_schema.py), [`backend/tests/test_adr_scrubbing_and_generalization.py`](../test_adr_scrubbing_and_generalization.py)

---

## 1. Objective & Scope

Validate that MANTA's ADR generalization worker (`process_adr_generalize_job`):
1. Executes **0 outbound WAN network calls** when `tenant.ai_processing_mode == "local_only"`, routing only to validated loopback/private/internal inference endpoints (`LocalAIClient`).
2. Enforces the fail-closed **`cloud_zdr` policy refusal guard** (`CloudPolicyViolationError`) whenever a `local_only` tenant attempts to use `CloudZDRAIClient` or `requested_mode="cloud_zdr"`.
3. Enforces **strict JSON schema adherence** (`GeneralizedADRPattern` with `extra="forbid"`, requiring `title`, `architecture_pattern`, `context_and_problem`, `decision_outcome`, `consequences`, and optional `quality_attributes`), rejecting malformed JSON, missing fields, or unexpected keys with `ADRGeneralizationSchemaError`.
4. Persists the validated schema into `AnonymizedADR` and `ADRSanitizationLog.solution_architect_diff_payload`, transitioning `AnonymizedADR.status` from `draft` to `pending_review`.

---

## 2. QA Verification Scenarios

### Scenario 1: Zero Outbound WAN Calls in `local_only` Mode + `pending_review` Transition
- **Given:** A tenant configured with `ai_processing_mode = "local_only"`, an `AnonymizedADR` in `draft` status, and a scrubbed `ADRSource`.
- **When:** `process_adr_generalize_job` runs with `LocalAIClient` pointing to `http://127.0.0.1:11434/v1` while global `httpx.AsyncHTTPTransport` and `socket.create_connection` are guarded against any WAN egress.
- **Expected Result:**
  - **0** outbound WAN HTTP or socket calls occur; only the loopback endpoint is invoked.
  - LLM output validates against `GeneralizedADRPattern`.
  - `AnonymizedADR.status` transitions `draft -> pending_review` with `sanitization_verified = False`.
  - `ADRSanitizationLog` persists `ai_mode = "local_only"` and `generalized_pattern` in `solution_architect_diff_payload`.

### Scenario 2: Explicit `cloud_zdr` Policy Refusal Guard
- **Given:** A tenant configured with `ai_processing_mode = "local_only"`.
- **When:** `process_adr_generalize_job` is invoked with either:
  1. An explicit `CloudZDRAIClient` instance, or
  2. `requested_mode = "cloud_zdr"` in the job payload.
- **Expected Result:**
  - `enforce_ai_client_policy` raises `CloudPolicyViolationError` **before** any HTTP request is constructed or sent.
  - **0** outbound HTTP/socket calls occur.
  - `AnonymizedADR.status` remains `"draft"` and no `ADRSanitizationLog` row is written.

### Scenario 3: Strict `GeneralizedADRPattern` JSON Schema Adherence
- **Given:** An active AI client returning:
  1. Non-JSON text / truncated JSON,
  2. JSON missing required fields (e.g., `architecture_pattern` or `decision_summary`), or
  3. JSON containing extra unexpected fields (`extra="forbid"`).
- **When:** `process_adr_generalize_job` validates the completion content via `GeneralizedADRPattern.model_validate_json(...)`.
- **Expected Result:**
  - `ADRGeneralizationSchemaError` is raised.
  - `AnonymizedADR.status` remains `"draft"` and no `ADRSanitizationLog` row is persisted.

### Scenario 4: `cloud_zdr` Valid Schema Persistence
- **Given:** A tenant configured with `ai_processing_mode = "cloud_zdr"` and a mocked `CloudZDRAIClient` returning valid `GeneralizedADRPattern` JSON.
- **When:** `process_adr_generalize_job` executes.
- **Expected Result:**
  - Request includes `response_format` enforcing `json_schema` (`GeneralizedADRPattern`).
  - `AnonymizedADR.status` transitions `draft -> pending_review` and `ADRSanitizationLog` records `ai_mode = "cloud_zdr"` and valid `generalized_pattern`.

---

## 3. Acceptance Criteria Traceability

| Acceptance Criterion | Scenario(s) | Automated Test(s) in `test_adr_generalization_isolation_and_schema.py` |
| --- | --- | --- |
| Zero external calls under `local_only` | Scenario 1 | `test_local_only_generalization_makes_zero_wan_calls_and_transitions_to_pending_review` |
| Refusal if Cloud ZDR is disabled | Scenario 2 | `test_cloud_zdr_refusal_guard_blocks_cloud_client_and_requested_mode_when_local_only` |
| Valid JSON schema adherence & `pending_review` persistence | Scenario 3, 4 | `test_strict_json_schema_adherence_rejects_malformed_or_extra_fields`, `test_cloud_zdr_mode_with_mocked_cloud_provider_persists_valid_schema_and_pending_review` |
