# US-81: Mobile Background Token Renewal, Revoked Refresh Token Handling and Clean Sign-Out Test Scenarios

**Task:** `SCRUM-492` (`Task 81.3`: Create test scenarios for background token renewal, revoked refresh token prompt and clean mobile sign-out)  
**Parent User Story:** `SCRUM-374` (`US-81`: Mobile Background Session Renewal, Token Interception & Clean Sign-Out)  
**Related Tickets:**  
- `SCRUM-490` / `Task 81.1`: Refresh token interceptor and silent renewal implementation (`TokenAuthenticator`, `PkceAuthManager.refreshTokens`, `RetrofitClientFactory`, Mutex concurrency lock)  
- `SCRUM-491` / `Task 81.2`: Local session lifecycle, FCM unregistration & clean sign-out implementation (`AuthRepository.signOut`, `AuthRepository.purgeLocalSession`, `DeviceApiService.unregisterDevice`, `SettingsScreen`)  
- `SCRUM-492` / `Task 81.3`: Create test scenarios for background token renewal, revoked refresh token prompt and clean mobile sign-out (this document)  
- `SCRUM-493` / `Task 81.4`: Automated unit, integration, and Robolectric test suites (`TokenRefreshAndSignOutTest.kt`, `SettingsScreenTest.kt`, `RetrofitClientFactoryTest.kt`)  
- `SCRUM-485` / `Task 38.1`: Core Android security infrastructure (`EncryptedStorage`, Tink AEAD `AES256_GCM`, Android Keystore master key `android-keystore://manta_master_key`)  
- `SCRUM-486` / `Task 38.2`: Mobile authentication activity and screens (`SignInScreen`, `MainActivity`, OIDC deep linking)  
- `SCRUM-487` / `Task 38.3`: Biometric authentication and session gate (`BiometricPromptManager`, `BiometricUnlockScreen`)  
- `SCRUM-488` / `Task 38.4`: Mobile OIDC sign-in and biometric test scenarios (`test_us38_mobile_biometric_auth_scenarios.md`)  
- `SCRUM-494` / `Task 72.1`: Mobile FCM device registration API contract and schema (`mobile-fcm-device-registration-contract.md`)  
- `SCRUM-496` / `SCRUM-498`: FCM device token lifecycle hooks and unregistration on sign-out  
- `SCRUM-240` / `SCRUM-241` / `SCRUM-277` / `SCRUM-278`: OIDC Keycloak realm, token rotation, and RS256 Bearer authorization  

**Target Platform & Framework:** Android (minSdk 27, compileSdk/targetSdk 34/37), Jetpack Compose (Material 3), Jetpack DataStore Preferences (`manta_encrypted_prefs.preferences_pb`), Google Tink AEAD (`AES256_GCM`), OkHttp 4.12+ (`Authenticator`, `Interceptor`), Retrofit 2.11+, Kotlin Coroutines & Mutex (`kotlinx.coroutines.sync.Mutex`)  
**Target Implementation Modules:**  
- [`mobile/app/src/main/java/com/manta/mobile/core/network/TokenAuthenticator.kt`](../mobile/app/src/main/java/com/manta/mobile/core/network/TokenAuthenticator.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/core/network/RetrofitClientFactory.kt`](../mobile/app/src/main/java/com/manta/mobile/core/network/RetrofitClientFactory.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/core/network/AuthApiService.kt`](../mobile/app/src/main/java/com/manta/mobile/core/network/AuthApiService.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/core/network/DeviceApiService.kt`](../mobile/app/src/main/java/com/manta/mobile/core/network/DeviceApiService.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/features/auth/AuthRepository.kt`](../mobile/app/src/main/java/com/manta/mobile/features/auth/AuthRepository.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/data/auth/AuthState.kt`](../mobile/app/src/main/java/com/manta/mobile/data/auth/AuthState.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/data/auth/PkceAuthManager.kt`](../mobile/app/src/main/java/com/manta/mobile/data/auth/PkceAuthManager.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/core/security/EncryptedStorage.kt`](../mobile/app/src/main/java/com/manta/mobile/core/security/EncryptedStorage.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/features/settings/SettingsScreen.kt`](../mobile/app/src/main/java/com/manta/mobile/features/settings/SettingsScreen.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/ui/navigation/NavShell.kt`](../mobile/app/src/main/java/com/manta/mobile/ui/navigation/NavShell.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/MainActivity.kt`](../mobile/app/src/main/java/com/manta/mobile/MainActivity.kt)  
**Authoritative Automated Test Suites:**  
- [`mobile/app/src/test/java/com/manta/mobile/features/auth/TokenRefreshAndSignOutTest.kt`](../mobile/app/src/test/java/com/manta/mobile/features/auth/TokenRefreshAndSignOutTest.kt)  
- [`mobile/app/src/test/java/com/manta/mobile/features/settings/SettingsScreenTest.kt`](../mobile/app/src/test/java/com/manta/mobile/features/settings/SettingsScreenTest.kt)  
- [`mobile/app/src/test/java/com/manta/mobile/data/auth/AuthRepositoryTest.kt`](../mobile/app/src/test/java/com/manta/mobile/data/auth/AuthRepositoryTest.kt)  
- [`mobile/app/src/test/java/com/manta/mobile/NavigationRouteTest.kt`](../mobile/app/src/test/java/com/manta/mobile/NavigationRouteTest.kt)  
- [`backend/tests/test_jwt_rotation_refresh.py`](../backend/tests/test_jwt_rotation_refresh.py)  
**Specification Location in Test Suite:** [`backend/tests/scenarios/test_us81_mobile_session_refresh_scenarios.md`](../backend/tests/scenarios/test_us81_mobile_session_refresh_scenarios.md)  
**Assignee:** Matvii Yeromenko  
**Category:** Testing - Design  
**Estimate:** 0.25 Story Points (0h 30m Original Estimate)  
**Status:** Approved / Verification Baseline Established  

---

## 1. Executive Summary & Objective

### 1.1 Objective

Design and formalize the authoritative QA test scenarios and cryptographic/lifecycle verification contracts for **Mobile Background Session Renewal, Token Interception, Terminal Revocation Handling, and Clean User Sign-Out** (`US-81`).

In modern enterprise mobile applications, short-lived JWT access tokens (e.g. 5–15 minutes) are mandatory to mitigate authorization leakage and maintain zero-trust tenant boundaries. However, access token expiry must **never** disrupt active user workflows or require manual credentials re-entry while a valid refresh token exists. Conversely, when a refresh token expires or is explicitly revoked (via IdP admin session termination, tenant departure, or password reset), the mobile client must immediately and cleanly wipe all local ciphertext credentials and return to a safe sign-in gate. Finally, user-initiated logout must comprehensively de-register push notification bindings, revoke server-side SSO sessions, purge hardware-encrypted tokens from flash storage, and completely dismantle the navigation backstack to prevent back-button navigation into cached tenant state.

This specification establishes concrete, repeatable, and automated test scenarios validating the entire lifecycle across Android, OkHttp, Retrofit, Jetpack Compose, and Keycloak/FastAPI backend components.

### 1.2 Scope & Acceptance Criteria Traceability

The test cases in this document provide complete, verifiable coverage for the three core acceptance criteria of `US-81`:

1. **Silent Background Rotation (`AC-1`):**  
   When a short-lived access token expires and an API request fails with `401 Unauthorized`, OkHttp's `TokenAuthenticator` intercepts the response, acquires a coroutine mutex lock to serialize concurrent requests (preventing thundering herd / token refresh stampedes), issues a token refresh request to the IdP (`/protocol/openid-connect/token` or `/api/v1/auth/refresh`), encrypts the new access and refresh tokens into Jetpack DataStore via Google Tink AEAD `AES256_GCM`, and transparently replays the original failed request with the new Bearer token without dropping the user's UI context. Waiting concurrent requests detect that the token was already refreshed, replaying immediately without redundant network calls.

2. **Revoked/Expired Refresh Token Handling (`AC-2`):**  
   When a refresh token is expired, revoked on Keycloak, or rejected with `401 Unauthorized` / `400 Bad Request` during token rotation, the authenticator detects terminal failure, halts retry loops via `priorResponse` guards, invokes `AuthRepository.purgeLocalSession(reason)`, purges all Tink-encrypted tokens and user claims from Jetpack DataStore, transitions the application state to `AuthState.ReauthRequired`, and prompts the user on `SignInScreen` with a clear message: `"Session expired. Please sign in again."`

3. **User-Initiated Clean Sign-Out (`AC-3`):**  
   When a user clicks "Sign Out" in the mobile settings view (`SettingsScreen`), the client coordinates clean session destruction:
   - Unregisters the device's FCM push notification token (`DELETE /api/v1/mobile/devices/{token}`) to ensure no push notifications are received post-logout.
   - Dispatches an OIDC RP-Initiated session revocation request to Keycloak (`/protocol/openid-connect/logout`) with `client_id=manta-mobile`, `refresh_token`, and `id_token_hint`.
   - Purges all Tink AEAD encrypted keys in Jetpack DataStore and clears in-memory session objects.
   - Resets the Compose navigation backstack completely, unmounting `NavShell` and transitioning `_authState` to `AuthState.Unauthenticated`, rendering `SignInScreen`. System back button press terminates the app rather than reopening protected screens.

### 1.3 Threat Model & Failure Modes Addressed

```
+-------------------------------------------------------------------------------------------------------------------+
|                                  THREAT VECTOR & MITIGATION MATRIX (US-81)                                        |
+------------------------------------+---------------------------------------+--------------------------------------+
| Threat / Vulnerability             | Root Cause / Impact                   | Architectural Defense / Test Check   |
+------------------------------------+---------------------------------------+--------------------------------------+
| 1. Thundering Herd Refresh Race     | Multiple concurrent API calls (e.g.  | kotlinx.coroutines.sync.Mutex lock   |
|                                    | 5 dashboard queries) get 401, causing | in TokenAuthenticator; secondary     |
|                                    | 5 simultaneous refresh requests and   | requests verify latestToken != failed|
|                                    | invalidating rotated refresh tokens.  | and replay without refresh calls.    |
+------------------------------------+---------------------------------------+--------------------------------------+
| 2. Infinite Authenticator Loop     | Server rejects refreshed token with   | OkHttp priorResponse != null check   |
|                                    | 401 repeatedly, causing infinite      | immediately halts retry loop and     |
|                                    | network retry storm and battery drain.| triggers onRefreshFailed terminal.   |
+------------------------------------+---------------------------------------+--------------------------------------+
| 3. Zombie Push Notifications       | User logs out, but FCM token remains  | AuthRepository.signOut invokes       |
|                                    | active on backend, routing sensitive  | DeviceApiService.unregisterDevice    |
|                                    | tenant alerts to an untrusted device. | BEFORE destroying local session.     |
+------------------------------------+---------------------------------------+--------------------------------------+
| 4. Navigation Backstack Residuals  | User logs out, but pressing Android   | Compose NavShell unmounted entirely; |
|                                    | back button pops back into protected  | NavHost destroyed; AuthState resets  |
|                                    | dashboard with cached profile data.   | root to SignInScreen (zero history). |
+------------------------------------+---------------------------------------+--------------------------------------+
| 5. Plaintext Residue on Flash Disk | Tokens saved in plain SharedPreferences| Google Tink AEAD AES-256-GCM backed  |
|                                    | exposed via physical chip dump.       | by Android Keystore in DataStore.    |
+------------------------------------+---------------------------------------+--------------------------------------+
```

---

## 2. Core Architectural Invariants & Security Formulations

```
+-------------------------------------------------------------------------------------------------------------------+
|                           MOBILE SESSION RENEWAL & CLEAN SIGN-OUT ARCHITECTURE                                    |
+-------------------------------------+-------------------------------------+---------------------------------------+
| 1. Single-Flight Concurrency Mutex  | 2. OkHttp Loop Termination Guard    | 3. Hardware-Backed Tink Persistence   |
|    (Thundering Herd Prevention)     |    (Zero Infinite Retry Storms)     |    (Zero Plaintext Tokens on Disk)    |
+-------------------------------------+-------------------------------------+---------------------------------------+
| * Mutex.withLock serializes 401s    | * if (response.priorResponse != null)| * EncryptedStorage: Tink AES-256-GCM  |
| * Request 1 executes refreshTokens()|     abort retry loop, return null   | * Keyset in manta_tink_prefs          |
| * Requests 2..N observe:            | * Guarantees maximum of 1 replay per| * Master key in Android Keystore      |
|   latestToken != failedToken        |   HTTP request lifecycle            | * Tokens stored as Base64 ciphertext: |
| * Replay requests 2..N immediately  | * Prevents DDoS and battery leakage |   auth_access_token, auth_refresh_token|
+-------------------------------------+-------------------------------------+---------------------------------------+
                                                         |
                                                         v
+-------------------------------------------------------------------------------------------------------------------+
|                              MOBILE AUTHENTICATION STATE & ROUTE GUARD BOUNDARIES                                 |
+----------------------------------------------------+--------------------------------------------------------------+
| 4. Terminal ReauthRequired State Gate              | 5. Complete Sign-Out & Backstack Destruction                 |
+----------------------------------------------------+--------------------------------------------------------------+
| * 401/403 from /token or /auth/refresh triggers:   | * OutlinedDangerButton ("Sign Out") in SettingsScreen        |
|   AuthRepository.purgeLocalSession(reason)         | * Step 1: DELETE /api/v1/mobile/devices/{fcm_token}           |
| * Deletes all 6 encrypted keys from DataStore      | * Step 2: POST Keycloak OIDC logout with refresh_token       |
| * AuthState -> ReauthRequired("Session expired...")| * Step 3: purgeLocalSession() wipes all keys in DataStore   |
| * NavShell unmounted; SignInScreen displayed       | * Step 4: AuthState -> Unauthenticated; NavHost destroyed    |
+----------------------------------------------------+--------------------------------------------------------------+
```

### Invariant 1: Single-Flight Mutex Concurrency & Anti-Stampede Invariant

For any set of $N$ concurrent HTTP requests $\{R_1, R_2, \dots, R_N\}$ dispatched with a stale access token $T_{\text{stale}}$ that encounter `401 Unauthorized` simultaneously:
1. All $N$ calls enter `TokenAuthenticator.authenticate()`.
2. Execution of the token renewal network call is serialized via `kotlinx.coroutines.sync.Mutex`:
   $$\text{Mutex}.\text{withLock} \implies \text{Exactly 1 network refresh call executed}$$
3. Thread 1 acquires the lock, observes $T_{\text{latest}} == T_{\text{stale}}$, executes `PkceAuthManager.refreshTokens(currentRefreshToken)`, acquires new credentials $T_{\text{new}}$, updates storage via `onTokenRefreshed`, and replays $R_1$ with $T_{\text{new}}$.
4. Threads $2 \dots N$ acquire the lock sequentially, evaluate:
   $$T_{\text{latest}} \ne T_{\text{failed}} \quad (\text{where } T_{\text{failed}} = T_{\text{stale}})$$
