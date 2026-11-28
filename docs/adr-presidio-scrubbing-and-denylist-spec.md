# ADR Presidio Entity Recognizers, Shannon-Entropy Token Regexes & Tenant Deny-List Specification

**Parent User Stories:** `US-41` (`SCRUM-515` / `STORY-315`) & `US-42` (`SCRUM-516` / `STORY-316`)  
**Task:** `TASK-315-0` (Specify Presidio entity recognizers, Shannon-entropy token regexes & tenant deny-list schema)  
**Category:** Design / Security Specification  
**Target Storage Engine:** PostgreSQL 16 with Row-Level Security (`pgvector/pgvector:pg16`)  
**Target Implementation Modules:**
- [`backend/app/adr/scrubber.py`](../backend/app/adr/scrubber.py)
- [`backend/app/adr/generalizer.py`](../backend/app/adr/generalizer.py)
- [`backend/app/tenants/schemas.py`](../backend/app/tenants/schemas.py)
- [`backend/app/tenants/router.py`](../backend/app/tenants/router.py)
- [`backend/app/db/models/models.py`](../backend/app/db/models/models.py)

---

## 1. Executive Summary & Architectural Invariants

Before raw Architectural Decision Record (ADR) Markdown extracted from tenant repositories (`adr_sources`) can be passed to any LLM generalization worker (either `local_only` or `cloud_zdr`), it **must** pass through a deterministic, offline-capable sanitization barrier (`ADRScrubber`) that scrubs all sensitive tenant identifiers, PII, credentials, and network coordinates.

Core security invariants:
1. **Zero Outbound WAN Dependency in Scrubber:** Presidio's `AnalyzerEngine` must initialize strictly offline via `OfflineSpacyNlpEngine` (using pre-installed local spaCy models if present, otherwise `spacy.blank("en")`), guaranteeing zero model downloads or external network calls in CI, Docker, and `local_only` deployments.
2. **Typed Placeholders Over Destructive Deletion:** Sensitive spans are replaced with square-bracket typed placeholders (`[REDACTED_*]`) rather than XML/angle-bracket tags (`<...>`) so Markdown renderers, HTML sanitizers, and the ADR Governance side-by-side diff UI preserve redaction markers verbatim without stripping them as unknown HTML tags.
3. **Preservation of Architectural Prose:** Technical terms (e.g., `PostgreSQL 16.2`, `Transactional Outbox`, `Redis`, `v1.2.3`, `HNSW`, `pgvector`) and Markdown structure (headings, fenced code blocks, lists) must remain intact so downstream architectural generalization retains technical fidelity.
4. **Fail-Closed Quarantine:** Any unhandled scanner exception, missing/empty `ADRSource`, or post-LLM residual leak transitions `AnonymizedADR.status` to `'quarantined'` (`sanitization_verified = FALSE`, `decision_summary = NULL`), records an audit row in `adr_sanitization_logs` with `raw_input_snippet = NULL`, and forwards **zero** raw text to any LLM client.

---

## 2. Canonical Redaction Classes & Placeholder Conventions

All detectors map into exactly **4 canonical redaction classes**:

| Canonical Class | Placeholder Token | Presidio / Custom Detector Stack | Scope & Exclusions |
| :--- | :--- | :--- | :--- |
| `EMAIL` | `[REDACTED_EMAIL]` | Presidio `EmailRecognizer` (`EMAIL_ADDRESS` -> `EMAIL`) + RFC-5322 email pattern | Personal, corporate, and internal email addresses (e.g., `alex.m@alphabank.internal`). |
| `IP_ADDRESS` | `[REDACTED_IP_ADDRESS]` | Presidio `IpRecognizer` + strict IPv4/IPv6 CIDR & `host:port` recognizer | Validated via `ipaddress.ip_address(...)`. Excludes software versions (`v1.2.3`, `16.2`) and non-IP dotted identifiers. |
| `API_TOKEN` | `[REDACTED_API_TOKEN]` | `ApiTokenEntropyRecognizer` (Tier 1 secret signatures + Tier 2 Shannon-entropy scanner) | Provider API keys, JWTs, PEM private keys, `Bearer` tokens, URI userinfo passwords, secret assignments, high-entropy hex/base64 literals. |
| `COMPANY_NAME` | `[REDACTED_COMPANY_NAME]` | `TenantDenyListRecognizer` + `CompanyNameRecognizer` (corporate suffixes & tenant identity) | Organization/client names, internal codenames, tenant `name`/`slug`/`allowed_domain` brand, legal-entity suffixes (`Corp`, `Inc`, `LLC`, `Ltd`, `GmbH`, `Bank`). Excludes built-in tech allowlist unless explicitly listed in tenant deny-list. |

---

## 3. Microsoft Presidio Recognizer Architecture

