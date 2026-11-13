package com.manta.mobile.data.auth

data class UserSession(
    val userId: String,
    val email: String,
    val name: String,
    val tenantId: String,
    val roles: List<String>,
    val accessToken: String,
    val refreshToken: String?,
    val idToken: String?
)

sealed interface BiometricUnlockSubState {
    data object Ready : BiometricUnlockSubState
    data object Verifying : BiometricUnlockSubState
    data object Success : BiometricUnlockSubState
    data class Failed(val attemptsLeft: Int) : BiometricUnlockSubState
    data object Unavailable : BiometricUnlockSubState
}

sealed interface AuthState {
    data object Unauthenticated : AuthState
    data class LockedRequiresBiometric(
        val userName: String? = null,
        val userEmail: String? = null,
        val primaryRole: String? = null,
        val subState: BiometricUnlockSubState = BiometricUnlockSubState.Ready
    ) : AuthState
    data class Authenticated(val session: UserSession) : AuthState
    data class ReauthRequired(val reason: String) : AuthState
}