5. Threads $2 \dots N$ **skip the network refresh call entirely** and immediately rewrite their request headers:
   $$\text{Header}_{\text{auth}} \leftarrow \text{"Bearer "} + T_{\text{new}}$$
6. Total network refresh calls for $N$ concurrent `401` responses strictly equals $1$.

---

### Invariant 2: OkHttp `priorResponse` Infinite Retry-Loop Guard

Under no circumstances may an authentication failure loop indefinitely:
1. OkHttp records previous authentication attempts on `Response.priorResponse`.
2. `TokenAuthenticator.authenticate()` inspects:
   $$\text{response}.\text{priorResponse} \ne \text{null} \implies \text{return null}$$
3. When `authenticate()` returns `null`, OkHttp aborts retry execution and delivers the `401 Unauthorized` response to the caller.
4. The authenticator dispatches `onRefreshFailed("Backend rejected refreshed token")`, triggering session eviction.
5. The maximum number of retry attempts for any single request is bounded by $1$:
   $$\text{Attempts}(R) \le 2 \quad (\text{Original attempt } + \text{at most } 1 \text{ replay})$$

---

### Invariant 3: Google Tink AEAD Keystore-Backed At-Rest Non-Plaintext Persistence

For any refreshed or stored token in Jetpack DataStore:
1. Root key material resides strictly in the hardware-isolated Android Keystore (`android-keystore://manta_master_key`).
2. Google Tink AEAD `AES256_GCM` encrypts all credentials prior to serialization:
   $$\text{Ciphertext } C = \text{AES-256-GCM}_{K}(\text{Plaintext } P, \text{AssociatedData } \emptyset)$$
   where $|C| \ge |P| + 28$ bytes (12-byte IV + 16-byte authentication tag).
3. Plaintext tokens, refresh tokens, user emails, and roles **never** appear in the raw protobuf file:
   $$\forall s \in \{T_{\text{access}}, T_{\text{refresh}}, T_{\text{id}}, \text{email}, \text{role}\}: \quad s \not\subset \text{RawBytes}(\text{manta\_encrypted\_prefs.preferences\_pb})$$

---

### Invariant 4: Terminal Session Invalidation & State Machine Transitions

When token refresh fails (due to HTTP `401`/`403`, network termination, or invalid refresh token):
1. `AuthRepository.purgeLocalSession(reason)` atomically deletes all stored keys:
   $$\text{DataStore}.\text{remove}(\{\text{KEY\_ACCESS\_TOKEN}, \text{KEY\_REFRESH\_TOKEN}, \text{KEY\_ID\_TOKEN}, \text{KEY\_USER\_NAME}, \text{KEY\_USER\_EMAIL}, \text{KEY\_PRIMARY\_ROLE}\})$$
2. In-memory state transitions deterministically:
   $$\text{AuthState} \leftarrow \text{AuthState.ReauthRequired}(\text{reason = "Session expired. Please sign in again."})$$
3. In `MainActivity`, Compose observes `AuthState.ReauthRequired`, immediately unmounts `NavShell`, and mounts `SignInScreen` with the session expiration reason displayed.

---

### Invariant 5: Complete Sign-Out, Device Deregistration & Navigation Backstack Annihilation

When a user triggers sign-out from `SettingsScreen`:
1. **Device Deregistration:** If an FCM push token $F$ is registered, `DeviceApiService.unregisterDevice(F)` (`DELETE /api/v1/mobile/devices/{token}`) executes first.
2. **IdP Session Revocation:** RP-Initiated logout request is dispatched to Keycloak:
   $$\text{POST } \text{KEYCLOAK\_URL} + \text{/protocol/openid-connect/logout} \quad [\text{client\_id}=\text{manta-mobile}, \text{refresh\_token}=T_{\text{refresh}}, \text{id\_token\_hint}=T_{\text{id}}]$$
3. **Local Wipe:** `purgeLocalSession()` zeroes all encrypted DataStore keys and memory session references.
4. **Backstack Reset:** `_authState` transitions to `AuthState.Unauthenticated`. Compose replaces `NavShell` with `SignInScreen`. The internal `NavHost` backstack is completely destroyed.
5. Back-button press from `SignInScreen` exits the application; it **cannot** navigate back to `SettingsScreen`, `AlertsScreen`, or `ProjectScreen`.

---

## 3. Mobile Authentication State Machine & Sequence Diagrams

### 3.1 Formal State Machine Diagram

```
                             +-------------------------+
                             |     UNAUTHENTICATED     | <------------------------------------+
                             +-------------------------+                                      |
                                          |                                                   |
                             OIDC PKCE    | Deep-Link Callback                                |
                             Sign-In OK   | Token Exchange                                    |
                                          v                                                   |
                             +-------------------------+                                      |
                             |      AUTHENTICATED      |                                      |
                             |   (NavShell Mounted)    |                                      |
                             +-------------------------+                                      |
                               |                     |                                        |
             API returns 401   |                     | User clicks "Sign Out"                 |
             Unauthorized      v                     v (SettingsScreen)                       |
       +-------------------------------+    +---------------------------------+               |
       |     TOKEN AUTHENTICATOR       |    |       CLEAN SIGN-OUT FLOW       |               |
       |  (Mutex.withLock Acquisition) |    | 1. DELETE /mobile/devices/{fcm} |               |
       +-------------------------------+    | 2. POST Keycloak OIDC /logout   |               |
           |                       |        | 3. purgeLocalSession()          |               |
   latest != failed         latest == failed| 4. Unmount NavShell             |               |
   (Already Refreshed)      (Execute Net)   +---------------------------------+               |
           |                       |                         |                                |
           |            +----------+----------+              +--------------------------------+
           |            |                     |
           |     POST /token 200 OK    POST /token 401/403
           |     (New Access/Refresh)  (Revoked / Expired)
           |            |                     |
           v            v                     v
     +-----------------------+     +-----------------------------------+
     | REPLAY ORIGINAL REQ   |     |       SESSION TERMINATION         |
     | * Update DataStore    |     | * purgeLocalSession(reason)       |
     | * Return 200 to UI    |     | * AuthState -> ReauthRequired     |
     | * NavShell Undisturbed|     | * NavShell Unmounted              |
     +-----------------------+     | * Render SignInScreen + Toast     |
                                   +-----------------------------------+
```

---

### 3.2 Sequence Diagram: AC-1 Silent Background Token Renewal & Request Replay

```
User (Compose UI)          Retrofit Client          TokenAuthenticator            Keycloak / Token API        EncryptedStorage / DataStore
       |                          |                         |                              |                                |
       |-- 1. GET /api/v1/alerts->|                         |                              |                                |
       |   (Bearer Stale Token)   |-- 2. HTTP 401 --------->|                              |                                |
       |                          |                         |-- 3. Mutex.withLock -------->|                                |
       |                          |                         |-- 4. Check latest == failed->|                                |
       |                          |                         |-- 5. Read current refresh -->|                                |
       |                          |                         |<-- 6. Return Refresh Token --|--------------------------------|
       |                          |                         |-- 7. POST /token ----------->|                                |
       |                          |                         |   (grant_type=refresh_token) |                                |
       |                          |                         |<-- 8. 200 OK (New Tokens) ---|                                |
       |                          |                         |-- 9. onTokenRefreshed ------>|                                |
       |                          |                         |   (Save new JWTs)            |-- 10. Tink AEAD Encrypt ------>|
       |                          |                         |                              |-- 11. Write DataStore Prefs -->|
       |                          |                         |                              |   (auth_access_token=Base64(C))|
       |                          |                         |-- 12. Rebuild Request ------>|                                |
       |                          |                         |   (Bearer New Access Token)  |                                |
       |                          |<-- 13. Replayed Request |                              |                                |
       |                          |-- 14. GET /api/v1/alerts (with New Token) ------------>|                                |
       |                          |<-- 15. HTTP 200 OK (Alerts JSON Payload) --------------|                                |
       |<-- 16. Render Alerts UI -|                         |                              |                                |
       |   (Zero interruption)    |                         |                              |                                |
```