### 3.1 Offline NLP Engine (`OfflineSpacyNlpEngine`)
To comply with `local_only` zero-WAN guarantees and deterministic CI builds without downloading `en_core_web_lg` at runtime:
1. Check `importlib.util.find_spec` for `en_core_web_sm` / `en_core_web_lg`; load locally if available.
2. Otherwise construct `spacy.blank("en")` and inject it into `SpacyNlpEngine` (`self.nlp = {"en": loaded_model}`).
3. Register custom deterministic recognizers on `RecognizerRegistry(supported_languages=["en"])` and attach per-tenant `TenantDenyListRecognizer` dynamically via `ad_hoc_recognizers` on each `analyze()` call so no tenant's deny-list ever leaks into shared singleton state.

### 3.2 Span Conflict Resolution & Anonymization Rules
When multiple recognizers match overlapping text spans:
1. **Never Re-Scrub Placeholders:** Existing `[REDACTED_(?:EMAIL|IP_ADDRESS|API_TOKEN|COMPANY_NAME)]` spans are skipped before recognition.
2. **Priority Tie-Breaking:** For identical `(start, end)` spans, precedence is strictly:
   $$\texttt{API\_TOKEN} > \texttt{EMAIL} > \texttt{IP\_ADDRESS} > \texttt{COMPANY\_NAME}$$
   (e.g., an email inside a `scheme://user:pass@host` URI does not split the password token; a `COMPANY_NAME` domain inside an `EMAIL` is superseded by `EMAIL`).
3. **Enclosing Span Rule:** A longer enclosing match with confidence $\ge$ inner match supersedes contained sub-spans.
4. **1:1 Replacement Counting:** `AnonymizerEngine.anonymize(...)` is invoked with `merge_entities_with_spaces=False` and explicit `OperatorConfig("replace", {"new_value": PLACEHOLDER_BY_CLASS[cls]})` for each of the 4 classes, guaranteeing exact 1:1 correspondence between replaced spans and logged category counts.

---

## 4. API Token Regexes & Shannon-Entropy Thresholds

### 4.1 Shannon-Entropy Formula
For any candidate string $s$ of length $N = |s| > 0$ over observed character alphabet $\Sigma$:

$$H(s) = -\sum_{c \in \Sigma} p(c) \log_2 p(c), \quad p(c) = \frac{\text{count}(c)}{N}$$

- Minimum entropy is $0.0\text{ bits/char}$ (single repeated character).
- Uniform random lowercase hex ($\Sigma = 16$) approaches $\log_2 16 = 4.0\text{ bits/char}$.
- Uniform random base64/alphanumeric ($\Sigma \approx 64$) approaches $\log_2 64 = 6.0\text{ bits/char}$.

