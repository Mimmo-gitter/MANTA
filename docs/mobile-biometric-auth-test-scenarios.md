# US-38: Mobile OIDC Sign-In, Biometric Unlock and Failed Biometric Fallback Test Scenarios

**Task:** `SCRUM-488` (`Task 38.4`: Create mobile test scenarios for OIDC sign-in, biometric unlock and failed biometric fallback)  
**Parent User Story:** `SCRUM-373` (`US-38`: Mobile Biometric Authentication & OIDC PKCE Session Security)  
**Related Tickets:**  
- `SCRUM-485` / `Task 38.1`: Auth infrastructure flow implementation (EncryptedStorage, Tink AEAD AES-256-GCM, PkceUtils, AuthRepository)  
- `SCRUM-486` / `Task 38.2`: Auth activity and screen implementation (SignInScreen, PKCE Custom Tab redirect, deep link handler)  
- `SCRUM-487` / `Task 38.3`: Biometric unlock flow implementation (BiometricPromptManager, BiometricUnlockScreen, multi-tier fallback)  
- `SCRUM-488` / `Task 38.4`: Create mobile test scenarios for OIDC sign-in, biometric unlock and failed biometric fallback (this document)  
- `SCRUM-489` / `Task 38.5`: Automated widget and instrumentation tests (BiometricUnlockScreenTest, AuthRepositoryTest, PkceAuthManagerTest, SignInScreenTest)  
- `SCRUM-490` / `SCRUM-491`: Refresh token interceptor and silent renewal implementation  
- `SCRUM-344` / `SCRUM-346` / `SCRUM-349`: Android mobile foundation, bottom navigation shell, and app startup test scenarios  
- `SCRUM-240` / `SCRUM-241` / `SCRUM-273`: OIDC Keycloak realm, PKCE S256, and RS256 token validation  

**Target Platform & Framework:** Android (minSdk 27, compileSdk/targetSdk 34/37), Jetpack Compose (Material 3), Jetpack DataStore Preferences (`manta_encrypted_prefs.preferences_pb`), Google Tink AEAD (`AES256_GCM`), AndroidX Biometric (`BiometricPrompt`)  
**Target Implementation Modules:**  
- [`mobile/app/src/main/java/com/manta/mobile/core/security/EncryptedStorage.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/main/java/com/manta/mobile/core/security/EncryptedStorage.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/core/security/BiometricPromptManager.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/main/java/com/manta/mobile/core/security/BiometricPromptManager.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/features/auth/AuthRepository.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/main/java/com/manta/mobile/features/auth/AuthRepository.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/features/auth/PkceAuthManager.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/main/java/com/manta/mobile/features/auth/PkceAuthManager.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/features/auth/SignInScreen.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/main/java/com/manta/mobile/features/auth/SignInScreen.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/features/auth/BiometricUnlockScreen.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/main/java/com/manta/mobile/features/auth/BiometricUnlockScreen.kt)  
- [`mobile/app/src/main/java/com/manta/mobile/MainActivity.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/main/java/com/manta/mobile/MainActivity.kt)  
**Authoritative Automated Test Suites:**  
- [`mobile/app/src/test/java/com/manta/mobile/features/auth/BiometricUnlockScreenTest.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/test/java/com/manta/mobile/features/auth/BiometricUnlockScreenTest.kt)  
- [`mobile/app/src/test/java/com/manta/mobile/data/auth/AuthRepositoryTest.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/test/java/com/manta/mobile/data/auth/AuthRepositoryTest.kt)  
- [`mobile/app/src/test/java/com/manta/mobile/features/auth/PkceAuthManagerTest.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/test/java/com/manta/mobile/features/auth/PkceAuthManagerTest.kt)  
- [`mobile/app/src/test/java/com/manta/mobile/features/auth/SignInScreenTest.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/test/java/com/manta/mobile/features/auth/SignInScreenTest.kt)  
**Specification Location in Documentation:** [`docs/mobile-biometric-auth-test-scenarios.md`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/docs/mobile-biometric-auth-test-scenarios.md)  
**Assignee:** Matvii Yeromenko  
**Category:** Testing - Design  
**Estimate:** 0.25 Story Points (0h 30m Original Estimate)  
**Status:** Approved / Verification Baseline Established  

---

## 1. Executive Summary & Objective

### 1.1 Objective

Design and formalize the authoritative QA test scenarios and cryptographic verification contracts for **Mobile OpenID Connect (OIDC) PKCE Sign-In, Hardware-Backed Keystore Ciphertext Persistence, Fast Biometric Unlock, and Failed/Cancelled Biometric Fallback** in `US-38` (`SCRUM-373` / `SCRUM-488`).

On mobile devices, user sessions must balance hardened enterprise security with seamless daily usability:
1. **OIDC PKCE Authentication & On-Disk Ciphertext Verification (AC-1):** Users authenticate via Keycloak utilizing OAuth 2.0 Authorization Code Flow with Proof Key for Code Exchange (RFC 7636, `S256`). Upon receiving authorization code `code`, tokens (`access_token`, `refresh_token`, `id_token`) and user metadata are encrypted via Google Tink AEAD (`AES256_GCM`) backed by Android Keystore, ensuring **zero plaintext tokens or user identifiers reside on physical disk storage** in Jetpack DataStore (`manta_encrypted_prefs.preferences_pb`), immediately navigating to the home/dashboard shell (`NavShell`).
2. **App Relaunch & Hardware Biometric Unlock (AC-2):** When the application is cold-started or relaunched with active tokens, it bypasses full credential entry and enters `AuthState.LockedRequiresBiometric`. It initializes `BiometricPromptManager`, querying AndroidX `BiometricPrompt` with hardware compatibility fallbacks. Upon a matching fingerprint or facial scan (`AuthenticationResult`), stored tokens are decrypted in memory, and the session seamlessly unlocks into `AuthState.Authenticated`.
3. **Cancelled / Failed Biometric Fallback & Full Re-Authentication (AC-3):** When a biometric scan fails, the UI dynamically decrements attempts remaining (e.g. `3 Attempts` $\rightarrow$ `2 Attempts` $\rightarrow$ `1 Attempt`). If the user cancels the prompt or exhausts all attempts (triggering OS sensor lockout `ERROR_LOCKOUT` or 3 failed attempts), dashboard access is strictly blocked. The app transitions to `BiometricUnlockSubState.Unavailable`, displays `● SENSOR UNAVAILABLE`, purges cached credentials upon user action, and redirects the user to full organization credential sign-in (`SignInScreen`).

---

## 2. Core Architectural Invariants & Security Formulations

