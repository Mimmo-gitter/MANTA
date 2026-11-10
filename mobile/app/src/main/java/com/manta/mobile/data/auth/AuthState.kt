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

sealed interface AuthState {
    data object Unauthenticated : AuthState
    data object LockedRequiresBiometric : AuthState
    data class Authenticated(val session: UserSession) : AuthState
    data class ReauthRequired(val reason: String) : AuthState
}