### 4.2 Tier 1: Deterministic Secret Signatures (`score = 0.95–1.00`)
Matched regardless of length/entropy thresholds:
- **OpenAI / Anthropic keys:** `\bsk-(?:proj-|ant-api\d+-|live-|test-)?[A-Za-z0-9_-]{20,}\b` (`score = 1.0`)
- **GitHub tokens:** `\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b` (`score = 1.0`)
- **GitLab tokens:** `\bglpat-[A-Za-z0-9_-]{20,}\b` (`score = 1.0`)
- **Slack tokens:** `\bxox[baprs]-[A-Za-z0-9-]{10,}\b` (`score = 1.0`)
- **AWS Access Key IDs:** `\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b` (`score = 1.0`)
- **Google API keys:** `\bAIza[0-9A-Za-z_-]{35}\b` (`score = 1.0`)
- **Stripe / SendGrid keys:** `\b(?:rk|pk)_(?:live|test)_[0-9A-Za-z]{16,}\b`, `\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b` (`score = 1.0`)
- **JWTs:** `\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b` (`score = 0.98`)
- **PEM Private Keys:** `-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z0-9 ]*PRIVATE KEY-----` (`score = 1.0`)
- **Bearer Tokens:** `(?i)\bBearer\s+([A-Za-z0-9._~+/=-]{16,})` (redacts capture group 1, `score = 0.96`)
- **URI Userinfo Passwords:** `(?i)\b[a-z][a-z0-9+.-]*://[^:/\s@]+:([^\s/@]+)@` (redacts capture group 1, `score = 0.98`)
- **Secret Key/Value Assignments:** `(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|master[_-]?key|auth[_-]?token|private[_-]?key|password|passwd|pwd)\b\s*[:=]\s*["']?([^\s"'`]{8,})["']?` (redacts value group, excluding boolean/null literals and already-redacted tokens, `score = 0.95`)

### 4.3 Tier 2: Entropy-Gated Secret Candidates (`score = 0.85`)
1. **Hexadecimal Secret Literals:**
   - Pattern: `\b[0-9a-fA-F]{32,128}\b`
   - Structural filter: Must contain at least one digit `[0-9]` and at least one hex letter `[a-fA-F]` (prevents flagging repeated numeric IDs or zeros), and must not be preceded by explicit Git SHA metadata labels unless inline credential context exists.
   - Threshold: $H(s) \ge 3.30\text{ bits/char}$.
2. **Base64 / URL-Safe Alphanumeric Secret Literals:**
   - Pattern: `(?<![A-Za-z0-9/_.-])[A-Za-z0-9+/=_-]{24,256}(?![A-Za-z0-9/_.-])`
   - Structural filter: Must contain at least one digit `[0-9]` and at least 2 distinct character classes among `{uppercase, lowercase, symbol (+/=_-)}` to avoid false positives on long architectural `PascalCase` or `snake_case` symbol names.
   - Thresholds:
     - **Standalone:** $H(s) \ge 4.20\text{ bits/char}$
     - **Context-Windowed (within 48 chars of `token`, `secret`, `key`, `apikey`, `bearer`, `password`, `credential`, `signature`, `salt`):** $H(s) \ge 3.90\text{ bits/char}$

---

## 5. Tenant Deny-List JSON & PostgreSQL Schema

### 5.1 PostgreSQL Column & RLS Policy (`tenants.adr_deny_list`)
Stored directly on the tenant record in `tenants` (which enforces `ENABLE` + `FORCE ROW LEVEL SECURITY` via `tenant_isolation_policy` for `eng_app_user`):

```sql
ALTER TABLE tenants
    ADD COLUMN adr_deny_list JSONB NOT NULL DEFAULT '{}'::jsonb;
```

### 5.2 JSON Schema (`TenantADRDenyListConfig`)
Validated at API ingress (`PATCH /api/v1/tenants/settings`) and audited in `audit_logs` (`action = 'tenant.settings.updated'`):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "TenantADRDenyListConfig",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "version": { "type": "integer", "const": 1, "default": 1 },
    "auto_include_tenant_identity": { "type": "boolean", "default": true },
    "terms": {
      "type": "array",
      "maxItems": 500,
      "items": { "type": "string", "minLength": 2, "maxLength": 256 }
    },
    "entries": {
      "type": "array",
      "maxItems": 500,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["pattern"],
        "properties": {
          "pattern": { "type": "string", "minLength": 2, "maxLength": 256 },
          "match_mode": { "type": "string", "enum": ["literal", "regex"], "default": "literal" },
          "redaction_class": {
            "type": "string",
            "enum": ["EMAIL", "IP_ADDRESS", "API_TOKEN", "COMPANY_NAME"],
            "default": "COMPANY_NAME"
          },
          "case_sensitive": { "type": "boolean", "default": false }
        }
      }
    }
  }
}
```

Security rules on `match_mode = "regex"`:
- Patterns must compile cleanly via `re.compile(...)`.
- Patterns matching empty strings (`compiled.match("") is not None`) are rejected (`422`).
- Nested-quantifier ReDoS patterns (e.g., `(a+)+`, `(.*)*`) are rejected (`422`).

### 5.3 `ADRSanitizationLog.presidio_entities_scrubbed` Contract
Every sanitization execution writes a row to `adr_sanitization_logs` with category counts compatible with `extract_redaction_summary` (`backend/app/adr/service.py`):

```json
{
  "EMAIL": 1,
  "IP_ADDRESS": 2,
  "API_TOKEN": 1,
  "COMPANY_NAME": 3,
  "counts": {
    "EMAIL": 1,
    "IP_ADDRESS": 2,
    "API_TOKEN": 1,
    "COMPANY_NAME": 3
  },
  "total": 7
}
```

---

## 6. Fail-Closed Quarantine State Machine

```mermaid
stateDiagram-v2
    [*] --> draft: Extracted into adr_sources + anonymized_adrs (US-40)
    draft --> pending_review: Scrubber OK + Policy-Guarded LLM JSON Validated + Post-Check Clean
    draft --> quarantined: Scanner Exception / Missing Source / Residual Secret Leak (0 LLM Calls)
    pending_review --> approved: Solution Architect Approves -> Enqueue Vector Embed (US-43)
    pending_review --> quarantined: Solution Architect Rejects (US-43)
```

| Transition | Trigger | `AnonymizedADR` State | `ADRSanitizationLog` Record | Downstream AI Calls |
| :--- | :--- | :--- | :--- | :--- |
| `draft -> quarantined` | Any exception in Presidio / entropy / deny-list scanner, missing `ADRSource`, or post-LLM residual leak | `status = 'quarantined'`, `sanitization_verified = FALSE`, `decision_summary = NULL`, embeddings cleared | `raw_input_snippet = NULL`, zeroed category counts, `solution_architect_diff_payload = {"status": "quarantined", "stage": ..., "error_type": ...}` | **0 (Strictly blocked)** |
| `draft -> pending_review` | Scrubber succeeds AND policy-guarded LLM emits valid `GeneralizedADRPattern` JSON | `status = 'pending_review'`, `sanitization_verified = FALSE`, `title`, `architecture_pattern`, `decision_summary` updated | `raw_input_snippet` = scrubbed preview only, `presidio_entities_scrubbed` counts, `llm_abstraction_prompt_tokens`, `solution_architect_diff_payload` | 1 (`local_only` or `cloud_zdr`) |
| `pending_review -> approved` | Solution Architect sign-off (`POST /api/v1/adrs/{id}/approve`) | `status = 'approved'`, `sanitization_verified = TRUE` | Preserved for audit & governance diff | Enqueues `adr.reembed` |