```
+-------------------------------------------------------------------------------------------------------------------+
|                        MOBILE OIDC PKCE & HARDWARE-BACKED BIOMETRIC SECURITY ARCHITECTURE                         |
+-------------------------------------+-------------------------------------+---------------------------------------+
| 1. RFC 7636 PKCE S256 Invariant     | 2. Tink AEAD At-Rest Encryption     | 3. AndroidX BiometricPrompt Gateway   |
|    (Code Challenge & Exchange)      |    (Zero Plaintext on Flash Disk)   |    (Multi-Tier Hardware Fallback)     |
+-------------------------------------+-------------------------------------+---------------------------------------+
| * Verifier v in [43, 128] chars     | * Master key in Android Keystore:   | * BiometricManager authenticator mask:|
| * Challenge c = BASE64URL(SHA256(v))|   android-keystore://manta_master_key  BIOMETRIC_STRONG | WEAK | CREDENTIAL|
| * Keycloak validates c == SHA256(v) | * Keyset stored in manta_tink_prefs | * Fallback to FingerprintManagerCompat|
| * Mitigates code injection attacks  | * Jetpack DataStore stores only     |   for Samsung / legacy API 28 devices |
| * Scopes: openid profile email      |   Base64(AES-256-GCM(plaintext))    | * Hardware failure emits typed errors |
|   offline_access                    | * Nonce = 12 bytes, Tag = 16 bytes  |   (LOCKOUT, CANCELED, NO_HARDWARE)    |
+-------------------------------------+-------------------------------------+---------------------------------------+
                                                         |
                                                         v
+-------------------------------------------------------------------------------------------------------------------+
|                              MOBILE AUTHENTICATION STATE & ROUTE GUARD BOUNDARIES                                 |
+----------------------------------------------------+--------------------------------------------------------------+
| 4. Navigation Route Guard Invariant                | 5. Three-Attempt Lockout & Hard Fallback Gate                |
+----------------------------------------------------+--------------------------------------------------------------+
| * NavShell (Dashboard) requires Authenticated state| * Transient failure: BiometricUnlockSubState.Failed(attempts)|
| * LockedRequiresBiometric prevents all child routes| * 3 consecutive failures: SubState.Unavailable               |
| * Deep links blocked until session unlocked        | * Session purge (logout): wipes DataStore keys               |
| * Zero token exposure in memory dump post-logout   | * Immediate redirect to Organization SSO / Password Form     |
+----------------------------------------------------+--------------------------------------------------------------+
```

### Invariant 1: RFC 7636 PKCE `S256` Cryptographic Exchange Invariant

For every mobile authorization session:
1. The client generates an unguessable high-entropy code verifier $v \in [A\text{–}Z, a\text{–}z, 0\text{–}9, -, ., _, \sim]^{128}$.
2. The code challenge $c$ is computed using the standard SHA-256 transformation:
   $$c = \text{BASE64URL-NOPAD}(\text{SHA-256}(v))$$