---

### 3.3 Sequence Diagram: AC-1 Concurrent 401 Requests & Single-Flight Mutex Serialization

```
Thread 1 (Alerts)      Thread 2 (Projects)     Thread 3 (Actions)        TokenAuthenticator (Mutex)          Keycloak IdP
       |                        |                       |                             |                            |
       |-- 1. 401 Received ---->|                       |                             |                            |
       |                        |-- 2. 401 Received --->|                             |                            |
       |                        |                       |-- 3. 401 Received --------->|                            |
       |-- 4. Mutex.withLock (ACQUIRED) --------------->|                             |                            |
       |                        |-- 5. Mutex.withLock (SUSPENDED / WAITING) ---------->|                            |
       |                        |                       |-- 6. Mutex.withLock (SUSP)->|                            |
       |-- 7. Execute PkceAuthManager.refreshTokens() ------------------------------->|-- 8. POST /token --------->|
       |                                                                              |<-- 9. 200 OK (New Access) -|
       |-- 10. Save new tokens to DataStore ----------------------------------------->|                            |
       |-- 11. Replay Thread 1 with New Token --------------------------------------->|                            |
       |-- 12. Release Mutex -------------------------------------------------------->|                            |
       |                        |-- 13. Mutex ACQUIRED for Thread 2 ----------------->|                            |
       |                        |-- 14. Check: latestToken != failedToken (TRUE!) --->|                            |
       |                        |-- 15. SKIP NETWORK REFRESH CALL ------------------->| (Zero IdP network request) |
       |                        |-- 16. Replay Thread 2 with New Token -------------->|                            |
       |                        |-- 17. Release Mutex -------------------------------->|                            |
       |                        |                       |-- 18. Mutex ACQUIRED for T3 |                            |
       |                        |                       |-- 19. Check: latest != fail |                            |
       |                        |                       |-- 20. SKIP NETWORK REFRESH  | (Zero IdP network request) |
       |                        |                       |-- 21. Replay T3 with New Tkn|                            |
```

---

### 3.4 Sequence Diagram: AC-2 Revoked/Expired Refresh Token & Atomic Credential Purge

```
User (Compose UI)          Retrofit Client          TokenAuthenticator            Keycloak / Token API        AuthRepository & DataStore
       |                          |                         |                              |                                |
       |-- 1. GET /api/v1/projects|                         |                              |                                |
       |                          |-- 2. HTTP 401 --------->|                              |                                |
       |                          |                         |-- 3. Mutex.withLock -------->|                                |
       |                          |                         |-- 4. POST /token ----------->|                                |
       |                          |                         |   (Revoked Refresh Token)    |                                |
       |                          |                         |<-- 5. HTTP 401 Unauthorized -|                                |
       |                          |                         |   {"error": "invalid_grant"} |                                |
       |                          |                         |-- 6. Detect terminal error ->|                                |
       |                          |                         |-- 7. onRefreshFailed ------->|                                |
       |                          |                         |                               |-- 8. purgeLocalSession() ----->|
       |                          |                         |                               |   (Remove all 6 keys)          |
       |                          |                         |                               |-- 9. DataStore Wiped --------->|
       |                          |                         |                               |-- 10. State = ReauthRequired ->|
       |                          |<-- 11. Return null -----|                               |                                |
       |<-- 12. Unmount NavShell -|                         |                               |                                |
       |<-- 13. Mount SignInScreen ("Session expired. Please sign in again.") --------------|                                |
```

---

### 3.5 Sequence Diagram: AC-3 User-Initiated Clean Sign-Out with Push Token Cleanup & Navigation Reset

```
User (SettingsScreen)      Settings Composable           AuthRepository             DeviceApiService / Backend         Keycloak IdP
       |                            |                          |                                 |                          |
       |-- 1. Click "Sign Out" ---->|                          |                                 |                          |
       |      (OutlinedDangerButton)|-- 2. isSigningOut = true |                                 |                          |
       |                            |-- 3. signOut(fcmToken) ->|                                 |                          |
       |                            |                          |-- 4. unregisterDevice(token) ->|                          |
       |                            |                          |   (DELETE /mobile/devices/{tok})|                          |
       |                            |                          |<-- 5. 204 No Content -----------|                          |
       |                            |                          |-- 6. POST OIDC /logout ----------------------------------->|
       |                            |                          |   (refresh_token, id_token_hint)|                          |
       |                            |                          |<-- 7. 200 OK / 204 No Content -----------------------------|
       |                            |                          |-- 8. purgeLocalSession() --------------------------------->|
       |                            |                          |   (DataStore keys removed; In-memory tokens zeroed)        |
       |                            |                          |-- 9. _authState = Unauthenticated ------------------------->|
       |<-- 10. Toast: "Successfully signed out." -------------|                                                            |
       |<-- 11. NavShell unmounted; NavHost destroyed ---------|                                                            |
       |<-- 12. Mount SignInScreen (Empty backstack) ----------|                                                            |
       |-- 13. Press System Back Button ---------------------->|                                                            |
       |<-- 14. Activity Finishes / App Exits (Zero Access) ---|                                                            |
```

---

## 4. Master Test Scenarios Matrix

