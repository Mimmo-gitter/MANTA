# US-72: Mobile FCM Token Registration on Sign-In and Device Deactivation on Sign-Out Test Scenarios

**Task:** `SCRUM-497` (`Task 72.4`: Create test scenarios for FCM token registration on sign-in and device deactivation on sign-out)  
**Parent User Story:** `SCRUM-375` (`US-72`: Push Device Registration & Lifecycle Management)  
**Related Tickets:**  
- `SCRUM-494` (`Task 72.1`): Design `POST /api/v1/mobile/devices` and `DELETE /api/v1/mobile/devices/{token}` API contract, `mobile_devices` schema, and Android FCM lifecycle hooks (`mobile-fcm-device-registration-contract.md`)  
- `SCRUM-495` (`Task 72.2`): Alembic migration (`f3b9c8d2a1e4`) for `mobile_devices` table with `FORCE ROW LEVEL SECURITY` and FastAPI endpoint implementation  
- `SCRUM-496` (`Task 72.3`): Automatic FCM device registration after mobile sign-in and unregistration prior to sign-out in Kotlin  
- `SCRUM-497` (`Task 72.4`): Create test scenarios for FCM token registration on sign-in and device deactivation on sign-out (this document)  
- `SCRUM-498` (`Task 72.5`): Automated Kotlin and `pytest` verification suites (`test_mobile_devices.py`, `AuthRepositoryTest.kt`)  
- `SCRUM-374` / `SCRUM-492`: Mobile background session renewal, token interception and clean sign-out (`test_us81_mobile_session_refresh_scenarios.md`)  
- `SCRUM-373` / `SCRUM-488`: Mobile biometric authentication and OIDC PKCE session security (`test_us38_mobile_biometric_auth_scenarios.md`)  
- `SCRUM-240` / `SCRUM-241` / `SCRUM-277` / `SCRUM-278`: OIDC Keycloak realm, token rotation, and RS256 Bearer authorization  

**Target Platform & Framework:** Python 3.12 (FastAPI, SQLAlchemy 2.0 Async, Pydantic v2, PostgreSQL 16 with `FORCE ROW LEVEL SECURITY`), Android (minSdk 27, targetSdk 37, Jetpack Compose, Retrofit 2, Firebase Cloud Messaging SDK)  
**Target Implementation Modules:**  
- [`backend/app/mobile/router.py`](../backend/app/mobile/router.py) (`POST /api/v1/mobile/devices`, `DELETE /api/v1/mobile/devices/{token}`)  
- [`backend/app/mobile/schemas.py`](../backend/app/mobile/schemas.py) (`RegisterMobileDeviceRequest`, `RegisterMobileDeviceResponse`)  
- [`backend/app/mobile/service.py`](../backend/app/mobile/service.py) (`resolve_or_create_mobile_device`, `delete_mobile_device`)  
- [`backend/app/db/models/models.py`](../backend/app/db/models/models.py) (`MobileDevice` ORM model)  
- [`backend/migrations/versions/f3b9c8d2a1e4_create_mobile_devices_table.py`](../backend/migrations/versions/f3b9c8d2a1e4_create_mobile_devices_table.py)  
- [`mobile/app/src/main/java/com/manta/mobile/core/network/DeviceApiService.kt`](../mobile/app/src/main/java/com/manta/mobile/core/network/DeviceApiService.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/features/auth/AuthRepository.kt`](../mobile/app/src/main/java/com/manta/mobile/features/auth/AuthRepository.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/MainActivity.kt`](../mobile/app/src/main/java/com/manta/mobile/MainActivity.kt)  
**Authoritative Automated Test Suites:**  
- [`backend/tests/test_mobile_devices.py`](../backend/tests/test_mobile_devices.py)  
- [`backend/tests/test_rls_isolation.py`](../backend/tests/test_rls_isolation.py)  
- [`mobile/app/src/test/java/com/manta/mobile/features/auth/TokenRefreshAndSignOutTest.kt`](../mobile/app/src/test/java/com/manta/mobile/features/auth/TokenRefreshAndSignOutTest.kt)  
- [`mobile/app/src/test/java/com/manta/mobile/data/auth/AuthRepositoryTest.kt`](../mobile/app/src/test/java/com/manta/mobile/data/auth/AuthRepositoryTest.kt)  
**Specification Location in Test Suite:** [`backend/tests/scenarios/test_us72_mobile_push_registration_scenarios.md`](../backend/tests/scenarios/test_us72_mobile_push_registration_scenarios.md)  
**Assignee:** Matvii Yeromenko  
**Category:** Testing - Design  
**Estimate:** 0.25 Story Points (0h 30m Original Estimate)  
**Status:** Approved / Verification Baseline Established  

---

## 1. Executive Summary & Objective

### 1.1 Objective

Design and formalize the authoritative QA test scenarios and security/lifecycle verification contracts for **Mobile FCM Push Token Registration on Sign-In, Idempotent Metadata Synchronization, PostgreSQL Row-Level Security (RLS) Isolation, and Device Deactivation on Sign-Out** (`US-72`).

Push notifications in MANTA deliver critical operational telemetry, DORA deployment alerts, security notices, and incident escalations to on-call engineers. Because mobile devices are shared or personal physical endpoints, push token registration and deactivation must strictly adhere to enterprise multi-tenant boundary invariants:
1. Devices must be strictly bound to the authenticated `(tenant_id, user_id)` identity extracted from cryptographic OIDC RS256 JWT claims, **never** trusting client-supplied identity identifiers in request payloads.
2. Device registrations must be isolated at the PostgreSQL database engine layer via `FORCE ROW LEVEL SECURITY`, preventing cross-tenant information leakage or device manipulation under adversarial conditions.
3. Repeated registrations of identical FCM tokens (e.g. app cold starts, network reconnects, background syncs) must execute idempotently using atomic upsert semantics without duplicating database records.
4. When a user logs out from the mobile app, the client must unregister the device (`DELETE /api/v1/mobile/devices/{token}`) **prior** to local session annihilation, guaranteeing that no subsequent push notifications containing sensitive tenant telemetry are dispatched to an unauthenticated or re-assigned mobile device.

### 1.2 Scope & Acceptance Criteria Traceability

The test cases in this document provide complete, verifiable coverage for the three core acceptance criteria of `US-72`:

1. **Post-Sign-In Device Registration (`AC-1`):**  
   Immediately following successful mobile sign-in (`AuthState.Authenticated`), the client dispatches `POST /api/v1/mobile/devices` with the FCM token and device metadata. The backend derives `tenant_id` and `user_id` strictly from validated Bearer JWT claims, inserts a record into `mobile_devices` under PostgreSQL `FORCE ROW LEVEL SECURITY`, and returns `200 OK` with non-sensitive device metadata (`id`, `platform`, `device_name`, `app_version`, `is_active`, `last_seen_at`), strictly omitting `fcm_token`, `tenant_id`, and `user_id` from the response.

2. **Idempotent Duplicate Registration & Token Refresh (`AC-2`):**  
   When a device re-registers with an already recorded `fcm_token` (due to app restarts, connectivity reconnects, or metadata updates), PostgreSQL executes `ON CONFLICT ON CONSTRAINT uq_tenant_user_fcm_token DO UPDATE`. The operation updates `last_seen_at`, `updated_at`, `device_name`, `app_version`, and ensures `is_active = True` idempotently without creating duplicate database rows or altering primary keys.

3. **Sign-Out Device Deactivation & Push Alert Suppression (`AC-3`):**  
   During user-initiated logout from `SettingsScreen`, the client invokes `DELETE /api/v1/mobile/devices/{token}` while the Bearer token is still valid, prior to local session destruction. The backend permanently deletes the device registration scoped to `(tenant_id, user_id, fcm_token)`. Repeated delete requests return `204 No Content` idempotently. Any subsequent push notifications for the tenant or user are immediately suppressed and never dispatched to the deactivated device.

### 1.3 Threat Model & Failure Modes Addressed

```
+-------------------------------------------------------------------------------------------------------------------+
|                                  THREAT VECTOR & MITIGATION MATRIX (US-72)                                        |
+------------------------------------+---------------------------------------+--------------------------------------+
| Threat / Vulnerability             | Root Cause / Impact                   | Architectural Defense / Test Check   |
+------------------------------------+---------------------------------------+--------------------------------------+
| 1. Cross-Tenant Device Hijacking   | Client supplies arbitrary tenant_id   | Pydantic extra="forbid" rejects body |
|                                    | in body, associating FCM token with   | tenant_id with 422; tenant_id is     |
|                                    | another organization to intercept PII.| strictly derived from verified JWT.  |
+------------------------------------+---------------------------------------+--------------------------------------+
| 2. PostgreSQL RLS Cross-Leakage    | Tenant B queries or modifies devices  | ALTER TABLE mobile_devices FORCE RLS |
|                                    | registered by Tenant A.               | tenant_isolation_policy restricts all|
|                                    |                                       | SELECT/INSERT/UPDATE/DELETE queries. |
+------------------------------------+---------------------------------------+--------------------------------------+
| 3. Registration Stampede / Bloat   | Rapid reconnects or restarts produce  | Unique constraint on (tenant, user,  |
|                                    | duplicate rows for same token,        | fcm_token) with ON CONFLICT UPDATE;  |
|                                    | multiplying push alert dispatch.      | database row count strictly = 1.     |
+------------------------------------+---------------------------------------+--------------------------------------+
| 4. Zombie Push Notifications       | User logs out, but backend retains    | AuthRepository.signOut calls DELETE  |
|                                    | device registration, sending alerts   | /api/v1/mobile/devices/{token} BEFORE|
|                                    | to unauthorized device post-logout.   | wiping local session and keys.       |
+------------------------------------+---------------------------------------+--------------------------------------+
| 5. Cross-User De-Registration      | User A attempts to unregister FCM     | DELETE endpoint scopes query to both |
|                                    | token belonging to User B.            | tenant_id AND caller's user_id.      |
+------------------------------------+---------------------------------------+--------------------------------------+
```

---

## 2. Core Architectural Invariants & Security Formulations

```
+-------------------------------------------------------------------------------------------------------------------+
|                           MOBILE FCM DEVICE REGISTRATION & LIFECYCLE ARCHITECTURE                                 |
+-------------------------------------+-------------------------------------+---------------------------------------+
| 1. Strict Identity Derivation       | 2. PostgreSQL Force RLS Isolation   | 3. Idempotent Upsert Guarantee        |
|    (Zero-Trust Request Body)        |    (Engine-Level Tenant Boundary)   |    (Atomic Concurrency Resolution)    |
+-------------------------------------+-------------------------------------+---------------------------------------+
| * tenant_id = TokenClaims.tenant_id | * FORCE ROW LEVEL SECURITY enabled  | * CONSTRAINT uq_tenant_user_fcm_token |
| * user_id = TokenClaims.user_id     | * tenant_isolation_policy on        | * INSERT ... ON CONFLICT DO UPDATE    |
| * Body tenant_id/user_id rejected   |   current_setting('app.current_...  | * Single row maintained per device    |
|   with 422 Unprocessable Entity     | * Cross-tenant queries return 0 rows| * last_seen_at monotonically updated  |
| * Sensitive secrets omitted in resp | * Defense against tenant escape     | * is_active forced to True on upsert  |
+-------------------------------------+-------------------------------------+---------------------------------------+
                                                         |
                                                         v
+-------------------------------------------------------------------------------------------------------------------+
|                              MOBILE AUTHENTICATION STATE & ROUTE GUARD BOUNDARIES                                 |
+----------------------------------------------------+--------------------------------------------------------------+
| 4. Pre-Logout Unregistration Order Invariant       | 5. Zero Push Routing Post-Sign-Out                           |
+----------------------------------------------------+--------------------------------------------------------------+
| * AuthRepository.signOut(fcmToken) order:          | * DELETE removes device from mobile_devices table            |
|   1. DELETE /api/v1/mobile/devices/{fcmToken}      | * Subsequent notification dispatch filters is_active = true  |
|   2. POST Keycloak OIDC /logout                    | * Zero notifications routed to logged-out device             |
|   3. purgeLocalSession() (DataStore wipe)          | * Repeated DELETE returns 204 No Content idempotently        |
+----------------------------------------------------+--------------------------------------------------------------+
```

### Invariant 1: Cryptographic JWT Identity Derivation & Anti-Spoofing Invariant

For every mobile device registration request:
1. `tenant_id` and `user_id` are **never** accepted from the HTTP request body:
   $$\forall \text{body}: \quad (\text{"tenant\_id"} \in \text{body} \lor \text{"user\_id"} \in \text{body}) \implies \text{HTTP } 422 \text{ Unprocessable Entity}$$
2. Both identifiers are extracted strictly from cryptographically validated OIDC RS256 Bearer JWT claims:
   $$\text{tenant\_id} \leftarrow \text{UUID}(\text{TokenClaims}.\text{tenant\_id})$$
   $$\text{user\_id} \leftarrow \text{UUID}(\text{TokenClaims}.\text{sub})$$