3. The authorization URL is dispatched to the system browser or Android Custom Tab:
   $$\text{URL}_{\text{auth}} = \text{KEYCLOAK\_URL} + \text{/protocol/openid-connect/auth} \,?\, \text{client\_id}=\text{manta-mobile} \;\&\; \text{redirect\_uri}=\text{manta://auth/callback} \;\&\; \text{response\_type}=\text{code} \;\&\; \text{code\_challenge}=c \;\&\; \text{code\_challenge\_method}=\text{S256} \;\&\; \text{scope}=\text{openid+profile+email+offline\_access}$$
4. Upon deep-link return to `manta://auth/callback?code=AUTH_CODE`, the token endpoint is invoked:
   $$\text{POST } \text{KEYCLOAK\_URL} + \text{/protocol/openid-connect/token} \quad [\text{grant\_type}=\text{authorization\_code}, \text{code}=\text{AUTH\_CODE}, \text{code\_verifier}=v, \text{client\_id}=\text{manta-mobile}, \text{redirect\_uri}=\text{manta://auth/callback}]$$
5. The verifier $v$ is zeroed in memory immediately following successful or failed token resolution.

---

### Invariant 2: Tink AEAD Keystore-Backed At-Rest Non-Plaintext Invariant

For any token or sensitive metadata persisted to Jetpack DataStore:
1. **Master Key:** Hardware-backed root master key resided in Android Keystore:
   $$\text{URI} = \text{android-keystore://manta\_master\_key}$$
2. **Authenticated Encryption:** Google Tink `AES256_GCM` authenticated cipher:
   $$\text{Ciphertext } C = \text{AES-256-GCM}_{\text{Key}}(\text{Plaintext } P, \text{AssociatedData } \emptyset)$$
   $$C = \text{IV}_{12\text{ bytes}} \,\|\, \text{EncryptedBytes}_{N} \,\|\, \text{AuthTag}_{16\text{ bytes}}$$
3. **Protobuf Storage:**
   $$\text{PrefValue} = \text{BASE64}(C)$$
   Stored in `/data/data/com.manta.mobile/files/datastore/manta_encrypted_prefs.preferences_pb`.
4. **Physical Disk Plaintext Absence Proof:**
   $$\forall s \in \{\text{access\_token}, \text{refresh\_token}, \text{id\_token}, \text{user\_email}, \text{user\_name}\}: \quad s \not\subset \text{RawBytes}(\text{manta\_encrypted\_prefs.preferences\_pb})$$
   Neither token headers (`eyJ...`), email strings (`@manta.com`), nor signature substrings appear in cleartext anywhere within the on-disk storage file.

---

### Invariant 3: Clean User Role Derivation & Profile Decoupling

When storing and presenting user identity from JWT claims:
1. Standard Keycloak infrastructure realm roles (`default-roles-manta`, `offline_access`, `uma_authorization`) are filtered out.
2. The primary user role is derived deterministically:
   $$\text{Role}_{\text{primary}} = \text{first\_of}(\text{roles} \setminus \{\text{default-roles-*}, \text{offline\_access}, \text{uma\_authorization}\}) \;\lor\; \text{roles}[0]$$
3. Stored profile keys in `EncryptedStorage`:
   - `auth_access_token` $\rightarrow \text{Ciphertext}(\text{JWT})$
   - `auth_refresh_token` $\rightarrow \text{Ciphertext}(\text{RefreshToken})$
   - `auth_id_token` $\rightarrow \text{Ciphertext}(\text{IdToken})$
   - `auth_user_name` $\rightarrow \text{Ciphertext}(\text{name})$
   - `auth_user_email` $\rightarrow \text{Ciphertext}(\text{email})$
   - `auth_primary_role` $\rightarrow \text{Ciphertext}(\text{Role}_{\text{primary}})$

---

### Invariant 4: Biometric Unlock Prompt Execution & AuthState State Machine

The application state machine strictly dictates navigation and screen visibility:

$$\text{AuthState} \in \{\text{Unauthenticated}, \text{LockedRequiresBiometric}, \text{Authenticated}, \text{ReauthRequired}\}$$
$$\text{BiometricUnlockSubState} \in \{\text{Ready}, \text{Verifying}, \text{Success}, \text{Failed}(\text{attemptsLeft}), \text{Unavailable}\}$$

1. **Cold Start with Tokens:** Transitions directly to $\text{AuthState.LockedRequiresBiometric}(\text{subState}=\text{Ready})$.
2. **Biometric Trigger:** SubState transitions $\text{Ready} \rightarrow \text{Verifying}$, invoking `BiometricPrompt.authenticate()`.
3. **On Success:** SubState transitions $\text{Verifying} \rightarrow \text{Success}$ ($\text{delay } 1000\text{ms}$ progress feedback) $\rightarrow$ $\text{AuthState.Authenticated}(\text{UserSession})$.
4. **On Sensor Failure:** SubState transitions $\text{Verifying} \rightarrow \text{Failed}(\text{attemptsLeft} = \max(0, 3 - \text{failures}))$.

---

### Invariant 5: 3-Attempt Lockout & Hard Credential Fallback Invariant

1. **Attempt Counter:** $\text{Failures} \in \{0, 1, 2, 3\}$.
2. **Threshold Violation:** When $\text{Failures} \ge 3$ or when Android OS returns `BIOMETRIC_ERROR_LOCKOUT` / `BIOMETRIC_ERROR_LOCKOUT_PERMANENT`, the state machine forces:
   $$\text{subState} = \text{BiometricUnlockSubState.Unavailable}$$
3. **Hard Fallback Action:** The user cannot retry biometrics. The only permitted action is `SIGN IN WITH ORGANIZATION SSO` (`onSsoClick`):
   - Invokes `authRepository.logout()`, wiping all encrypted keys in DataStore.
   - Triggers `PkceAuthManager.launchOidcFlow()`, redirecting to Keycloak full credential entry.

---

### Invariant 6: Anti-Bypass Protected Route Guard Invariant

1. The root UI content router evaluates `authState`:
   - If `AuthState.Unauthenticated` or `AuthState.ReauthRequired` $\rightarrow$ Render `SignInScreen`.
   - If `AuthState.LockedRequiresBiometric` $\rightarrow$ Render `BiometricUnlockScreen`. Under no circumstances can the user interact with or observe `NavShell` (Alerts, Projects, Actions, Settings).
   - If `AuthState.Authenticated` $\rightarrow$ Render `NavShell`.
2. Any deep link (e.g. `manta://projects/123`) arriving while in `LockedRequiresBiometric` is deferred until `unlockSession()` resolves `AuthState.Authenticated`.

---

## 3. Mobile Authentication State Machine & Sequence Diagrams

### 3.1 Formal State Machine Diagram

```
                              +--------------------------+
                              |     Cold Start App       |
                              +--------------------------+
                                           |
                    Tokens in DataStore?   |
                    +----------------------+----------------------+
                    | NO                                          | YES
                    v                                             v
     +-------------------------------+             +----------------------------------+
     |   AuthState.Unauthenticated   |             | AuthState.LockedRequiresBiometric|
     |     (Renders SignInScreen)    |             |  (subState = Ready / Verifying)  |
     +-------------------------------+             +----------------------------------+
                    |                                             |
         Click SSO / Password                                     | Biometric Prompt
                    |                                             v
                    v                               +----------------------------+
     +-------------------------------+              | BiometricPrompt.Result?    |
     | PkceFlowState.Authorizing     |              +----------------------------+
     | (Keycloak Browser Auth Code)  |                 /          |            \
     +-------------------------------+         Success/     Fail  |     Cancel  \ 3x Fail / Lockout
                    |                                /    (att < 3)        (att=0)|
                    v                               v             v               v
     +-------------------------------+   +-----------+  +-----------+  +-------------+
     | PkceFlowState.ExchangingCode  |   | SubState. |  | SubState. |  | SubState.   |
     | (Token Exchange + AES-256-GCM)|   |  Success  |  | Failed(N) |  | Unavailable |
     +-------------------------------+   +-----------+  +-----------+  +-------------+
                    |                          |              |               |
                    | Complete                 | Delay 1s     | Retry         | Click SSO
                    v                          v              v               v
     +-------------------------------+   +-----------+        |        +-------------+
     |   AuthState.Authenticated     |<--+  Unlock   |        +------->|   Logout    |
     |     (Renders NavShell)        |   |  Session  |                 | & PKCE SSO  |
     +-------------------------------+   +-----------+                 +-------------+
```

---

### 3.2 Sequence Diagram: Initial OIDC PKCE Sign-In & Non-Plaintext Persistence (AC-1)

```
User               SignInScreen         PkceAuthManager       SystemBrowser / IdP       AuthRepository       EncryptedStorage / DataStore
 |                      |                      |                       |                       |                          |
 |-- 1. Click SSO ----->|                      |                       |                       |                          |
 |                      |-- 2. launchOidc() -->|                       |                       |                          |
 |                      |                      |-- 3. Gen verifier v ->|                       |                          |
 |                      |                      |-- 4. Gen S256 chal c->|                       |                          |
 |                      |                      |-- 5. Launch URL ----->|                       |                          |
 |                      |                      |                       |-- 6. Enter creds ---->|                          |
 |                      |                      |                       |-- 7. Validate PKCE -->|                          |
 |                      |                      |<-- 8. Deep-Link Code -|                       |                          |
 |                      |                      |    (manta://auth/callback?code=X)             |                          |
 |                      |                      |-- 9. Exchange Token ->|                       |                          |
 |                      |                      |<-- 10. Return JWTs ---|                       |                          |
 |                      |                      |-- 11. saveTokens(JWT) ----------------------->|                          |
 |                      |                      |                                               |-- 12. Parse Claims ----->|
 |                      |                      |                                               |-- 13. Tink Encrypt GCM ->|
 |                      |                      |                                               |       (Ciphertext C)     |
 |                      |                      |                                               |-- 14. Write DataStore -->|
 |                      |                      |                                               |       (Pref: Base64(C))  |
 |                      |                      |<-- 15. Success -------------------------------|                          |
 |                      |<-- 16. State = Authenticated ----------------------------------------|                          |
 |<-- 17. Render NavShell (Alerts) ------------|                                               |                          |
```

---

### 3.3 Sequence Diagram: App Cold Start & Successful Biometric Unlock (AC-2)

```
User               MainActivity        AuthRepository      BiometricPromptManager     BiometricUnlockScreen      NavShell
 |                      |                    |                       |                          |                    |
 |-- 1. Launch App ---->|                    |                       |                          |                    |
 |                      |-- 2. checkState -->|                       |                          |                    |
 |                      |                    |-- 3. Read tokens ---->|                          |                    |
 |                      |                    |<-- 4. Stored Tokens --|                          |                    |
 |                      |<-- 5. LockedRequiresBiometric(Ready) ------|                          |                    |
 |                      |-- 6. SetContent ----------------------------------------------------->|                    |
 |                      |    (Renders Verify Identity, UserProfileCard, Sensor Ready Chip)      |                    |
 |                      |-- 7. canAuthenticate() ------------------->|                          |                    |
 |                      |                                            |-- 8. BIOMETRIC_SUCCESS ->|                    |
 |                      |-- 9. showBiometricPrompt() --------------->|                          |                    |
 |                      |                                            |-- 10. Display Dialog --->|                    |
 |-- 11. Scan Finger -->|                                            |                          |                    |
 |                      |                                            |-- 12. onAuthSucceeded -->|                    |
 |                      |<-- 13. Result.AuthenticationSuccess -------|                          |                    |
 |                      |-- 14. updateSubState(Success) --------------------------------------->|                    |
 |                      |    (Renders "Identity Verified", "Redirecting...", Progress bar)      |                    |
 |                      |-- 15. Delay 1000ms -------------------------------------------------->|                    |
 |                      |-- 16. unlockSession() -------------------->|                          |                    |
 |                      |                                            |-- 17. Decrypt JWTs ----->|                    |
 |                      |<-- 18. AuthState.Authenticated(session) ---|                          |                    |
 |<-- 19. Render NavShell (Alerts, Projects, Actions, Settings) -----------------------------------------------------|
```

---

### 3.4 Sequence Diagram: Biometric Prompt Rejection, 3-Attempt Lockout & Hard Fallback (AC-3)

```
User               MainActivity        BiometricPromptManager     BiometricUnlockScreen     AuthRepository     SignInScreen
 |                      |                       |                          |                      |                 |
 |-- 1. Fail Finger 1 ->|                       |                          |                      |                 |
 |                      |                       |-- 2. onAuthFailed ------>|                      |                 |
 |                      |<-- 3. Result.Failed --|                          |                      |                 |
 |                      |-- 4. attemptsLeft = 2 -------------------------->|                      |                 |
 |                      |    (Status: "Not Recognized", "LOCKOUT IN 2 Attempts", "Retry Button")  |                 |
 |-- 5. Click Retry --->|-- 6. Re-trigger prompt |                          |                      |                 |
 |-- 7. Fail Finger 2 ->|<-- 8. Result.Failed --|                          |                      |                 |
 |                      |-- 9. attemptsLeft = 1 -------------------------->|                      |                 |
 |-- 10. Fail Finger 3 >|<-- 11. Result.Failed -|                          |                      |                 |
 |                      |-- 12. attemptsLeft = 0 (Lockout Triggered) ----->|                      |                 |
 |                      |    (Status: "SENSOR UNAVAILABLE", "SIGN IN WITH ORGANIZATION SSO")      |                 |
 |-- 13. Click SSO ---->|                                                  |                      |                 |
 |                      |-- 14. onSsoClick() ----------------------------->|                      |                 |
 |                      |-- 15. logout() -------------------------------------------------------->|                 |
 |                      |                                                                         |-- 16. Purge --->|
 |                      |                                                                         |   DataStore     |
 |                      |<-- 17. AuthState.Unauthenticated ---------------------------------------|                 |
 |<-- 18. Redirect to SignInScreen (Full credentials required, NavShell completely blocked) ------------------------|
```

---

## 4. Master Test Scenarios Matrix

| Scenario ID | Scenario Name | Target AC | Category | Preconditions | Trigger / Action | Expected Result | Automated Suite |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`TC-MOB-AUTH-001`** | **OIDC PKCE Authorization URL & S256 Challenge Verification** | `AC-1` | Security / Protocol | App freshly installed, `Unauthenticated`. | Click "Sign In with Organization SSO". | Generates 128-char verifier $v$, computes $c=\text{BASE64URL}(\text{SHA256}(v))$. Custom Tab opens Keycloak with `code_challenge_method=S256`. | `PkceAuthManagerTest.kt` |
| **`TC-MOB-AUTH-002`** | **Deep-Link Callback & PKCE Token Exchange** | `AC-1` | Functional / Integration | Custom Tab completes Keycloak auth (`alpha_dev`). | Keycloak invokes `manta://auth/callback?code=CODE`. | `handleAuthorizationResponse` posts code and verifier to `/token`. Keycloak returns valid RS256 `access_token`, `refresh_token`, `id_token`. | `PkceAuthManagerTest.kt` |
| **`TC-MOB-AUTH-003`** | **Jetpack DataStore AES-256-GCM Non-Plaintext Disk Invariant** | `AC-1` | Security / At-Rest | Tokens acquired via OIDC callback. | `authRepository.saveTokens()` executes. Inspect `/data/data/.../preferences_pb` binary. | **Zero plaintext tokens or emails found on disk.** File contains only protobuf entries of Base64 Tink AEAD ciphertexts with 12-byte IV and 16-byte tag. | `AuthRepositoryTest.kt` |
| **`TC-MOB-AUTH-004`** | **Clean User Role & Profile Metadata Extraction** | `AC-1` | Data / Mapping | JWT has `default-roles-manta`, `offline_access`, `DEVELOPER`. | `saveTokens()` stores profile. | `auth_primary_role` strictly saved as `DEVELOPER`. Default system roles filtered. `auth_user_name` and `auth_user_email` encrypted. | `AuthRepositoryTest.kt` |
| **`TC-MOB-AUTH-005`** | **Cold Start with Encrypted DataStore Tokens** | `AC-2` | Lifecycle / State | App closed; encrypted tokens present on disk. | Launch app process (`MainActivity.onCreate`). | Reads encrypted tokens, transitions to `AuthState.LockedRequiresBiometric(Ready)`. Renders `BiometricUnlockScreen` with UserProfileCard. | `AuthRepositoryTest.kt` |
| **`TC-MOB-AUTH-006`** | **BiometricPrompt Hardware Check & Auto-Invocation** | `AC-2` | Hardware / UX | Device has enrolled fingerprint sensor. | `BiometricUnlockScreen` enters composition with `Ready`. | `BiometricPromptManager.canAuthenticate()` returns `Ready`. `BiometricPrompt` modal opens with title "Unlock MANTA Workspace". | `BiometricUnlockScreenTest.kt` |
| **`TC-MOB-AUTH-007`** | **Successful Biometric Scan & Session Unlock** | `AC-2` | Functional / UX | `BiometricPrompt` modal active. | User applies valid enrolled fingerprint. | `onAuthenticationSucceeded` emits `Success`. SubState updates to `Success`. After 1s progress animation, session unlocks and displays `NavShell`. | `BiometricUnlockScreenTest.kt` |
| **`TC-MOB-AUTH-008`** | **Transient Biometric Scan Failure & Attempt Decrement** | `AC-3` | Negative / Fallback | `BiometricPrompt` active (attempt 1). | User applies non-matching finger. | `onAuthenticationFailed` decrements attempts. Substate becomes `Failed(attemptsLeft = 2)`. UI displays "● Not Recognized", "LOCKOUT IN 2 Attempts", and Retry button. | `BiometricUnlockScreenTest.kt` |
| **`TC-MOB-AUTH-009`** | **3-Attempt Lockout Exhaustion & Sensor Disabling** | `AC-3` | Security / Lockout | User has failed 2 previous attempts. | User applies non-matching finger 3rd time. | SubState transitions to `Unavailable`. UI displays "● SENSOR UNAVAILABLE", hides Retry button, and presents "SIGN IN WITH ORGANIZATION SSO". | `BiometricUnlockScreenTest.kt` |
| **`TC-MOB-AUTH-010`** | **Biometric Prompt Explicit User Cancellation** | `AC-3` | Fallback / Negative | `BiometricPrompt` modal active. | User taps "Cancel" or backdrop outside. | `onAuthenticationError` receives `ERROR_USER_CANCELED` / `ERROR_NEGATIVE_BUTTON`. Dashboard remains strictly locked; UI prompts Retry or Switch. | `BiometricUnlockScreenTest.kt` |
| **`TC-MOB-AUTH-011`** | **Switch User Action & Local Token Purge** | `AC-3` | Security / Lifecycle | User on `BiometricUnlockScreen`. | User taps "Switch" button on `UserProfileCard`. | `authRepository.logout()` purges all DataStore keys. App transitions to `AuthState.Unauthenticated`. `SignInScreen` displayed. | `AuthRepositoryTest.kt` |
| **`TC-MOB-AUTH-012`** | **Device Without Enrolled Biometrics or Hardware Absent** | `AC-3` | Edge / Hardware | Device with no lockscreen or sensor hardware. | App relaunched with stored tokens. | `canAuthenticate()` returns `NotEnrolled` or `FeatureUnavailable`. Shows warning toast and renders `Unavailable` state with SSO fallback. | `BiometricUnlockScreenTest.kt` |

---

## 5. Detailed Test Scenario Specifications

### Scenario `TC-MOB-AUTH-001`: OIDC PKCE Authorization URL & S256 Challenge Verification (`AC-1`)

#### 5.1.1 Objective & Traceability
Verify that the mobile application initiates authentication utilizing standard PKCE with `code_challenge_method=S256` to prevent authorization code interception and cross-app injection attacks. Traces directly to `US-38` AC-1 and RFC 7636.

#### 5.1.2 Preconditions
- M.A.N.T.A. mobile application is freshly launched in `AuthState.Unauthenticated`.
- Keycloak 24.0.5 is running with realm `manta` and client `manta-mobile` configured with public access and PKCE enforced.

#### 5.1.3 Execution Sequence & Concrete Inputs
1. Render `SignInScreen`.
2. Tap `[data-testid="company-sso-button"]` ("Sign In with Organization SSO").
3. Inspect the intent and generated URI dispatched to Android Custom Tab:
   ```text
   Uri: http://10.0.2.2:8080/realms/manta/protocol/openid-connect/auth?
     response_type=code&
     client_id=manta-mobile&
     redirect_uri=manta%3A%2F%2Fauth%2Fcallback&
     code_challenge=E9Melhoa2OwvFrGMTJguCH5ZwKRKg5Ub46-dgWDCe_k&
     code_challenge_method=S256&
     scope=openid+profile+email+offline_access
   ```

#### 5.1.4 Verification Assertions
```kotlin
// In PkceAuthManagerTest / Robolectric verification:
val verifier = PkceUtils.generateCodeVerifier()
assertTrue(verifier.length in 43..128)
assertTrue(verifier.all { it in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~" })

val challenge = PkceUtils.generateCodeChallenge(verifier)
val expectedChallenge = Base64.getUrlEncoder().withoutPadding().encodeToString(
    MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII))
)
assertEquals(expectedChallenge, challenge)
assertEquals(PkceFlowState.Authorizing, PkceAuthManager.flowState.value)
```

---

### Scenario `TC-MOB-AUTH-002`: Deep-Link Callback Interception & Token Exchange (`AC-1`)

#### 5.2.1 Objective & Traceability
Verify that Keycloak's redirect to `manta://auth/callback` is handled by `MainActivity`, which extracts the authorization code, verifies the original code verifier, and exchanges it for JWT credentials. Traces to `US-38` AC-1.

#### 5.2.2 Preconditions
- User has authenticated on Keycloak as `alpha_dev` (`Alpha Developer`, `Tenant Alpha`).
- Keycloak generates authorization code `auth_code_9a8b7c6d5e`.
- `currentCodeVerifier` is retained in memory.

#### 5.2.3 Execution Sequence & Concrete Inputs
1. Deliver intent to `MainActivity.onNewIntent()`:
   ```kotlin
   val callbackIntent = Intent(Intent.ACTION_VIEW).apply {
       data = Uri.parse("manta://auth/callback?code=auth_code_9a8b7c6d5e&state=xyz")
   }
   activity.onNewIntent(callbackIntent)
   ```
2. `PkceAuthManager.handleAuthorizationResponse()` executes:
   - Posts authorization code and code verifier to `http://10.0.2.2:8080/realms/manta/protocol/openid-connect/token`.
3. Keycloak validates $c == \text{BASE64URL}(\text{SHA256}(v))$ and returns:
   ```json
   {
     "access_token": "eyJhbGciOiJSUzI1NiIs...",
     "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
     "id_token": "eyJhbGciOiJSUzI1NiIs...",
     "token_type": "Bearer",
     "expires_in": 300
   }
   ```

#### 5.2.4 Verification Assertions
```kotlin
val result = PkceAuthManager.handleAuthorizationResponse(callbackUri, authRepository)
assertTrue(result)
assertEquals(PkceFlowState.Success, PkceAuthManager.flowState.value)
assertTrue(authRepository.authState.value is AuthState.Authenticated)
```

---

### Scenario `TC-MOB-AUTH-003`: Jetpack DataStore Protobuf Non-Plaintext Disk Invariant (`AC-1`)

#### 5.3.1 Objective & Traceability
Formally verify that token persistence uses Google Tink AEAD `AES256_GCM` backed by Android Keystore, guaranteeing that no plaintext secrets, tokens, or PII can be extracted from physical flash storage. Traces to `US-38` AC-1 and Enterprise Mobile Security Invariant 2.

#### 5.3.2 Preconditions
- `authRepository.saveTokens()` has executed with `mockAccessToken`, `mockRefreshToken`, and user email `developer@manta.com`.
- Jetpack DataStore has flushed to `/data/data/com.manta.mobile/files/datastore/manta_encrypted_prefs.preferences_pb`.

#### 5.3.3 Execution Sequence & Concrete Inputs
1. Read raw binary bytes directly from the on-disk protobuf file:
   ```kotlin
   val fileBytes = File(context.filesDir, "datastore/manta_encrypted_prefs.preferences_pb").readBytes()
   val fileContentString = String(fileBytes, Charsets.ISO_8859_1)
   ```
2. Scan the file for any plaintext occurrences of tokens, email, or user names:
   ```kotlin
   val plainStringsToSearch = listOf(
       "developer@manta.com",
       "Manta Developer",
       "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9",
       "tenant-uuid-5678",
       "mock_refresh_token"
   )
   ```

#### 5.3.4 Verification Assertions
```kotlin
// Assert ZERO plaintext occurrences exist in the raw disk bytes
for (secret in plainStringsToSearch) {
    assertFalse(
        "CRITICAL SECURITY VIOLATION: Plaintext '$secret' found on disk!",
        fileContentString.contains(secret)
    )
}

// Assert keys exist as Base64-encoded encrypted strings
val rawDataStoreMap = dataStore.data.first().asMap()
for ((key, value) in rawDataStoreMap) {
    assertTrue(value is String)
    val decodedBytes = Base64.getDecoder().decode(value as String)
    // AES-256-GCM minimum overhead: 12-byte IV + 16-byte Auth Tag = 28 bytes
    assertTrue("Ciphertext too short: ${decodedBytes.size} bytes", decodedBytes.size >= 28)
}
```

---

### Scenario `TC-MOB-AUTH-004`: Clean User Role & Profile Metadata Extraction (`AC-1`)

#### 5.4.1 Objective & Traceability
Verify that `AuthRepository.saveTokens()` parses JWT claims, ignores standard Keycloak system roles (`default-roles-manta`, `offline_access`, `uma_authorization`), isolates the primary user role (`DEVELOPER`), and encrypts user profile metadata for offline biometric rendering. Traces to `US-38` AC-1.

#### 5.4.2 Preconditions
- Input `access_token` JWT contains payload:
  ```json
  {
    "sub": "user-uuid-1234",
    "name": "Manta Developer",
    "email": "developer@manta.com",
    "tenant_id": "tenant-uuid-5678",
    "realm_access": {
      "roles": ["default-roles-manta", "offline_access", "DEVELOPER"]
    }
  }
  ```

#### 5.4.3 Execution Sequence
1. Invoke `authRepository.saveTokens(accessToken, refreshToken, idToken)`.
2. Inspect values decrypted via `encryptedStorage.readString()`.

#### 5.4.4 Verification Assertions
```kotlin
assertEquals("DEVELOPER", encryptedStorage.readString("auth_primary_role"))
assertEquals("Manta Developer", encryptedStorage.readString("auth_user_name"))
assertEquals("developer@manta.com", encryptedStorage.readString("auth_user_email"))

val session = (authRepository.authState.value as AuthState.Authenticated).session
assertEquals("user-uuid-1234", session.userId)
assertEquals("tenant-uuid-5678", session.tenantId)
assertTrue(session.roles.contains("DEVELOPER"))
```

---

### Scenario `TC-MOB-AUTH-005`: Cold Start Relaunch with Stored Tokens & State Transition (`AC-2`)

#### 5.5.1 Objective & Traceability
Verify that cold-starting or relaunching the application when encrypted tokens reside in DataStore immediately transitions `AuthState` to `LockedRequiresBiometric`, populates user profile metadata, and displays `BiometricUnlockScreen` without exposing the dashboard. Traces to `US-38` AC-2.

#### 5.5.2 Preconditions
- Stored tokens and metadata exist in `EncryptedStorage`.
- Application process is launched fresh (`MainActivity.onCreate`).

#### 5.5.3 Execution Sequence
1. Call `authRepository.checkInitialAuthState()`.
2. Observe state emitted on `authRepository.authState`.

#### 5.5.4 Verification Assertions
```kotlin
val state = authRepository.checkInitialAuthState()
assertTrue(state is AuthState.LockedRequiresBiometric)

val lockedState = state as AuthState.LockedRequiresBiometric
assertEquals("Manta Developer", lockedState.userName)
assertEquals("developer@manta.com", lockedState.userEmail)
assertEquals("DEVELOPER", lockedState.primaryRole)
assertEquals(BiometricUnlockSubState.Ready, lockedState.subState)

// UI Assertion
composeTestRule.onNodeWithText("Verify Identity").assertIsDisplayed()
composeTestRule.onNodeWithText("Manta Developer").assertIsDisplayed()
composeTestRule.onNodeWithText("developer@manta.com").assertIsDisplayed()
composeTestRule.onNodeWithText("DEVELOPER").assertIsDisplayed()
composeTestRule.onNodeWithText("● Sensor is Ready").assertIsDisplayed()
```

---

### Scenario `TC-MOB-AUTH-006`: BiometricPrompt Hardware Check & Auto-Invocation (`AC-2`)

#### 5.6.1 Objective & Traceability
Verify that `BiometricPromptManager.canAuthenticate()` queries device capabilities with authenticators `BIOMETRIC_STRONG | BIOMETRIC_WEAK | DEVICE_CREDENTIAL` (and legacy Android 9 / Samsung fingerprint fallback), automatically displaying the system biometric dialog. Traces to `US-38` AC-2.

#### 5.6.2 Preconditions
- Device reports `BiometricManager.BIOMETRIC_SUCCESS`.
- `AuthState` is `LockedRequiresBiometric(Ready)`.

#### 5.6.3 Execution Sequence
1. `MainActivity` detects `subState == Ready` inside `LaunchedEffect(Unit)`.
2. Calls `triggerBiometricUnlock()`.
3. `authRepository.updateBiometricSubState(BiometricUnlockSubState.Verifying)`.
4. `biometricPromptManager.showBiometricPrompt("Unlock MANTA Workspace", "Scan your fingerprint or face to proceed")`.

#### 5.6.4 Verification Assertions
```kotlin
assertEquals(BiometricPromptManager.BiometricStatus.Ready, biometricPromptManager.canAuthenticate())
assertEquals(
    BiometricUnlockSubState.Verifying,
    (authRepository.authState.value as AuthState.LockedRequiresBiometric).subState
)
```

---

### Scenario `TC-MOB-AUTH-007`: Successful Biometric Scan & Automatic Session Unlock (`AC-2`)

#### 5.7.1 Objective & Traceability
Verify that upon receiving a successful biometric callback from hardware, the app transitions through `BiometricUnlockSubState.Success`, decrypts tokens in memory, updates `AuthState.Authenticated`, and navigates to `NavShell`. Traces to `US-38` AC-2.

#### 5.7.2 Preconditions
- Biometric prompt is active on screen in `Verifying` state.

#### 5.7.3 Execution Sequence
1. Emulate successful biometric scan:
   ```kotlin
   biometricCallback.onAuthenticationSucceeded(mockAuthenticationResult)
   ```
2. `resultChannel` receives `BiometricResult.AuthenticationSuccess`.
3. UI substate transitions to `BiometricUnlockSubState.Success`.
4. Linear progress indicator animates (`Redirecting...`, `● Successfully Verified`).
5. After 1000ms delay, `authRepository.unlockSession()` executes.

#### 5.7.4 Verification Assertions
```kotlin
// Compose assertions during Success substate
composeTestRule.onNodeWithText("Identity Verified").assertIsDisplayed()
composeTestRule.onNodeWithText("● Successfully Verified").assertIsDisplayed()
composeTestRule.onNodeWithText("Redirecting...").assertIsDisplayed()

// Post-unlock assertions
assertTrue(authRepository.authState.value is AuthState.Authenticated)
val session = (authRepository.authState.value as AuthState.Authenticated).session
assertEquals("user-uuid-1234", session.userId)
assertEquals(mockAccessToken, session.accessToken)

// Navigation assertion: NavShell is rendered, BiometricUnlockScreen dismissed
composeTestRule.onNodeWithText("Alerts").assertIsDisplayed()
```

---

### Scenario `TC-MOB-AUTH-008`: Transient Biometric Failure & Attempt Decrement (`AC-3`)

#### 5.8.1 Objective & Traceability
Verify that when biometric sensor capture fails (e.g. dirty sensor, non-matching fingerprint), the app remains locked, increments failure count, displays attempts remaining, and offers a retry button. Traces to `US-38` AC-3.

#### 5.8.2 Preconditions
- Biometric prompt active; `failedAttemptCount = 0`.

#### 5.8.3 Execution Sequence
1. Emulate sensor mismatch:
   ```kotlin
   biometricCallback.onAuthenticationFailed()
   ```
2. `MainActivity` increments `failedAttemptCount` to `1`.
3. Computes `attemptsLeft = (3 - 1) = 2`.
4. Emits `BiometricUnlockSubState.Failed(attemptsLeft = 2)`.

#### 5.8.4 Verification Assertions
```kotlin
composeTestRule.onNodeWithText("Verification Failed").assertIsDisplayed()
composeTestRule.onNodeWithText("Attempt 2 of 3").assertIsDisplayed()
composeTestRule.onNodeWithText("● Not Recognized • Try Again").assertIsDisplayed()
composeTestRule.onNodeWithText("LOCKOUT IN").assertIsDisplayed()
composeTestRule.onNodeWithText("2 Attempts").assertIsDisplayed()
composeTestRule.onNodeWithText("Retry Biometrics").assertIsDisplayed()

// Clicking Retry re-opens prompt
composeTestRule.onNodeWithText("Retry Biometrics").performClick()
assertEquals(
    BiometricUnlockSubState.Ready,
    (authRepository.authState.value as AuthState.LockedRequiresBiometric).subState
)
```

---

### Scenario `TC-MOB-AUTH-009`: 3-Attempt Lockout Exhaustion & Sensor Disabling (`AC-3`)

#### 5.9.1 Objective & Traceability
Verify that after 3 consecutive failed biometric attempts or an OS biometric lockout error (`BIOMETRIC_ERROR_LOCKOUT`), biometric retry is permanently disabled, dashboard access is strictly blocked, and the user is forced into full Organization SSO re-authentication. Traces to `US-38` AC-3.

#### 5.9.2 Preconditions
- User has failed 2 previous attempts (`failedAttemptCount = 2`).

#### 5.9.3 Execution Sequence
1. Emulate 3rd consecutive failed scan:
   ```kotlin
   biometricCallback.onAuthenticationFailed()
   ```
2. `failedAttemptCount` reaches `3`, computing `attemptsLeft = 0`.
3. `authRepository.updateBiometricSubState(BiometricUnlockSubState.Unavailable)`.
4. User taps `[data-testid="sso-fallback-button"]` ("SIGN IN WITH ORGANIZATION SSO").

#### 5.9.4 Verification Assertions
```kotlin
// UI Assertions
composeTestRule.onNodeWithText("Biometrics Unavailable").assertIsDisplayed()
composeTestRule.onNodeWithText("● SENSOR UNAVAILABLE").assertIsDisplayed()
composeTestRule.onNodeWithText("Biometric is disabled. Re-verify identity using Organization SSO.").assertIsDisplayed()
composeTestRule.onNodeWithText("SIGN IN WITH ORGANIZATION SSO").assertIsDisplayed()
composeTestRule.onNodeWithText("Retry Biometrics").assertDoesNotExist()

// Fallback Action: Clears local session and starts fresh PKCE OIDC flow
composeTestRule.onNodeWithText("SIGN IN WITH ORGANIZATION SSO").performClick()
assertEquals(AuthState.Unauthenticated, authRepository.authState.value)
assertNull(encryptedStorage.readString("auth_access_token"))
assertNull(encryptedStorage.readString("auth_refresh_token"))
assertEquals(PkceFlowState.Authorizing, PkceAuthManager.flowState.value)
```

---

### Scenario `TC-MOB-AUTH-010`: Biometric Prompt Explicit User Cancellation (`AC-3`)

#### 5.10.1 Objective & Traceability
Verify that if the user explicitly cancels or dismisses the system biometric dialog, the session remains locked, no token data is released, and the dashboard remains inaccessible. Traces to `US-38` AC-3.

#### 5.10.2 Preconditions
- Biometric prompt active on screen.

#### 5.10.3 Execution Sequence
1. Emulate negative button / cancel tap:
   ```kotlin
   biometricCallback.onAuthenticationError(
       BiometricPrompt.ERROR_USER_CANCELED,
       "User canceled the authentication."
   )
   ```
2. Result emitted on `promptResults` channel.

#### 5.10.4 Verification Assertions
```kotlin
val currentState = authRepository.authState.value
assertTrue(currentState is AuthState.LockedRequiresBiometric)
val subState = (currentState as AuthState.LockedRequiresBiometric).subState
assertTrue(subState is BiometricUnlockSubState.Failed)
assertEquals(2, (subState as BiometricUnlockSubState.Failed).attemptsLeft)

// Verify dashboard cannot be accessed
composeTestRule.onNodeWithText("Alerts").assertDoesNotExist()
composeTestRule.onNodeWithText("Project").assertDoesNotExist()
```

---

### Scenario `TC-MOB-AUTH-011`: Switch User Action & Local Token Purge (`AC-3`)

#### 5.11.1 Objective & Traceability
Verify that clicking "Switch" on the `UserProfileCard` immediately executes `authRepository.logout()`, deletes all encrypted tokens and cached PII from DataStore, and returns to `SignInScreen`. Traces to `US-38` AC-3.

#### 5.11.2 Preconditions
- User on `BiometricUnlockScreen` with cached profile (`Alex Mercer`).

#### 5.11.3 Execution Sequence
1. Tap "Switch" button in the `UserProfileCard` header:
   ```kotlin
   composeTestRule.onNodeWithText("Switch").performClick()
   ```

#### 5.11.4 Verification Assertions
```kotlin
assertEquals(AuthState.Unauthenticated, authRepository.authState.value)
assertNull(encryptedStorage.readString("auth_access_token"))
assertNull(encryptedStorage.readString("auth_refresh_token"))
assertNull(encryptedStorage.readString("auth_id_token"))
assertNull(encryptedStorage.readString("auth_user_name"))
assertNull(encryptedStorage.readString("auth_user_email"))
assertNull(encryptedStorage.readString("auth_primary_role"))

// Verify SignInScreen is displayed with input fields
composeTestRule.onNodeWithText("M.A.N.T.A.").assertIsDisplayed()
composeTestRule.onNodeWithText("Organization Email").assertIsDisplayed()
composeTestRule.onNodeWithText("Password").assertIsDisplayed()
```

---

### Scenario `TC-MOB-AUTH-012`: Device Without Enrolled Biometrics or Hardware Absent (`AC-3`)

#### 5.12.1 Objective & Traceability
Verify proper defensive degradation when the host device has no biometric hardware (`BIOMETRIC_ERROR_NO_HARDWARE`) or the user has not configured a fingerprint/face lock (`BIOMETRIC_ERROR_NONE_ENROLLED`). Traces to `US-38` AC-3.

#### 5.12.2 Preconditions
- Android device or emulator with no fingerprint enrolled in system settings.

#### 5.12.3 Execution Sequence
1. Launch app with stored tokens.
2. `biometricPromptManager.canAuthenticate()` evaluates device state.

#### 5.12.4 Verification Assertions
```kotlin
assertEquals(BiometricPromptManager.BiometricStatus.NotEnrolled, biometricPromptManager.canAuthenticate())
// State machine immediately enters Unavailable
assertEquals(
    BiometricUnlockSubState.Unavailable,
    (authRepository.authState.value as AuthState.LockedRequiresBiometric).subState
)
composeTestRule.onNodeWithText("● SENSOR UNAVAILABLE").assertIsDisplayed()
composeTestRule.onNodeWithText("SIGN IN WITH ORGANIZATION SSO").assertIsDisplayed()
```

---

## 6. Automated Test Suites & Verification Execution Guide

### 6.1 Automated Test Class & Coverage Matrix

| Test Suite File | Test Methods | Scenarios Covered | Target Acceptance Criteria |
| :--- | :--- | :--- | :--- |
| [`mobile/app/src/test/java/com/manta/mobile/features/auth/PkceAuthManagerTest.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/test/java/com/manta/mobile/features/auth/PkceAuthManagerTest.kt) | `resetState_resetsFlowStateToIdle`<br>`handleAuthorizationResponse_whenErrorParameterPresent_setsErrorState`<br>`handleAuthorizationResponse_whenCodeMissing_setsErrorState`<br>`handleAuthorizationResponse_whenVerifierMissing_setsErrorState` | `TC-MOB-AUTH-001`<br>`TC-MOB-AUTH-002` | `AC-1` (OIDC PKCE Protocol & Error Interception) |
| [`mobile/app/src/test/java/com/manta/mobile/data/auth/AuthRepositoryTest.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/test/java/com/manta/mobile/data/auth/AuthRepositoryTest.kt) | `checkInitialAuthState_whenNoTokensStored_returnsUnauthenticated`<br>`checkInitialAuthState_whenTokensStored_returnsLockedRequiresBiometricWithCachedProfile`<br>`updateBiometricSubState_updatesSubStateOfLockedRequiresBiometric`<br>`unlockSession_whenLockedAndTokensExist_transitionsToAuthenticated`<br>`unlockSession_whenNoTokensExist_returnsFalseAndUnauthenticated`<br>`saveTokens_filtersSystemRolesAndCachesPrimaryRole`<br>`logout_clearsTokensAndProfileMetadata` | `TC-MOB-AUTH-003`<br>`TC-MOB-AUTH-004`<br>`TC-MOB-AUTH-005`<br>`TC-MOB-AUTH-011` | `AC-1` (Disk At-Rest Encryption & Clean Roles)<br>`AC-2` (Initial State & Session Unlock)<br>`AC-3` (Session Logout & Purge) |
| [`mobile/app/src/test/java/com/manta/mobile/features/auth/BiometricUnlockScreenTest.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/test/java/com/manta/mobile/features/auth/BiometricUnlockScreenTest.kt) | `stateReady_displaysVerifyIdentityTitleAndSensorReadyChip`<br>`stateSuccess_displaysIdentityVerifiedTitleAndProgress`<br>`stateFailed_displaysVerificationFailedTitleAttemptCountAndRetryButton`<br>`stateFailed_clickingRetryButton_triggersRetryCallback`<br>`stateUnavailable_displaysBiometricsUnavailableTitleAndSsoButton`<br>`stateUnavailable_clickingSsoButton_triggersSsoCallback` | `TC-MOB-AUTH-006`<br>`TC-MOB-AUTH-007`<br>`TC-MOB-AUTH-008`<br>`TC-MOB-AUTH-009`<br>`TC-MOB-AUTH-010`<br>`TC-MOB-AUTH-012` | `AC-2` (Biometric UI Ready & Success Animation)<br>`AC-3` (Retry & 3-Attempt Lockout SSO Fallback) |
| [`mobile/app/src/test/java/com/manta/mobile/features/auth/SignInScreenTest.kt`](file:///c:/Users/Dell/OneDrive/Documents/GitHub/MANTA/mobile/app/src/test/java/com/manta/mobile/features/auth/SignInScreenTest.kt) | `signInScreen_displaysHeaderAndAllElements`<br>`signInScreen_clickingBiometricCard_triggersCallback` | `TC-MOB-AUTH-001`<br>`TC-MOB-AUTH-011` | `AC-1` (Sign-In UI & SSO Entry)<br>`AC-3` (Post-Fallback Re-entry Screen) |

---

### 6.2 Local Execution & CI Verification Commands

To verify all test scenarios in local development and continuous integration environments without device dependencies:

```bash
cd mobile

# 1. Run local JVM unit & Robolectric Compose tests (both dev and stage debug variants)
./gradlew testDevDebugUnitTest testStageDebugUnitTest

# 2. Check Kotlin coding standards & Ktlint rules
./gradlew ktlintCheck

# 3. Run Android Lint static analysis on debug variants
./gradlew lintDevDebug lintStageDebug

# 4. Verify debug APK compilation
./gradlew assembleDebug --build-cache
```

In the backend Python CI pipeline (to avoid any linter or formatting regressions in Markdown files):

```bash
# Check Python formatting and linting standards across the repository
python -m ruff format --check .
python -m ruff check .
```