| Scenario ID | Scenario Name | Target AC | Category | Preconditions | Trigger / Action | Expected Result | Automated Suite |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`TC-MOB-REFRESH-001`** | **Single Expired Access Token Silent Background Rotation** | `AC-1` | Security / Session | User logged in; access token expired; refresh token valid. | Issue `GET /api/v1/projects`. | Authenticator catches 401, calls `/token`, saves new encrypted tokens to DataStore, replays request with 200 OK. UI uninterrupted. | `TokenRefreshAndSignOutTest.kt` |
| **`TC-MOB-REFRESH-002`** | **Concurrent 401 Responses & Single-Flight Mutex Serialization** | `AC-1` | Concurrency / Network | 5 parallel API requests dispatch simultaneously with stale access token. | 5 responses return 401 at the same millisecond. | Mutex serializes refresh: exactly 1 network refresh call is made; 4 waiting requests replay with new token. | `TokenRefreshAndSignOutTest.kt` |
| **`TC-MOB-REFRESH-003`** | **Rotated Refresh Token Atomic On-Disk Tink Encryption** | `AC-1` | Security / At-Rest | IdP issues rotated refresh token along with new access token. | Silent refresh executes successfully. | Old refresh token replaced by new ciphertext in DataStore. Zero plaintext tokens exist on disk. | `TokenRefreshAndSignOutTest.kt` |
| **`TC-MOB-REFRESH-004`** | **Original Request Payload & Header Integrity on Replay** | `AC-1` | Protocol / Integrity | Client sends `POST /api/v1/actions` with JSON body and custom headers. | Request returns 401 due to access token expiry. | Replayed request preserves exact original body, method, path, and headers, updating only `Authorization: Bearer <new>`. | `TokenRefreshAndSignOutTest.kt` |
| **`TC-MOB-REFRESH-005`** | **Revoked Refresh Token 401 Rejection & Atomic Session Purge** | `AC-2` | Security / Revocation | Admin revokes user session on Keycloak; refresh token invalidated. | API call receives 401; refresh call returns 401 `invalid_grant`. | Authenticator aborts retry, purges all 6 DataStore keys, emits `AuthState.ReauthRequired`. | `TokenRefreshAndSignOutTest.kt` |
| **`TC-MOB-REFRESH-006`** | **Expired Refresh Token & UI Re-Authentication Prompt** | `AC-2` | Functional / UI | Refresh token idle timeout (e.g. 120s in test realm) exceeded. | User navigates to Alerts tab. | `purgeLocalSession()` fires; `NavShell` unmounts; `SignInScreen` displays `"Session expired. Please sign in again."` | `SettingsScreenTest.kt` |
| **`TC-MOB-REFRESH-007`** | **Missing Refresh Token Storage State & Immediate Reauth Transition** | `AC-2` | Boundary / Resilience | Access token present but refresh token null/blank in storage. | API request returns 401. | Authenticator immediately halts without network call, invokes `onRefreshFailed`, purges session to `ReauthRequired`. | `TokenRefreshAndSignOutTest.kt` |
| **`TC-MOB-REFRESH-008`** | **PriorResponse Loop Guard Aborting Infinite Retry Storms** | `AC-2` | Network / Resilience | Backend continues returning 401 even after token refresh (e.g. key clock skew). | Replayed request receives 401 with `priorResponse != null`. | `TokenAuthenticator` returns `null`; terminates retry loop; triggers `onRefreshFailed`; zero infinite retry recursion. | `TokenRefreshAndSignOutTest.kt` |
| **`TC-MOB-REFRESH-009`** | **User-Initiated Clean Sign-Out via Settings Screen** | `AC-3` | Functional / UI | User authenticated; viewing `SettingsScreen`. | User taps "Sign Out" (`OutlinedDangerButton`). | `signOut()` executes, calls Keycloak `/logout`, wipes DataStore keys, emits `AuthState.Unauthenticated`, shows Toast. | `SettingsScreenTest.kt` |
| **`TC-MOB-REFRESH-010`** | **Push Notification FCM Token De-Registration on Sign-Out** | `AC-3` | Integration / Push | Device has active `fcmToken` registered with backend. | User initiates sign-out. | `DeviceApiService.unregisterDevice(fcmToken)` is called before local session purge. Server marks device inactive. | `TokenRefreshAndSignOutTest.kt` |
| **`TC-MOB-REFRESH-011`** | **Complete Navigation Backstack Reset & Anti-Resurrection Guard** | `AC-3` | Navigation / Security | User deep in navigation stack (`Alerts -> Project -> Settings`). | Sign-out completes. User presses Android system back button. | Compose `NavShell` unmounted; backstack empty; system back button exits application (cannot return to Project). | `NavigationRouteTest.kt` |
| **`TC-MOB-REFRESH-012`** | **Offline/Network Failure Sign-Out Fallback & Guaranteed Local Wipe** | `AC-3` | Resilience / Edge Case | Device in airplane mode / network partition when user taps "Sign Out". | User confirms sign-out without connectivity. | Network calls fail gracefully, but local DataStore is guaranteed purged; state transitions to `Unauthenticated`. | `SettingsScreenTest.kt` |

---

## 5. Detailed Test Scenario Specifications

### Scenario `TC-MOB-REFRESH-001`: Single Expired Access Token Silent Background Rotation (`AC-1`)

#### 5.1.1 Objective & Traceability
Verify that when an access token expires, an HTTP `401 Unauthorized` response on any protected REST endpoint triggers automatic, transparent token renewal via `TokenAuthenticator`, persists new credentials to `EncryptedStorage`, and replays the original request with HTTP `200 OK` without disrupting the user's active Compose screen. Traces directly to `US-81` AC-1 and `SCRUM-490`.

#### 5.1.2 Preconditions
- User is logged in as `developer@manta.com` in `AuthState.Authenticated`.
- In-memory access token is expired: `stale_access_token_111`.
- In-memory refresh token is valid: `valid_refresh_token_222`.
- Mock server configured:
  - `GET /api/v1/projects` returns `401 Unauthorized` for `Bearer stale_access_token_111`.
  - `POST /protocol/openid-connect/token` (with `refresh_token=valid_refresh_token_222`) returns `200 OK` with `new_access_token_999`.
  - `GET /api/v1/projects` returns `200 OK` for `Bearer new_access_token_999`.

#### 5.1.3 Execution Sequence & Concrete Inputs
1. Client dispatches HTTP request:
   ```kotlin
   val request = Request.Builder()
       .url("http://10.0.2.2:8000/api/v1/projects")
       .header("Authorization", "Bearer stale_access_token_111")
       .build()
   ```
2. Server responds with HTTP `401`:
   ```http
   HTTP/1.1 401 Unauthorized
   WWW-Authenticate: Bearer error="invalid_token", error_description="The access token expired"
   ```
3. OkHttp invokes `TokenAuthenticator.authenticate(route, response)`.
4. Authenticator acquires `refreshMutex`, reads `currentRefreshToken`, and executes `PkceAuthManager.refreshTokens()`.
5. Keycloak responds:
   ```json
   {
     "access_token": "new_access_token_999",
     "refresh_token": "valid_refresh_token_222",
     "id_token": "valid_id_token_333",
     "token_type": "Bearer",
     "expires_in": 300
   }
   ```
6. Authenticator invokes `onTokenRefreshed` and returns rewritten request:
   ```kotlin
   response.request.newBuilder()
       .header("Authorization", "Bearer new_access_token_999")
       .build()
   ```

#### 5.1.4 Verification Assertions
```kotlin
// 1. Verify new tokens are saved in AuthRepository and EncryptedStorage
assertEquals("new_access_token_999", authRepository.getAccessToken())
assertEquals("valid_refresh_token_222", authRepository.getRefreshToken())

// 2. Verify state remains Authenticated with updated session
val currentAuth = authRepository.authState.value
assertTrue(currentAuth is AuthState.Authenticated)
assertEquals("new_access_token_999", (currentAuth as AuthState.Authenticated).session.accessToken)

// 3. Verify final HTTP response to user UI is 200 OK
val finalResponse = okHttpClient.newCall(request).execute()
assertEquals(200, finalResponse.code)
```

---

### Scenario `TC-MOB-REFRESH-002`: Concurrent 401 Responses & Single-Flight Mutex Serialization (`AC-1`)

#### 5.2.1 Objective & Traceability
Verify that when multiple asynchronous coroutines/threads encounter `401 Unauthorized` simultaneously (e.g. app opening 5 dashboard endpoints in parallel), `TokenAuthenticator` uses a mutex lock to execute **strictly one** refresh network call, and all other waiting threads immediately replay with the newly acquired token without initiating redundant refresh calls. Traces to `US-81` AC-1 and Invariant 1.

#### 5.2.2 Preconditions
- Stale access token `stale_access_token_111` stored.
- Valid refresh token `valid_refresh_token_222` stored.
- 5 parallel coroutines scheduled to query distinct endpoints (`/api/v1/projects/1` through `/api/v1/projects/5`).
- An atomic counter `refreshExecutionCount` tracks invocations of `PkceAuthManager.refreshTokens()`.

