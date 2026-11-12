package com.manta.mobile.features.auth

import com.manta.mobile.core.security.EncryptedStorage
import com.manta.mobile.data.auth.AuthState
import com.manta.mobile.data.auth.BiometricUnlockSubState
import com.manta.mobile.data.auth.JwtUtils
import com.manta.mobile.data.auth.UserClaims
import com.manta.mobile.data.auth.UserSession
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

class AuthRepository(private val encryptedStorage: EncryptedStorage) {
    private val _authState = MutableStateFlow<AuthState>(AuthState.Unauthenticated)
    val authState: StateFlow<AuthState> = _authState.asStateFlow()

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
            encryptedStorage.writeString(KEY_PRIMARY_ROLE, claims.roles.first())
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
    }

    suspend fun logout() {
        encryptedStorage.remove(KEY_ACCESS_TOKEN)
        encryptedStorage.remove(KEY_REFRESH_TOKEN)
        encryptedStorage.remove(KEY_ID_TOKEN)
        encryptedStorage.remove(KEY_USER_NAME)
        encryptedStorage.remove(KEY_USER_EMAIL)
        encryptedStorage.remove(KEY_PRIMARY_ROLE)
        _authState.value = AuthState.Unauthenticated
    }

    companion object {
        private const val KEY_ACCESS_TOKEN = "auth_access_token"
        private const val KEY_REFRESH_TOKEN = "auth_refresh_token"
        private const val KEY_ID_TOKEN = "auth_id_token"
        private const val KEY_USER_NAME = "auth_user_name"
        private const val KEY_USER_EMAIL = "auth_user_email"
        private const val KEY_PRIMARY_ROLE = "auth_primary_role"
    }
}
