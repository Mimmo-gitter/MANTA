package com.manta.mobile.features.auth

import android.util.Log
import com.manta.mobile.BuildConfig
import com.manta.mobile.core.network.AuthApiService
import com.manta.mobile.core.network.DeviceApiService
import com.manta.mobile.core.network.RetrofitClientFactory
import com.manta.mobile.core.network.UserProfileResponse
import com.manta.mobile.core.security.EncryptedStorage
import com.manta.mobile.data.auth.AuthState
import com.manta.mobile.data.auth.BiometricUnlockSubState
import com.manta.mobile.data.auth.JwtUtils
import com.manta.mobile.data.auth.UserClaims
import com.manta.mobile.data.auth.UserSession
import com.manta.mobile.data.push.PushRegistrationManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import okhttp3.FormBody
import okhttp3.OkHttpClient
import okhttp3.Request

class AuthRepository(
    private val encryptedStorage: EncryptedStorage,
    private val deviceApiService: DeviceApiService? = null,
    private val authApiService: AuthApiService? = null,
    private val pushRegistrationManager: PushRegistrationManager? = null,
    private val httpClientProvider: () -> OkHttpClient = { OkHttpClient() }
) {
    private val _authState = MutableStateFlow<AuthState>(AuthState.Unauthenticated)
    val authState: StateFlow<AuthState> = _authState.asStateFlow()

    private val httpClient: OkHttpClient by lazy { httpClientProvider() }
    private val activeDeviceApiService: DeviceApiService by lazy {
        deviceApiService ?: RetrofitClientFactory.createAuthenticatedService(this)
    }
    private val activeAuthApiService: AuthApiService by lazy {
        authApiService ?: RetrofitClientFactory.createAuthenticatedService(this)
    }

    suspend fun getAccessToken(): String? = encryptedStorage.readString(KEY_ACCESS_TOKEN)
    suspend fun getRefreshToken(): String? = encryptedStorage.readString(KEY_REFRESH_TOKEN)
    suspend fun getIdToken(): String? = encryptedStorage.readString(KEY_ID_TOKEN)

    suspend fun fetchCurrentUser(): Result<UserProfileResponse> = withContext(Dispatchers.IO) {
        val currentToken = getAccessToken()
        if (currentToken != null) {
            Log.d(TAG, "Current JWT Payload: ${JwtUtils.getRawPayload(currentToken)}")
        }
        try {
            val response = activeAuthApiService.getCurrentUser()
            val body = response.body()
            if (response.isSuccessful && body != null) {
                Log.d(TAG, "Successfully fetched user profile from /api/v1/auth/me: ${body.name}")
                if (body.name.isNotBlank()) {
                    encryptedStorage.writeString(KEY_USER_NAME, body.name)
                }
                if (body.email.isNotBlank()) {
                    encryptedStorage.writeString(KEY_USER_EMAIL, body.email)
                }
                if (body.roles.isNotEmpty()) {
                    val userRole = body.roles.firstOrNull { role ->
                        !role.startsWith("default-roles") &&
                            role != "offline_access" &&
                            role != "uma_authorization"
                    } ?: body.roles.first()
                    encryptedStorage.writeString(KEY_PRIMARY_ROLE, userRole)
                }
                Result.success(body)
            } else {
                Log.e(TAG, "Failed to fetch user profile: HTTP ${response.code()}")
                Result.failure(Exception("Failed to fetch user profile (${response.code()})."))
            }
        } catch (e: Exception) {
            Log.e(TAG, "Network error fetching user profile: ${e.message}")
            Result.failure(e)
        }
    }

    suspend fun checkInitialAuthState(): AuthState {
        val accessToken = encryptedStorage.readString(KEY_ACCESS_TOKEN)
        val newState = if (accessToken.isNullOrBlank()) {
            AuthState.Unauthenticated
        } else {
            val userName = encryptedStorage.readString(KEY_USER_NAME)
            val userEmail = encryptedStorage.readString(KEY_USER_EMAIL)
            val primaryRole = encryptedStorage.readString(KEY_PRIMARY_ROLE)
            AuthState.LockedRequiresBiometric(
                userName = userName,
                userEmail = userEmail,
                primaryRole = primaryRole,
                subState = BiometricUnlockSubState.Ready
            )
        }
        _authState.value = newState
        return newState
    }

    fun updateBiometricSubState(subState: BiometricUnlockSubState) {
        val current = _authState.value
        if (current is AuthState.LockedRequiresBiometric) {
            _authState.value = current.copy(subState = subState)
        }
    }

    suspend fun unlockSession(): Boolean {
        val accessToken = encryptedStorage.readString(KEY_ACCESS_TOKEN)
        if (accessToken.isNullOrBlank()) {
            _authState.value = AuthState.Unauthenticated
            return false
        }

        val refreshToken = encryptedStorage.readString(KEY_REFRESH_TOKEN)
        val idToken = encryptedStorage.readString(KEY_ID_TOKEN)
        val claims = JwtUtils.parseClaims(accessToken) ?: UserClaims(
            userId = "",
            email = "",
            name = "",
            tenantId = "",
            roles = emptyList()
        )

        val session = UserSession(
            userId = claims.userId,
            email = claims.email,
            name = claims.name,
            tenantId = claims.tenantId,
            roles = claims.roles,
            accessToken = accessToken,
            refreshToken = refreshToken,
            idToken = idToken
        )

        _authState.value = AuthState.Authenticated(session)

        try {
            pushRegistrationManager?.registerCurrentTokenIfAuthenticated()
        } catch (e: Exception) {
            Log.w(TAG, "Unlock session FCM registration failed: ${e.message}")
        }

        return true
    }

    suspend fun saveTokens(
        accessToken: String,
        refreshToken: String? = null,
        idToken: String? = null
    ) {
        encryptedStorage.writeString(KEY_ACCESS_TOKEN, accessToken)
        if (refreshToken != null) {
            encryptedStorage.writeString(KEY_REFRESH_TOKEN, refreshToken)
        } else {
            encryptedStorage.remove(KEY_REFRESH_TOKEN)
        }
        if (idToken != null) {
            encryptedStorage.writeString(KEY_ID_TOKEN, idToken)
        } else {
            encryptedStorage.remove(KEY_ID_TOKEN)
        }

        val claims = JwtUtils.parseClaims(accessToken) ?: UserClaims(
            userId = "",
            email = "",
            name = "",
            tenantId = "",
            roles = emptyList()
        )

        if (claims.name.isNotBlank()) {
            encryptedStorage.writeString(KEY_USER_NAME, claims.name)
        }
        if (claims.email.isNotBlank()) {
            encryptedStorage.writeString(KEY_USER_EMAIL, claims.email)
        }
        if (claims.roles.isNotEmpty()) {
            val userRole = claims.roles.firstOrNull { role ->
                !role.startsWith("default-roles") &&
                    role != "offline_access" &&
                    role != "uma_authorization"
            } ?: claims.roles.first()
            encryptedStorage.writeString(KEY_PRIMARY_ROLE, userRole)
        }

        val session = UserSession(
            userId = claims.userId,
            email = claims.email,
            name = claims.name,
            tenantId = claims.tenantId,
            roles = claims.roles,
            accessToken = accessToken,
            refreshToken = refreshToken,
            idToken = idToken
        )

        _authState.value = AuthState.Authenticated(session)

        try {
            pushRegistrationManager?.registerCurrentTokenIfAuthenticated()
        } catch (e: Exception) {
            Log.w(TAG, "Post-signin FCM registration failed: ${e.message}")
        }
    }

    suspend fun purgeLocalSession(reason: String? = null) {
        encryptedStorage.remove(KEY_ACCESS_TOKEN)
        encryptedStorage.remove(KEY_REFRESH_TOKEN)
        encryptedStorage.remove(KEY_ID_TOKEN)
        encryptedStorage.remove(KEY_USER_NAME)
        encryptedStorage.remove(KEY_USER_EMAIL)
        encryptedStorage.remove(KEY_PRIMARY_ROLE)
        _authState.value = if (reason != null) {
            AuthState.ReauthRequired(reason)
        } else {
            AuthState.Unauthenticated
        }
    }

    suspend fun signOut(fcmToken: String? = null): Result<Unit> = withContext(Dispatchers.IO) {
        if (!fcmToken.isNullOrBlank()) {
            try {
                val response = activeDeviceApiService.unregisterDevice(fcmToken)
                if (!response.isSuccessful) {
                    return@withContext Result.failure(
                        Exception(
                            "Sign-out failed: Unable to unregister device (${response.code()})."
                        )
                    )
                }
            } catch (_: Exception) {
                return@withContext Result.failure(
                    Exception(
                        "Sign-out failed: Unable to unregister device. Please check your network connection."
                    )
                )
            }
        }

        val refreshToken = getRefreshToken()
        val idToken = getIdToken()
        if (!refreshToken.isNullOrBlank()) {
            try {
                val logoutUrl =
                    "${BuildConfig.KEYCLOAK_URL}/realms/manta/protocol/openid-connect/logout"
                val formBuilder = FormBody.Builder()
                    .add("client_id", "manta-mobile")
                    .add("refresh_token", refreshToken)
                if (!idToken.isNullOrBlank()) {
                    formBuilder.add("id_token_hint", idToken)
                }

                val request = Request.Builder()
                    .url(logoutUrl)
                    .post(formBuilder.build())
                    .build()

                httpClient.newCall(request).execute().use { response ->
                    if (!response.isSuccessful && response.code != 400 && response.code != 401) {
                        return@withContext Result.failure(
                            Exception(
                                "Sign-out failed: Unable to revoke Keycloak session (${response.code})."
                            )
                        )
                    }
                }
            } catch (_: Exception) {
                return@withContext Result.failure(
                    Exception(
                        "Sign-out failed: Unable to revoke session. Please check your network connection."
                    )
                )
            }
        }

        purgeLocalSession()
        return@withContext Result.success(Unit)
    }

    companion object {
        private const val TAG = "AuthRepository"
        private const val KEY_ACCESS_TOKEN = "auth_access_token"
        private const val KEY_REFRESH_TOKEN = "auth_refresh_token"
        private const val KEY_ID_TOKEN = "auth_id_token"
        private const val KEY_USER_NAME = "auth_user_name"
        private const val KEY_USER_EMAIL = "auth_user_email"
        private const val KEY_PRIMARY_ROLE = "auth_primary_role"
    }
}