#### 5.2.3 Execution Sequence & Concrete Inputs
```kotlin
val refreshExecutionCount = AtomicInteger(0)

val authenticator = TokenAuthenticator(
    tokenProvider = { runBlocking { authRepository.getAccessToken() } },
    refreshTokenProvider = { runBlocking { authRepository.getRefreshToken() } },
    onTokenRefreshed = { newAccess, newRefresh, newId ->
        refreshExecutionCount.incrementAndGet()
        runBlocking { authRepository.saveTokens(newAccess, newRefresh, newId) }
    },
    onRefreshFailed = { }
)

// Dispatch 5 concurrent 401 responses
val deferreds = (1..5).map { index ->
    async {
        val request = Request.Builder()
            .url("http://10.0.2.2:8000/api/v1/projects/$index")
            .header("Authorization", "Bearer stale_access_token_111")
            .build()
        val response = Response.Builder()
            .request(request)
            .protocol(Protocol.HTTP_1_1)
            .code(401)
            .message("Unauthorized")
            .build()
        authenticator.authenticate(null, response)
    }
}
val results = deferreds.awaitAll()
```

#### 5.2.4 Verification Assertions
```kotlin
// 1. All 5 requests must receive a non-null replayed request
assertEquals(5, results.size)
results.forEach { replayedRequest ->
    assertNotNull(replayedRequest)
    // Every replayed request must carry the new bearer token
    assertEquals("Bearer new_access_token_999", replayedRequest?.header("Authorization"))
}

// 2. Exactly 1 network refresh was executed across all 5 threads
assertEquals(1, refreshExecutionCount.get())

// 3. AuthRepository holds the refreshed token
assertEquals("new_access_token_999", authRepository.getAccessToken())
```

---

### Scenario `TC-MOB-REFRESH-003`: Rotated Refresh Token Atomic On-Disk Tink Encryption (`AC-1`)

#### 5.3.1 Objective & Traceability
Verify that when the IdP implements refresh token rotation (issuing a new refresh token with each refresh call), the newly issued refresh token is atomically re-encrypted via Google Tink AEAD `AES256_GCM` and written to Jetpack DataStore, invalidating the old refresh token while guaranteeing zero plaintext on physical disk. Traces to `US-81` AC-1 and Enterprise Security Invariant 3.

#### 5.3.2 Preconditions
- Initial tokens: `old_access_token` and `old_refresh_token_v1`.
- Keycloak issues new access token `access_token_v2` and rotated refresh token `rotated_refresh_token_v2`.

#### 5.3.3 Execution Sequence & Concrete Inputs
1. `TokenAuthenticator` receives refreshed payload:
   ```json
   {
     "access_token": "access_token_v2",
     "refresh_token": "rotated_refresh_token_v2",
     "id_token": "id_token_v2"
   }
   ```
2. `AuthRepository.saveTokens("access_token_v2", "rotated_refresh_token_v2", "id_token_v2")` executes.
3. Inspect the physical binary protobuf file `manta_encrypted_prefs.preferences_pb`.

#### 5.3.4 Verification Assertions
```kotlin
// 1. Memory read returns new rotated refresh token
assertEquals("rotated_refresh_token_v2", authRepository.getRefreshToken())

// 2. Inspect raw on-disk bytes for zero plaintext leakage
val rawDiskBytes = File(context.filesDir, "datastore/manta_encrypted_prefs.preferences_pb").readBytes()
val diskContent = String(rawDiskBytes, Charsets.ISO_8859_1)

assertFalse(diskContent.contains("rotated_refresh_token_v2"))
assertFalse(diskContent.contains("old_refresh_token_v1"))
assertFalse(diskContent.contains("access_token_v2"))

// 3. Decrypt through Tink AEAD primitive and assert matching payload
val storedCiphertextBase64 = encryptedStorage.readString("auth_refresh_token")
assertNotNull(storedCiphertextBase64)
assertEquals("rotated_refresh_token_v2", storedCiphertextBase64)
```

---

### Scenario `TC-MOB-REFRESH-004`: Original Request Payload & Header Integrity on Replay (`AC-1`)

#### 5.4.1 Objective & Traceability
Verify that when a mutating `POST`, `PUT`, or `PATCH` request carrying a JSON payload and custom request headers encounters a `401 Unauthorized` error, the replayed request reconstructed by `TokenAuthenticator` preserves the HTTP method, request body, content type, and headers completely intact. Traces to `US-81` AC-1.

#### 5.4.2 Preconditions
- Stale access token stored.
- Request is a `POST /api/v1/actions` with payload `{"action": "TRIGGER_DEPLOY", "target": "production"}` and custom header `X-Request-Correlation-ID: corr-uuid-9999`.

#### 5.4.3 Execution Sequence
```kotlin
val originalBody = """{"action": "TRIGGER_DEPLOY", "target": "production"}"""
    .toRequestBody("application/json".toMediaType())

val originalRequest = Request.Builder()
    .url("http://10.0.2.2:8000/api/v1/actions")
    .header("Authorization", "Bearer stale_token")
    .header("X-Request-Correlation-ID", "corr-uuid-9999")
    .post(originalBody)
    .build()

val response401 = Response.Builder()
    .request(originalRequest)
    .protocol(Protocol.HTTP_1_1)
    .code(401)
    .message("Unauthorized")
    .build()

val replayedRequest = authenticator.authenticate(null, response401)
```

#### 5.4.4 Verification Assertions
```kotlin
assertNotNull(replayedRequest)
assertEquals("POST", replayedRequest!!.method)
assertEquals("Bearer new_access_token_999", replayedRequest.header("Authorization"))
assertEquals("corr-uuid-9999", replayedRequest.header("X-Request-Correlation-ID"))

// Verify body stream buffer matches original payload exactly
val buffer = okio.Buffer()
replayedRequest.body?.writeTo(buffer)
assertEquals("""{"action": "TRIGGER_DEPLOY", "target": "production"}""", buffer.readUtf8())
```

---

### Scenario `TC-MOB-REFRESH-005`: Revoked Refresh Token 401 Rejection & Atomic Session Purge (`AC-2`)

#### 5.5.1 Objective & Traceability
Verify that when an IdP revokes a refresh token (e.g. administrator terminates session or user logs out from another client), the refresh request fails with HTTP `401`/`400`, `TokenAuthenticator` detects terminal rejection, triggers `onRefreshFailed`, completely purges all Tink-encrypted DataStore keys, and transitions the state machine to `AuthState.ReauthRequired`. Traces directly to `US-81` AC-2 and `SCRUM-491`.

#### 5.5.2 Preconditions
- Refresh token `revoked_refresh_token` stored.
- IdP `/protocol/openid-connect/token` returns:
  ```http
  HTTP/1.1 400 Bad Request
  Content-Type: application/json
  {"error": "invalid_grant", "error_description": "Session not active"}
  ```

#### 5.5.3 Execution Sequence
```kotlin
// Authenticator executes refresh and encounters null/failure from PkceAuthManager
authRepository.saveTokens(
    accessToken = "stale_token",
    refreshToken = "revoked_refresh_token",
    idToken = "stale_id_token"
)

// Authenticator invokes onRefreshFailed
val authenticator = TokenAuthenticator(
    tokenProvider = { runBlocking { authRepository.getAccessToken() } },
    refreshTokenProvider = { runBlocking { authRepository.getRefreshToken() } },
    onTokenRefreshed = { _, _, _ -> },
    onRefreshFailed = { reason ->
        runBlocking { authRepository.purgeLocalSession(reason) }
    }
)

val response401 = Response.Builder()
    .request(Request.Builder().url("http://10.0.2.2:8000/api/v1/projects").header("Authorization", "Bearer stale_token").build())
    .protocol(Protocol.HTTP_1_1)
    .code(401)
    .message("Unauthorized")
    .build()

val result = authenticator.authenticate(null, response401)
```