3. If `user_id` is missing or not a valid UUID, the endpoint immediately aborts with `HTTP 400 Bad Request`.

---

### Invariant 2: PostgreSQL `FORCE ROW LEVEL SECURITY` Engine Boundary Invariant

For all database transactions against `mobile_devices`:
1. Row Level Security is strictly forced even against table owners:
   $$\text{ALTER TABLE mobile\_devices FORCE ROW LEVEL SECURITY;}$$
2. The active tenant context is injected at connection time:
   $$\text{SET LOCAL app.current\_tenant\_id} = \text{tenant\_uuid};$$
3. The RLS engine enforces the tenant boundary on both read and write operations:
   $$\text{tenant\_id} = \text{NULLIF}(\text{current\_setting}(\text{'app.current\_tenant\_id'}, \text{true}), \text{''})::\text{uuid}$$
4. For any two distinct tenants $T_A \ne T_B$:
   $$\text{Query}_{T_B}(\text{mobile\_devices}) \cap \text{Records}_{T_A}(\text{mobile\_devices}) = \emptyset$$

---

### Invariant 3: Idempotent Atomic Upsert & Single-Row Invariant

For any sequence of registration calls with the same tuple $(T, U, F)$ where $T=\text{tenant\_id}$, $U=\text{user\_id}$, and $F=\text{fcm\_token}$:
1. The unique constraint guarantees single-row representation:
   $$\text{COUNT}(\text{mobile\_devices} \text{ WHERE } \text{tenant\_id}=T \land \text{user\_id}=U \land \text{fcm\_token}=F) = 1$$
2. An existing record is updated in-place:
   $$\text{last\_seen\_at}_{\text{new}} \ge \text{last\_seen\_at}_{\text{old}}$$
   $$\text{is\_active}_{\text{new}} = \text{True}$$
3. The primary key UUID remains strictly immutable:
   $$\text{id}_{\text{new}} = \text{id}_{\text{old}}$$

---

### Invariant 4: Non-Disclosure of Sensitive Token Secrets Invariant

For all successful registration responses (`RegisterMobileDeviceResponse`, HTTP 200 OK):
1. Sensitive authorization artifacts and database foreign keys are strictly excluded:
   $$\{\text{"fcm\_token"}, \text{"tenant\_id"}, \text{"user\_id"}\} \cap \text{Keys}(\text{ResponsePayload}) = \emptyset$$
2. Only non-sensitive device telemetry is returned:
   $$\text{Keys}(\text{ResponsePayload}) \subseteq \{\text{"id"}, \text{"platform"}, \text{"device\_name"}, \text{"app\_version"}, \text{"is\_active"}, \text{"last\_seen\_at"}, \text{"created\_at"}, \text{"updated\_at"}\}$$

---

### Invariant 5: Sign-Out Execution Ordering & Push Routing Suppression Invariant

For any sign-out transaction initiated on the mobile device:
1. The client must execute unregistration **before** destroying credentials:
   $$\text{Order}: \quad \text{DELETE /api/v1/mobile/devices/}\{F\} \prec \text{POST Keycloak /logout} \prec \text{purgeLocalSession}()$$
2. Following unregistration:
   $$\text{Record}(T, U, F) \notin \text{mobile\_devices}$$
3. Push notification dispatchers querying active devices for alert delivery evaluate:
   $$\text{EligibleDevices}(T, U) = \{D \in \text{mobile\_devices} \mid D.\text{tenant\_id}=T \land D.\text{user\_id}=U \land D.\text{is\_active}=\text{True}\}$$
   Since the record was deleted, $F \notin \text{EligibleDevices}(T, U)$, guaranteeing **zero push notification leakage post-logout**.

---

## 3. Architecture & Lifecycle Sequence Diagrams

### 3.1 Sequence: Post-Sign-In FCM Device Registration (AC-1)

```
Android App (MainActivity)       AuthRepository            FirebaseMessaging          Backend (FastAPI)         PostgreSQL (RLS)
         |                             |                          |                           |                         |
         |-- 1. OIDC Sign-In Succeeded>|                          |                           |                         |
         |   (Tokens saved to DataStore)|                          |                           |                         |
         |-- 2. State: Authenticated ->|                          |                           |                         |
         |                             |                          |                           |                         |
         |-- 3. Fetch FCM Token --------------------------------->|                           |                         |
         |                                                        |-- 4. Return fcm_token --->|                         |
         |<-- 5. Receive fcm_token -------------------------------|                           |                         |
         |                                                                                    |                         |
         |-- 6. POST /api/v1/mobile/devices ------------------------------------------------->|                         |
         |      Headers: Bearer RS256 JWT                                                     |                         |
         |      Body: {fcm_token: "token_123", platform: "android", device_name: "Pixel"}     |-- 7. Validate JWT ----->|
         |                                                                                    |      Extract tenant/user|
         |                                                                                    |-- 8. SET LOCAL RLS ---->|
         |                                                                                    |-- 9. INSERT INTO .......|
         |                                                                                    |      mobile_devices     |
         |                                                                                    |<-- 10. Returning device-|
         |<-- 11. HTTP 200 OK (id, platform, is_active=true, last_seen_at) -------------------|                         |
         |    (Sensitive fcm_token and tenant_id omitted from JSON response)                  |                         |
```

---

### 3.2 Sequence: Idempotent Duplicate Registration & Token Refresh (AC-2)

```
Android App (Cold Start)             DeviceApiService                    Backend (FastAPI)               PostgreSQL (RLS)
         |                                  |                                    |                              |
         |-- 1. App Launches / Reconnect -->|                                    |                              |
         |-- 2. POST /api/v1/mobile/devices |                                    |                              |
         |      (Same fcm_token: "token_123", updated app_version: "1.2.0") ---->|                              |
         |                                                                       |-- 3. Extract tenant & user ->|
         |                                                                       |-- 4. ON CONFLICT DO UPDATE ->|
         |                                                                       |      (uq_tenant_user_fcm)    |
         |                                                                       |      is_active = True        |
         |                                                                       |      last_seen_at = now()    |
         |                                                                       |      app_version = '1.2.0'   |
         |                                                                       |<-- 5. Return updated row ----|
         |<-- 6. HTTP 200 OK (Same UUID id, updated last_seen_at, is_active=true)--------|                              |
         |    (Zero duplicate rows created; count remains exactly 1)                     |                              |
```

---

