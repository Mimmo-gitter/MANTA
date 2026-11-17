package com.manta.mobile.data.auth

data class UserSession(
    val userId: String,
    val email: String,
    val name: String,
    val tenantId: String,
    val roles: List<String>,
    val accessToken: String,
    val refreshToken: String? = null,
    val idToken: String? = null,
    val squadName: String = "Core Infrastructure Squad"
) {
    val primaryRole: String
        get() = roles.firstOrNull { role ->
            !role.startsWith("default-roles") &&
                role != "offline_access" &&
                role != "uma_authorization"
        } ?: roles.firstOrNull() ?: DEFAULT.roles.first()

    val displayName: String
        get() = name.ifBlank { DEFAULT.name }

    val displayEmail: String
        get() = email.ifBlank { DEFAULT.email }

    companion object {
        val DEFAULT = UserSession(
            userId = "preview-user-id",
            email = "alex.vance@manta-labs.io",
            name = "Alex Mercer",
            tenantId = "preview-tenant-id",
            roles = listOf("Lead"),
            accessToken = "",
            refreshToken = null,
            idToken = null,
            squadName = "Core Infrastructure Squad"
        )
    }
}

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