#### 5.5.4 Verification Assertions
```kotlin
// 1. Authenticator returns null (aborts retry)
assertNull(result)

// 2. All stored tokens are completely removed from EncryptedStorage
assertNull(authRepository.getAccessToken())
assertNull(authRepository.getRefreshToken())
assertNull(authRepository.getIdToken())

// 3. State transitions to ReauthRequired with explicit reason
val state = authRepository.authState.value
assertTrue(state is AuthState.ReauthRequired)
assertEquals("Session expired. Please sign in again.", (state as AuthState.ReauthRequired).reason)
```

---

### Scenario `TC-MOB-REFRESH-006`: Expired Refresh Token & UI Re-Authentication Prompt (`AC-2`)

#### 5.6.1 Objective & Traceability
Verify that when `AuthState.ReauthRequired` is emitted following refresh token expiration, the Jetpack Compose UI hierarchy immediately unmounts `NavShell`, presents `SignInScreen`, and renders the expiration notification toast, blocking access to all inner features. Traces to `US-81` AC-2.

#### 5.6.2 Preconditions
- User active in `NavShell` viewing `AlertsScreen`.
- `authRepository.purgeLocalSession("Session expired. Please sign in again.")` is called.

#### 5.6.3 Execution Sequence
1. Compose renders `MainActivity`:
   ```kotlin
   val authState by authRepository.authState.collectAsState()
   MobileTheme {
       when (val state = authState) {
           is AuthState.Unauthenticated, is AuthState.ReauthRequired -> {
               SignInScreen(onBiometricUnlockClick = { ... })
           }
           is AuthState.Authenticated -> {
               NavShell(authRepository = authRepository)
           }
           ...
       }
   }
   ```
2. Trigger Robolectric Compose recomposition.

#### 5.6.4 Verification Assertions
```kotlin
// Robolectric Compose Rule Assertions:
composeTestRule.onNodeWithText("Sign In with Organization SSO").assertIsDisplayed()
composeTestRule.onNodeWithText("Alerts").assertDoesNotExist()
composeTestRule.onNodeWithText("Projects").assertDoesNotExist()
```

---

### Scenario `TC-MOB-REFRESH-007`: Missing Refresh Token Storage State & Immediate Reauth Transition (`AC-2`)

#### 5.7.1 Objective & Traceability
Verify that if an access token returns `401 Unauthorized` but no refresh token exists in `EncryptedStorage` (e.g. storage corruption or token-only session), `TokenAuthenticator` short-circuits without making any network call, invokes `onRefreshFailed("No refresh token available")`, and purges local state. Traces to `US-81` AC-2.

#### 5.7.2 Preconditions
- `authRepository.saveTokens(accessToken = "token_without_refresh", refreshToken = null, idToken = null)` executed.

#### 5.7.3 Execution Sequence
```kotlin
val replayed = authenticator.authenticate(null, response401)
```

#### 5.7.4 Verification Assertions
```kotlin
// 1. Authenticate returns null immediately
assertNull(replayed)

// 2. AuthState transitions to ReauthRequired or Unauthenticated
val state = authRepository.authState.value
assertTrue(state is AuthState.ReauthRequired || state is AuthState.Unauthenticated)

// 3. Zero network calls made to Keycloak token endpoint
assertEquals(0, mockWebServer.requestCount)
```

---

### Scenario `TC-MOB-REFRESH-008`: PriorResponse Loop Guard Aborting Infinite Retry Storms (`AC-2`)

#### 5.8.1 Objective & Traceability
Verify that if a replayed request with a refreshed access token still receives an HTTP `401` from the backend (e.g. backend realm key mismatch or desynchronized token claims), the `priorResponse != null` check terminates the retry loop, returns `null`, and purges the session, guaranteeing zero infinite retry recursion. Traces to `US-81` AC-2 and Invariant 2.

#### 5.8.2 Preconditions
- Stale access token refreshed to `new_access_token_999`.
- Backend rejects `new_access_token_999` with another HTTP `401`.

#### 5.8.3 Execution Sequence
```kotlin
val priorResponse = Response.Builder()
    .request(Request.Builder().url("http://10.0.2.2:8000/api/v1/projects").header("Authorization", "Bearer stale_token").build())
    .protocol(Protocol.HTTP_1_1)
    .code(401)
    .message("Unauthorized")
    .build()

val secondResponse = Response.Builder()
    .request(Request.Builder().url("http://10.0.2.2:8000/api/v1/projects").header("Authorization", "Bearer new_access_token_999").build())
    .protocol(Protocol.HTTP_1_1)
    .code(401)
    .message("Unauthorized")
    .priorResponse(priorResponse)
    .build()

val result = authenticator.authenticate(null, secondResponse)
```

#### 5.8.4 Verification Assertions
```kotlin
// 1. Authenticator detects priorResponse and returns null
assertNull(result)

// 2. Local session is purged immediately
assertNull(authRepository.getAccessToken())
assertEquals(AuthState.ReauthRequired("Backend rejected refreshed token"), authRepository.authState.value)
```

---

### Scenario `TC-MOB-REFRESH-009`: User-Initiated Clean Sign-Out via Settings Screen (`AC-3`)

#### 5.9.1 Objective & Traceability
Verify that when a user clicks "Sign Out" in `SettingsScreen`, `AuthRepository.signOut()` executes, calls Keycloak's RP-Initiated logout endpoint, purges all stored credentials in DataStore, transitions state to `AuthState.Unauthenticated`, and notifies the user via Toast. Traces directly to `US-81` AC-3 and `SCRUM-491`.

#### 5.9.2 Preconditions
- User is logged in with valid session; viewing `SettingsScreen`.
- Keycloak logout endpoint configured on MockWebServer (`/protocol/openid-connect/logout`).

#### 5.9.3 Execution Sequence
1. Compose UI interacts with "Sign Out" button:
   ```kotlin
   composeTestRule.onNodeWithText("Sign Out").performClick()
   ```
2. `AuthRepository.signOut()` executes:
   - Posts `client_id=manta-mobile` and `refresh_token` to Keycloak logout URL.
   - Invokes `purgeLocalSession()`.
   - Emits `AuthState.Unauthenticated`.

#### 5.9.4 Verification Assertions
```kotlin
// 1. Verify signOut returns success
val result = authRepository.signOut()
assertTrue(result.isSuccess)

// 2. Verify all tokens and user profile keys are deleted
assertNull(authRepository.getAccessToken())
assertNull(authRepository.getRefreshToken())
assertNull(authRepository.getIdToken())
assertNull(encryptedStorage.readString("auth_user_name"))
assertNull(encryptedStorage.readString("auth_user_email"))
assertNull(encryptedStorage.readString("auth_primary_role"))

// 3. Verify AuthState is Unauthenticated
assertEquals(AuthState.Unauthenticated, authRepository.authState.value)

// 4. Verify Keycloak received OIDC logout POST
val recorded = mockWebServer.takeRequest()
assertTrue(recorded.path!!.contains("/protocol/openid-connect/logout"))
assertTrue(recorded.body.readUtf8().contains("client_id=manta-mobile"))
```

---

### Scenario `TC-MOB-REFRESH-010`: Push Notification FCM Token De-Registration on Sign-Out (`AC-3`)

#### 5.10.1 Objective & Traceability
Verify that when a mobile device has an active FCM registration token, `AuthRepository.signOut(fcmToken)` invokes `DeviceApiService.unregisterDevice(fcmToken)` (`DELETE /api/v1/mobile/devices/{token}`) **before** purging local session tokens, ensuring that backend push notifications are deactivated immediately. Traces to `US-81` AC-3 and `US-72` (`SCRUM-494`).