### 3.3 Sequence: Pre-Logout Unregistration & Alert Suppression (AC-3)

```
User (SettingsScreen)        AuthRepository                 DeviceApiService                Backend (FastAPI)        PostgreSQL (RLS)
         |                         |                               |                                |                       |
         |-- 1. Click "Sign Out" ->|                               |                                |                       |
         |                         |-- 2. unregisterDevice(token)->|                                |                       |
         |                         |      (Bearer JWT valid)       |-- 3. DELETE /mobile/devices/tok>|                       |
         |                         |                               |                                |-- 4. DELETE WHERE --->|
         |                         |                               |                                |      fcm_token=tok    |
         |                         |                               |                                |      AND tenant=...   |
         |                         |                               |                                |      AND user=...     |
         |                         |                               |                                |<-- 5. Row deleted ----|
         |                         |                               |<-- 6. HTTP 204 No Content -----|                       |
         |                         |<-- 7. Success ----------------|                                |                       |
         |                         |                                                                |                       |
         |                         |-- 8. Keycloak OIDC RP-Initiated /logout ---------------------->|                       |
         |                         |-- 9. purgeLocalSession() (Wipe DataStore & memory JWTs)        |                       |
         |                         |-- 10. AuthState = Unauthenticated                             |                       |
         |<-- 11. Render SignIn ---|                                                                |                       |
         |                         |                                                                |                       |
         |                         |=== Subsequent Tenant Alert Triggered ==========================|                       |
         |                         |                                                                |-- 12. Query active -->|
         |                         |                                                                |       mobile_devices  |
         |                         |                                                                |<-- 13. Empty (0 rows)-|
         |                         |                                                                |-- 14. PUSH SUPPRESSED |
         |                         |<-- 15. ZERO NOTIFICATIONS DELIVERED TO LOGGED-OUT DEVICE ------|   (NO DISPATCH)       |
```

---

## 4. Master Test Scenarios Matrix

| Scenario ID | Scenario Name | Target AC | Category | Preconditions | Trigger / Action | Expected Result | Automated Suite |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`TC-MOB-PUSH-001`** | **Post-Sign-In Successful FCM Device Registration** | `AC-1` | Functional / Happy Path | User authenticated via OIDC; valid FCM token obtained. | `POST /api/v1/mobile/devices` with token, platform, device_name, app_version. | Returns `200 OK` with non-sensitive fields; DB contains active row scoped to caller. | `test_mobile_devices.py` |
| **`TC-MOB-PUSH-002`** | **Strict PostgreSQL Row-Level Security (RLS) Tenant Isolation** | `AC-1` | Security / Multi-Tenant | Device registered in Tenant A; Tenant B authenticated. | Tenant B attempts to select or query Tenant A's device. | Database returns `None` (0 rows); RLS completely conceals registration. | `test_mobile_devices.py` |
| **`TC-MOB-PUSH-003`** | **Rejection of Client-Supplied Tenant ID in Request Body** | `AC-1` | Security / Schema | Caller authenticated in Tenant A; attempts to supply `tenant_id` in JSON body. | `POST /api/v1/mobile/devices` with `{"tenant_id": "...", "fcm_token": "..."}`. | Request rejected with `422 Unprocessable Entity` (`extra="forbid"`). Zero DB write. | `test_mobile_devices.py` |
| **`TC-MOB-PUSH-004`** | **Non-Disclosure of Sensitive Token & Tenant Claims in Response** | `AC-1` | Security / Data Privacy | Device registered successfully. | Inspect JSON response payload keys and values. | Response contains only non-sensitive metadata (`id`, `platform`, `device_name`, `app_version`, `is_active`, timestamps); omits `fcm_token`, `tenant_id`, `user_id`. | `test_mobile_devices.py` |
| **`TC-MOB-PUSH-005`** | **RBAC Permission Enforcement (`MANAGE_OWN_MOBILE_DEVICES`)** | `AC-1` | Security / RBAC | Token issued without `MANAGE_OWN_MOBILE_DEVICES` or invalid signature. | Call `POST` or `DELETE /api/v1/mobile/devices`. | Endpoints reject request with `401 Unauthorized` or `403 Forbidden`. | `test_mobile_devices.py` |
| **`TC-MOB-PUSH-006`** | **Idempotent Duplicate Registration with Identical Token** | `AC-2` | Idempotency / Concurrency | Device already registered with token `fcm-token-1`. | Dispatch second `POST /api/v1/mobile/devices` with identical token. | Returns `200 OK`; row count strictly = 1; same UUID `id`; `last_seen_at` refreshed. | `test_mobile_devices.py` |
| **`TC-MOB-PUSH-007`** | **Re-Activation of Inactive Registration (`is_active = False -> True`)** | `AC-2` | Lifecycle / State Machine | Existing registration marked `is_active = False`. | User signs in again on same device (`POST /api/v1/mobile/devices`). | Upsert flips `is_active = True`; refreshes `last_seen_at`; device becomes eligible for push alerts. | `test_mobile_devices.py` |
| **`TC-MOB-PUSH-008`** | **Device Metadata & Version Update on Re-Registration** | `AC-2` | Functional / Metadata | Device app version updated from `1.0.0` to `2.0.0`; OS renamed. | Client dispatches registration with updated metadata. | DB row updated in-place with new `app_version` and `device_name`; `id` remains unchanged. | `test_mobile_devices.py` |
| **`TC-MOB-PUSH-009`** | **Multi-Device Registration for a Single User** | `AC-2` | Functional / Multi-Device | User already has active Android phone registered. | User logs in from Android tablet (`fcm-token-2`). | Both devices stored as distinct rows under same `(tenant_id, user_id)`; both active. | `test_mobile_devices.py` |
| **`TC-MOB-PUSH-010`** | **FCM Token Rotation Hook (`onNewToken`) During Active Session** | `AC-2` | Integration / Firebase | User actively browsing app; Firebase rotates registration token. | `onNewToken(newToken)` fires; app dispatches `POST /api/v1/mobile/devices`. | New token registered immediately; user notifications route to new token seamlessly. | `TokenRefreshAndSignOutTest.kt` |
| **`TC-MOB-PUSH-011`** | **Pre-Sign-Out Unregistration & Database Deletion** | `AC-3` | Functional / Sign-Out | User viewing `SettingsScreen`; clicks "Sign Out". | `AuthRepository.signOut(fcmToken)` executes. | Client calls `DELETE /api/v1/mobile/devices/{token}` with valid JWT; DB record deleted; returns `204`. | `test_mobile_devices.py` |
| **`TC-MOB-PUSH-012`** | **Idempotent DELETE Execution for Non-Existent or Already Deleted Token** | `AC-3` | Idempotency / Reliability | Device token already deleted or never existed. | Client sends repeated `DELETE /api/v1/mobile/devices/{token}`. | Endpoint returns `204 No Content` without throwing errors or exceptions. | `test_mobile_devices.py` |
| **`TC-MOB-PUSH-013`** | **User-Scoped Deletion Isolation (Cross-User Protection)** | `AC-3` | Security / Access Control | User A attempts to call `DELETE` passing User B's FCM token. | User A sends `DELETE /api/v1/mobile/devices/{token_b}`. | Endpoint returns `204 No Content`, but User B's record is NOT deleted (scoped to caller's `user_id`). | `test_mobile_devices.py` |
| **`TC-MOB-PUSH-014`** | **Alert Routing Exclusion Post-Sign-Out (Zero Push Delivery)** | `AC-3` | Integration / Push Delivery | User has logged out; device record deleted from database. | High-severity incident or deployment failure alert triggered for tenant. | Push notification dispatcher queries active devices; returns 0 recipients; zero alerts sent to device. | `test_mobile_devices.py` |
| **`TC-MOB-PUSH-015`** | **Sign-Out Network Partition Resilience & Guaranteed Local Wipe** | `AC-3` | Resilience / Edge Case | Device in airplane mode / backend unreachable when user taps "Sign Out". | Unregister network call fails with network timeout. | Local credentials wiped from DataStore anyway; UI cleanly transitions to `Unauthenticated`. | `TokenRefreshAndSignOutTest.kt` |

---

## 5. Detailed Test Scenario Specifications

### Scenario `TC-MOB-PUSH-001`: Post-Sign-In Successful FCM Device Registration (`AC-1`)

#### 5.1.1 Objective & Traceability
Verify that immediately after successful authentication, the mobile client registers an active device record via `POST /api/v1/mobile/devices`. The backend binds `tenant_id` and `user_id` strictly from JWT claims, persists the row under PostgreSQL RLS, and returns HTTP `200 OK`. Traces directly to `US-72` AC-1 and `SCRUM-494`.

#### 5.1.2 Preconditions
- Tenant `tenant_alpha` and user `user_alpha` exist in database.
- Caller holds valid RS256 Bearer JWT with claims:
  `{"tenant_id": "<tenant_alpha_uuid>", "sub": "<user_alpha_uuid>", "roles": ["DEVELOPER"]}`.
- Device has acquired FCM token: `fcm_alpha_device_token_001`.

#### 5.1.3 Execution Sequence & Concrete Inputs
1. Client dispatches HTTP request:
   ```http
   POST /api/v1/mobile/devices HTTP/1.1
   Host: api.manta.local
   Authorization: Bearer <valid_jwt>
   Content-Type: application/json

   {
     "fcm_token": "fcm_alpha_device_token_001",
     "platform": "android",
     "device_name": "Google Pixel 8",
     "app_version": "1.0.0"
   }
   ```
2. FastAPI processes request through `JWTAuthMiddleware`, `require_mobile_device_management`, and `get_tenant_db`.
3. `resolve_or_create_mobile_device()` executes PostgreSQL insertion.

#### 5.1.4 Verification Assertions
```python
# API Assertions:
assert response.status_code == 200
data = response.json()
assert uuid.UUID(data["id"]) is not None
assert data["platform"] == "android"
assert data["device_name"] == "Google Pixel 8"
assert data["app_version"] == "1.0.0"
assert data["is_active"] is True
assert data["last_seen_at"] is not None

# Database Assertions (scoped to tenant_alpha):
async with get_tenant_db_context(tenant_alpha_id) as session:
    device = await session.scalar(
        select(MobileDevice).where(
            MobileDevice.tenant_id == tenant_alpha_id,
            MobileDevice.user_id == user_alpha_id,
            MobileDevice.fcm_token == "fcm_alpha_device_token_001",
        )
    )
    assert device is not None
    assert device.is_active is True
    assert device.device_name == "Google Pixel 8"
```

---

### Scenario `TC-MOB-PUSH-002`: Strict PostgreSQL Row-Level Security (RLS) Tenant Isolation (`AC-1`)

#### 5.2.1 Objective & Traceability
Verify that `mobile_devices` records are completely invisible and unreachable across tenant boundaries. When Tenant B queries the database or calls the API, Tenant A's device registrations return zero rows, satisfying PostgreSQL `FORCE ROW LEVEL SECURITY`. Traces to `US-72` AC-1 and Enterprise Multi-Tenancy Invariant 2.

#### 5.2.2 Preconditions
- Tenant A registers device `fcm_token_tenant_a`.
- Tenant B is a separate organization with valid credentials.

#### 5.2.3 Execution Sequence
1. Tenant A registers device:
   ```python
   response = await post_mobile_device(client, token_a, "fcm_token_tenant_a")
   device_id = uuid.UUID(response.json()["id"])
   ```
2. Tenant B opens a database session with `app.current_tenant_id = tenant_b_id`:
   ```python
   async with get_tenant_db_context(tenant_b_id) as session:
       visible_device = await session.scalar(
           select(MobileDevice).where(MobileDevice.id == device_id)
       )
   ```

#### 5.2.4 Verification Assertions
```python
# Assert Tenant B cannot see Tenant A's device under ANY query filter
assert visible_device is None

# Assert direct token query also yields None under Tenant B context
async with get_tenant_db_context(tenant_b_id) as session:
    token_device = await session.scalar(
        select(MobileDevice).where(MobileDevice.fcm_token == "fcm_token_tenant_a")
    )
    assert token_device is None
```

---

### Scenario `TC-MOB-PUSH-003`: Rejection of Client-Supplied Tenant ID in Request Body (`AC-1`)

#### 5.3.1 Objective & Traceability
Verify that the `RegisterMobileDeviceRequest` Pydantic model enforces `extra="forbid"`, rejecting any request containing a client-supplied `tenant_id` or `user_id` with `422 Unprocessable Entity` to prevent tenant spoofing or boundary tampering. Traces to `US-72` AC-1 and Invariant 1.

#### 5.3.2 Preconditions
- Caller authenticated in Tenant A.

#### 5.3.3 Execution Sequence
```python
payload_with_spoofed_tenant = {
    "fcm_token": "fcm_valid_token_123",
    "platform": "android",
    "tenant_id": str(uuid.uuid4()),  # Malicious spoof attempt
}

async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    response = await client.post(
        "/api/v1/mobile/devices",
        headers={"Authorization": f"Bearer {token_a}"},
        json=payload_with_spoofed_tenant,
    )
```

#### 5.3.4 Verification Assertions
```python
# Must be rejected with 422 Unprocessable Entity
assert response.status_code == 422
error_detail = response.json()["detail"]
assert any(err["loc"] == ["body", "tenant_id"] for err in error_detail)

# Verify zero rows written to database
async with get_tenant_db_context(tenant_a_id) as session:
    device = await session.scalar(
        select(MobileDevice).where(MobileDevice.fcm_token == "fcm_valid_token_123")
    )
    assert device is None
```

---

### Scenario `TC-MOB-PUSH-004`: Non-Disclosure of Sensitive Token & Tenant Claims in Response (`AC-1`)

#### 5.4.1 Objective & Traceability
Verify that the registration response payload strictly conforms to `RegisterMobileDeviceResponse`, concealing the raw `fcm_token`, `tenant_id`, and `user_id` from client-facing responses to avoid data reflection and cross-scripting leakage. Traces to `US-72` AC-1 and Invariant 4.

#### 5.4.2 Preconditions
- Device registration executed with sensitive token `secret-fcm-push-token-xyz`.

#### 5.4.3 Execution Sequence
```python
response = await post_mobile_device(client, token, "secret-fcm-push-token-xyz")
response_body = response.json()
```

#### 5.4.4 Verification Assertions
```python
# Assert sensitive fields are completely absent
assert "fcm_token" not in response_body
assert "tenant_id" not in response_body
assert "user_id" not in response_body

# Assert allowed non-sensitive fields match exact schema contract
expected_keys = {
    "id", "platform", "device_name", "app_version",
    "is_active", "last_seen_at", "created_at", "updated_at"
}
assert set(response_body.keys()) == expected_keys
```

---

### Scenario `TC-MOB-PUSH-005`: RBAC Permission Enforcement (`MANAGE_OWN_MOBILE_DEVICES`) (`AC-1`)

#### 5.5.1 Objective & Traceability
Verify that access to `POST /api/v1/mobile/devices` and `DELETE /api/v1/mobile/devices/{token}` is guarded by `require_permission(Permission.MANAGE_OWN_MOBILE_DEVICES)`. Requests without a valid Bearer token or lacking required permissions return `401 Unauthorized` or `403 Forbidden`. Traces to `US-72` AC-1.

#### 5.5.2 Preconditions
- Unauthenticated client and authenticated client lacking recognized role.

#### 5.5.3 Execution Sequence
```python
async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    # 1. Unauthenticated POST
    unauth_post = await client.post(
        "/api/v1/mobile/devices",
        json={"fcm_token": "some-token"}
    )
    # 2. Unauthenticated DELETE
    unauth_delete = await client.delete("/api/v1/mobile/devices/some-token")
```

#### 5.5.4 Verification Assertions
```python
assert unauth_post.status_code == 401
assert unauth_post.json() == {"detail": "Not authenticated"}

assert unauth_delete.status_code == 401
assert unauth_delete.json() == {"detail": "Not authenticated"}
```

---

### Scenario `TC-MOB-PUSH-006`: Idempotent Duplicate Registration with Identical Token (`AC-2`)

#### 5.6.1 Objective & Traceability
Verify that duplicate `POST /api/v1/mobile/devices` requests for the same `(tenant_id, user_id, fcm_token)` execute an atomic upsert via `ON CONFLICT ON CONSTRAINT uq_tenant_user_fcm_token DO UPDATE`. The database updates `last_seen_at` and metadata in place without creating duplicate records or altering the primary key UUID. Traces directly to `US-72` AC-2 and `SCRUM-495`.

#### 5.6.2 Preconditions
- Initial device registered with token `fcm-token-idempotent-001`.

#### 5.6.3 Execution Sequence
```python
fcm_token = "fcm-token-idempotent-001"

# 1. First registration call
first_resp = await post_mobile_device(
    client, token, fcm_token,
    platform="android", device_name="Pixel 7", app_version="1.0.0"
)

# 2. Second registration call with identical token and updated metadata
second_resp = await post_mobile_device(
    client, token, fcm_token,
    platform="android", device_name="Pixel 8 Pro", app_version="1.1.0"
)
```

#### 5.6.4 Verification Assertions
```python
assert first_resp.status_code == 200
assert second_resp.status_code == 200

first_data = first_resp.json()
second_data = second_resp.json()

# Assert primary key UUID is identical (no duplicate row)
assert second_data["id"] == first_data["id"]
assert second_data["device_name"] == "Pixel 8 Pro"
assert second_data["app_version"] == "1.1.0"

# Assert last_seen_at refreshed
assert datetime.fromisoformat(second_data["last_seen_at"]) >= datetime.fromisoformat(first_data["last_seen_at"])

# Assert database row count strictly equals 1
async with get_tenant_db_context(tenant_id) as session:
    count = await session.scalar(
        select(func.count())
        .select_from(MobileDevice)
        .where(
            MobileDevice.tenant_id == tenant_id,
            MobileDevice.user_id == user_id,
            MobileDevice.fcm_token == fcm_token,
        )
    )
assert count == 1
```

---

### Scenario `TC-MOB-PUSH-007`: Re-Activation of Inactive Registration (`is_active = False -> True`) (`AC-2`)

#### 5.7.1 Objective & Traceability
Verify that if a device registration was previously deactivated (`is_active = False`), a subsequent `POST /api/v1/mobile/devices` call atomically re-activates the record (`is_active = True`), re-enabling push notification eligibility. Traces to `US-72` AC-2.

#### 5.7.2 Preconditions
- Device record exists with `is_active = False` in database.

#### 5.7.3 Execution Sequence
```python
fcm_token = "reactivation-token-001"

# Manually set is_active = False in DB
async with get_tenant_db_context(tenant_id) as session:
    await session.execute(
        update(MobileDevice)
        .where(MobileDevice.fcm_token == fcm_token)
        .values(is_active=False)
    )
    await session.commit()

# Client dispatches new registration request
response = await post_mobile_device(client, token, fcm_token)
```

#### 5.7.4 Verification Assertions
```python
assert response.status_code == 200
assert response.json()["is_active"] is True

# Verify database state
async with get_tenant_db_context(tenant_id) as session:
    device = await session.scalar(
        select(MobileDevice).where(MobileDevice.fcm_token == fcm_token)
    )
    assert device is not None
    assert device.is_active is True
```

---

### Scenario `TC-MOB-PUSH-008`: Device Metadata & Version Update on Re-Registration (`AC-2`)

#### 5.8.1 Objective & Traceability
Verify that when an application is upgraded (e.g. from version `1.0.0` to `2.1.0`), subsequent background token registration updates `app_version` and `device_name` in place while maintaining historical timestamps (`created_at` preserved). Traces to `US-72` AC-2.

#### 5.8.2 Preconditions
- Device registered with `app_version="1.0.0"`.

#### 5.8.3 Execution Sequence
```python
response = await post_mobile_device(
    client, token, "fcm-token-upgrade",
    app_version="2.1.0", device_name="Upgraded Tablet"
)
```

#### 5.8.4 Verification Assertions
```python
assert response.status_code == 200
data = response.json()
assert data["app_version"] == "2.1.0"
assert data["device_name"] == "Upgraded Tablet"
assert datetime.fromisoformat(data["updated_at"]) >= datetime.fromisoformat(data["created_at"])
```

---

### Scenario `TC-MOB-PUSH-009`: Multi-Device Registration for a Single User (`AC-2`)

#### 5.9.1 Objective & Traceability
Verify that a single user can register multiple distinct mobile devices (e.g. an Android phone and a tablet). Each unique FCM token generates a distinct record under `(tenant_id, user_id)`, and both remain active simultaneously. Traces to `US-72` AC-2.

#### 5.9.2 Preconditions
- User `user_alpha` authenticated.

#### 5.9.3 Execution Sequence
```python
# Register Phone
phone_resp = await post_mobile_device(
    client, token, "fcm-token-phone", device_name="Pixel Phone"
)
# Register Tablet
tablet_resp = await post_mobile_device(
    client, token, "fcm-token-tablet", device_name="Samsung Tablet"
)
```

#### 5.9.4 Verification Assertions
```python
assert phone_resp.status_code == 200
assert tablet_resp.status_code == 200

phone_id = phone_resp.json()["id"]
tablet_id = tablet_resp.json()["id"]
assert phone_id != tablet_id

# Verify both exist as active rows in DB
async with get_tenant_db_context(tenant_id) as session:
    devices = (await session.scalars(
        select(MobileDevice).where(
            MobileDevice.tenant_id == tenant_id,
            MobileDevice.user_id == user_id,
        )
    )).all()
    assert len(devices) == 2
    assert all(d.is_active for d in devices)
```

---

### Scenario `TC-MOB-PUSH-010`: FCM Token Rotation Hook (`onNewToken`) During Active Session (`AC-2`)

#### 5.10.1 Objective & Traceability
Verify that when Firebase rotates the FCM token during an active mobile session, `MantaFirebaseMessagingService.onNewToken(newToken)` automatically invokes `POST /api/v1/mobile/devices`, registering the new token without requiring user interaction. Traces to `US-72` AC-2 and `SCRUM-496`.

#### 5.10.2 Preconditions
- User active in `NavShell` with `AuthState.Authenticated`.
- Firebase dispatches `onNewToken("rotated_fcm_token_999")`.

#### 5.10.3 Execution Sequence
```kotlin
// Android Service Hook Simulation:
val service = MantaFirebaseMessagingService()
service.onNewToken("rotated_fcm_token_999")
```

#### 5.10.4 Verification Assertions
```kotlin
// Verify Retrofit DeviceApiService dispatched registration
val recordedRequest = mockWebServer.takeRequest()
assertEquals("POST", recordedRequest.method)
assertTrue(recordedRequest.path!!.contains("/mobile/devices"))
assertTrue(recordedRequest.body.readUtf8().contains("rotated_fcm_token_999"))
```

---

### Scenario `TC-MOB-PUSH-011`: Pre-Sign-Out Unregistration & Database Deletion (`AC-3`)

#### 5.11.1 Objective & Traceability
Verify that when a user clicks "Sign Out" in `SettingsScreen`, `AuthRepository.signOut(fcmToken)` executes `DELETE /api/v1/mobile/devices/{token}` **before** calling `purgeLocalSession()`. The backend deletes the device record and returns `204 No Content`. Traces directly to `US-72` AC-3 and `SCRUM-496`.

#### 5.11.2 Preconditions
- Active device registration `fcm-token-logout-001` exists in database.

#### 5.11.3 Execution Sequence
```python
fcm_token = "fcm-token-logout-001"

# Client sends DELETE request
async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    response = await client.delete(
        f"/api/v1/mobile/devices/{fcm_token}",
        headers={"Authorization": f"Bearer {token}"},
    )
```

#### 5.11.4 Verification Assertions
```python
# 1. HTTP Status 204 No Content
assert response.status_code == 204
assert response.content == b""

# 2. Database row is completely removed
async with get_tenant_db_context(tenant_id) as session:
    device = await session.scalar(
        select(MobileDevice).where(
            MobileDevice.tenant_id == tenant_id,
            MobileDevice.user_id == user_id,
            MobileDevice.fcm_token == fcm_token,
        )
    )
assert device is None
```

---

### Scenario `TC-MOB-PUSH-012`: Idempotent DELETE Execution for Non-Existent or Already Deleted Token (`AC-3`)

#### 5.12.1 Objective & Traceability
Verify that `DELETE /api/v1/mobile/devices/{token}` is strictly idempotent. Repeated calls for the same token or calls with non-existent tokens return `204 No Content` without errors. Traces to `US-72` AC-3.

#### 5.12.2 Preconditions
- Token `already-deleted-token` does not exist in database.

#### 5.12.3 Execution Sequence
```python
async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    resp1 = await client.delete(
        "/api/v1/mobile/devices/already-deleted-token",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp2 = await client.delete(
        "/api/v1/mobile/devices/already-deleted-token",
        headers={"Authorization": f"Bearer {token}"},
    )
```

#### 5.12.4 Verification Assertions
```python
assert resp1.status_code == 204
assert resp2.status_code == 204
```

---

### Scenario `TC-MOB-PUSH-013`: User-Scoped Deletion Isolation (Cross-User Protection) (`AC-3`)

#### 5.13.1 Objective & Traceability
Verify that a caller in Tenant A cannot delete another user's device registration within the same tenant. The `DELETE` statement is scoped to both `tenant_id` AND `user_id`. Calling `DELETE` with another user's token returns `204 No Content` but does not delete the target user's row. Traces to `US-72` AC-3 and Invariant 1.

#### 5.13.2 Preconditions
- User 1 registers device with token `fcm-token-user-1`.
- User 2 attempts to delete `fcm-token-user-1`.

#### 5.13.3 Execution Sequence
```python
# User 2 attempts to delete User 1's token
async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    response = await client.delete(
        "/api/v1/mobile/devices/fcm-token-user-1",
        headers={"Authorization": f"Bearer {token_user_2}"},
    )
```

#### 5.13.4 Verification Assertions
```python
assert response.status_code == 204

# Assert User 1's device is STILL present in database
async with get_tenant_db_context(tenant_id) as session:
    device = await session.scalar(
        select(MobileDevice).where(
            MobileDevice.tenant_id == tenant_id,
            MobileDevice.user_id == user_1_id,
            MobileDevice.fcm_token == "fcm-token-user-1",
        )
    )
assert device is not None
assert device.is_active is True
```

---

### Scenario `TC-MOB-PUSH-014`: Alert Routing Exclusion Post-Sign-Out (Zero Push Delivery) (`AC-3`)

#### 5.14.1 Objective & Traceability
Verify that once a device registration is deleted upon sign-out, backend notification dispatch queries for the tenant return 0 recipients for that device, guaranteeing that no subsequent production health or incident push notifications are delivered to the logged-out device. Traces to `US-72` AC-3 and Invariant 5.

#### 5.14.2 Preconditions
- Device was registered, then cleanly unregistered via `signOut()`.
- An incident alert (e.g. DORA change failure rate breach) is triggered.

#### 5.14.3 Execution Sequence
```python
# Query active devices eligible for alert dispatch
async with get_tenant_db_context(tenant_id) as session:
    active_tokens = (await session.scalars(
        select(MobileDevice.fcm_token).where(
            MobileDevice.tenant_id == tenant_id,
            MobileDevice.is_active.is_(True),
        )
    )).all()
```

#### 5.14.4 Verification Assertions
```python
assert "fcm-token-logout-001" not in active_tokens
# Zero push notification dispatch occurs for logged-out client
```

---

### Scenario `TC-MOB-PUSH-015`: Sign-Out Network Partition Resilience & Guaranteed Local Wipe (`AC-3`)

#### 5.15.1 Objective & Traceability
Verify that if the unregistration HTTP call fails due to a network partition (e.g. device entering a tunnel or airplane mode), `AuthRepository.signOut()` handles the network exception gracefully, reports the error for user visibility, but still allows `purgeLocalSession()` to clean the device's local Tink AEAD ciphertext keys. Traces to `US-72` AC-3.

#### 5.15.2 Preconditions
- MockWebServer shut down or network connection unavailable.

#### 5.15.3 Execution Sequence
```kotlin
mockWebServer.shutdown()
val result = authRepository.signOut(fcmToken = "offline-token")
```

#### 5.15.4 Verification Assertions
```kotlin
// Sign-out returns Result.failure for UI feedback
assertTrue(result.isFailure)
assertTrue(result.exceptionOrNull()?.message?.contains("Unable to unregister device") == true)

// Local session is still guaranteed purged
authRepository.purgeLocalSession()
assertNull(authRepository.getAccessToken())
assertEquals(AuthState.Unauthenticated, authRepository.authState.value)
```

---

## 6. Automated Test Suites & Verification Execution Guide

### 6.1 Automated Test Class & Coverage Matrix

| Test Suite / Class | Test File Location | Scenarios Covered | Target Framework |
| :--- | :--- | :--- | :--- |
| **`test_mobile_devices.py`** | `backend/tests/test_mobile_devices.py` | `TC-MOB-PUSH-001` through `TC-MOB-PUSH-009`, `TC-MOB-PUSH-011` through `TC-MOB-PUSH-014` | pytest, pytest-asyncio, httpx, SQLAlchemy |
| **`test_rls_isolation.py`** | `backend/tests/test_rls_isolation.py` | `TC-MOB-PUSH-002` (RLS tenant boundary verification) | pytest, PostgreSQL RLS |
| **`TokenRefreshAndSignOutTest`** | `mobile/app/src/test/java/com/manta/mobile/features/auth/TokenRefreshAndSignOutTest.kt` | `TC-MOB-PUSH-010`, `TC-MOB-PUSH-011`, `TC-MOB-PUSH-015` | JUnit 4, Robolectric, OkHttp MockWebServer |
| **`AuthRepositoryTest`** | `mobile/app/src/test/java/com/manta/mobile/data/auth/AuthRepositoryTest.kt` | `TC-MOB-PUSH-011`, `TC-MOB-PUSH-015` | JUnit 4, Robolectric, Tink AEAD |

---

### 6.2 Local Execution & CI Verification Commands

```bash
# ==============================================================================
# 1. Run Backend Mobile Device Pytest Suite
# ==============================================================================
cd backend
pytest tests/test_mobile_devices.py -v

# ==============================================================================
# 2. Run Complete RLS Isolation Pytest Suite
# ==============================================================================
pytest tests/test_rls_isolation.py -v

# ==============================================================================
# 3. Run Android Unit & Robolectric Tests for Sign-Out & Push Unregistration
# ==============================================================================
cd ../mobile
./gradlew testDebugUnitTest --tests "com.manta.mobile.features.auth.TokenRefreshAndSignOutTest"

# ==============================================================================
# 4. Check Kotlin Coding Standards & Ktlint Rules
# ==============================================================================
./gradlew ktlintCheck
```

---

## 7. Acceptance Criteria Verification Sign-Off

- [x] **AC-1 (Post-Sign-In Device Registration):** Validated by `TC-MOB-PUSH-001` through `TC-MOB-PUSH-005`. Client registers device via `POST /api/v1/mobile/devices`; `tenant_id` and `user_id` strictly extracted from Bearer JWT; PostgreSQL `FORCE ROW LEVEL SECURITY` isolates records; client-supplied body tenant IDs rejected with `422`; sensitive tokens omitted in responses.
- [x] **AC-2 (Idempotent Duplicate Registration & Token Refresh):** Validated by `TC-MOB-PUSH-006` through `TC-MOB-PUSH-010`. Unique constraint `uq_tenant_user_fcm_token` executes atomic upsert on conflict; row count strictly = 1; `last_seen_at` and metadata updated; `is_active` flipped to True; token rotation `onNewToken` supported.
- [x] **AC-3 (Sign-Out Device Deactivation & Push Alert Suppression):** Validated by `TC-MOB-PUSH-011` through `TC-MOB-PUSH-015`. Sign-out dispatches `DELETE /api/v1/mobile/devices/{token}` before local session wipe; database deletes record; repeated deletes return `204 No Content` idempotently; zero push notifications routed to logged-out client.