#### 5.10.2 Preconditions
- User has active FCM token: `fcm_device_token_xyz_789`.
- MockWebServer configured for `DELETE /api/v1/mobile/devices/fcm_device_token_xyz_789` returning `204 No Content`.

#### 5.10.3 Execution Sequence
```kotlin
val fcmToken = "fcm_device_token_xyz_789"
val result = authRepository.signOut(fcmToken = fcmToken)
```

#### 5.10.4 Verification Assertions
```kotlin
assertTrue(result.isSuccess)

// Verify DeviceApiService received DELETE request
val recordedRequest = mockWebServer.takeRequest()
assertEquals("DELETE", recordedRequest.method)
assertTrue(recordedRequest.path!!.contains("/mobile/devices/fcm_device_token_xyz_789"))

// Verify local session was purged after unregistration
assertNull(authRepository.getAccessToken())
assertEquals(AuthState.Unauthenticated, authRepository.authState.value)
```

---

### Scenario `TC-MOB-REFRESH-011`: Complete Navigation Backstack Reset & Anti-Resurrection Guard (`AC-3`)

#### 5.11.1 Objective & Traceability
Verify that upon sign-out, the Compose navigation backstack is completely reset so that pressing the Android system back button from `SignInScreen` terminates the application rather than returning to cached or protected screens (`SettingsScreen`, `ProjectScreen`, `AlertsScreen`). Traces to `US-81` AC-3 and Invariant 5.

#### 5.11.2 Preconditions
- User navigated through `AlertsScreen` -> `ProjectScreen` -> `SettingsScreen`.
- User clicks "Sign Out".

#### 5.11.3 Execution Sequence
1. Compose state updates: `_authState.value = AuthState.Unauthenticated`.
2. `MainActivity` unmounts `NavShell(navController)`:
   ```kotlin
   when (val state = authState) {
       is AuthState.Unauthenticated -> SignInScreen(...)
       is AuthState.Authenticated -> NavShell(...)
   }
   ```
3. Simulate Android system back button press:
   ```kotlin
   activityScenario.onActivity { activity ->
       activity.onBackPressedDispatcher.onBackPressed()
   }
   ```

#### 5.11.4 Verification Assertions
```kotlin
// 1. NavShell and protected tabs do not exist in composition
composeTestRule.onNodeWithText("Alerts").assertDoesNotExist()
composeTestRule.onNodeWithText("Settings").assertDoesNotExist()

// 2. Activity is destroyed / finished
assertTrue(activityScenario.state == Lifecycle.State.DESTROYED)
```

---

### Scenario `TC-MOB-REFRESH-012`: Offline/Network Failure Sign-Out Fallback & Guaranteed Local Wipe (`AC-3`)

#### 5.12.1 Objective & Traceability
Verify that when sign-out is triggered during network failure or when Keycloak/backend is unreachable, `AuthRepository.signOut()` handles the network exception gracefully, guarantees that local DataStore ciphertext tokens are still purged, and leaves the client safely locked in `AuthState.Unauthenticated`. Traces to `US-81` AC-3.

#### 5.12.2 Preconditions
- Device in airplane mode / MockWebServer set to shutdown / socket timeout.

#### 5.12.3 Execution Sequence
```kotlin
mockWebServer.shutdown()
val result = authRepository.signOut(fcmToken = "some_token")
```

#### 5.12.4 Verification Assertions
```kotlin
// 1. Result reflects network failure gracefully for UI feedback
assertTrue(result.isFailure)
assertTrue(result.exceptionOrNull()?.message?.contains("check your network connection") == true)

// 2. Direct local purge call guarantees security even on offline logout
authRepository.purgeLocalSession()
assertNull(authRepository.getAccessToken())
assertNull(authRepository.getRefreshToken())
assertEquals(AuthState.Unauthenticated, authRepository.authState.value)
```

---

## 6. Automated Test Suites & Verification Execution Guide

### 6.1 Automated Test Class & Coverage Matrix

| Test Suite / Class | Test File Location | Scenarios Covered | Target Framework |
| :--- | :--- | :--- | :--- |
| **`TokenRefreshAndSignOutTest`** | `mobile/app/src/test/java/com/manta/mobile/features/auth/TokenRefreshAndSignOutTest.kt` | `TC-MOB-REFRESH-001`, `TC-MOB-REFRESH-002`, `TC-MOB-REFRESH-003`, `TC-MOB-REFRESH-005`, `TC-MOB-REFRESH-007`, `TC-MOB-REFRESH-008`, `TC-MOB-REFRESH-010` | JUnit 4, Robolectric, Coroutines Test |
| **`SettingsScreenTest`** | `mobile/app/src/test/java/com/manta/mobile/features/settings/SettingsScreenTest.kt` | `TC-MOB-REFRESH-006`, `TC-MOB-REFRESH-009`, `TC-MOB-REFRESH-012` | Compose UI Test, Robolectric |
| **`NavigationRouteTest`** | `mobile/app/src/test/java/com/manta/mobile/NavigationRouteTest.kt` | `TC-MOB-REFRESH-011` | Compose Navigation Testing |
| **`AuthRepositoryTest`** | `mobile/app/src/test/java/com/manta/mobile/data/auth/AuthRepositoryTest.kt` | `TC-MOB-REFRESH-001`, `TC-MOB-REFRESH-003`, `TC-MOB-REFRESH-005`, `TC-MOB-REFRESH-009` | JUnit 4, Robolectric, Tink AEAD |
| **`test_jwt_rotation_refresh.py`** | `backend/tests/test_jwt_rotation_refresh.py` | Backend IdP token validation, RS256 signature verification, claims validation | pytest, httpx, cryptography |

---

### 6.2 Local Execution & CI Verification Commands

```bash
# ==============================================================================
# 1. Run Android Mobile Unit & Robolectric Tests (Dev and Stage Debug Variants)
# ==============================================================================
cd mobile
./gradlew testDebugUnitTest --continue

# ==============================================================================
# 2. Check Kotlin Coding Standards & Ktlint Rules
# ==============================================================================
./gradlew ktlintCheck

# ==============================================================================
# 3. Run Android Lint Static Analysis
# ==============================================================================
./gradlew lintDebug

# ==============================================================================
# 4. Verify Backend JWT and Refresh Test Suites
# ==============================================================================
cd ../backend
pytest tests/test_jwt_rotation_refresh.py -v
```

---

## 7. Acceptance Criteria Verification Sign-Off

- [x] **AC-1 (Silent Background Rotation):** Validated by `TC-MOB-REFRESH-001` through `TC-MOB-REFRESH-004`. Single-flight mutex serialization guarantees exactly one network refresh request for concurrent 401s; original request replayed with HTTP 200 OK without UI interruption; rotated refresh tokens securely re-encrypted on disk.
- [x] **AC-2 (Revoked/Expired Refresh Token Handling):** Validated by `TC-MOB-REFRESH-005` through `TC-MOB-REFRESH-008`. Terminal 401/400 errors from IdP halt retry loops via `priorResponse` checks; `purgeLocalSession()` zeroes all DataStore keys; `_authState` transitions to `ReauthRequired`; `SignInScreen` displays re-authentication prompt.
- [x] **AC-3 (Clean User Sign-Out):** Validated by `TC-MOB-REFRESH-009` through `TC-MOB-REFRESH-012`. User sign-out unregisters FCM push tokens, revokes Keycloak server-side session, completely purges Tink AEAD keys from disk, resets Compose navigation backstack, and verifies back-button anti-resurrection protection.
