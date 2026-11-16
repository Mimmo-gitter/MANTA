package com.manta.mobile.core.network

import com.manta.mobile.data.auth.PkceAuthManager
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.Authenticator
import okhttp3.Request
import okhttp3.Response
import okhttp3.Route

class TokenAuthenticator(
    private val tokenProvider: () -> String?,
    private val refreshTokenProvider: () -> String?,
    private val onTokenRefreshed: (
        accessToken: String,
        refreshToken: String?,
        idToken: String?
    ) -> Unit,
    private val onRefreshFailed: (reason: String) -> Unit
) : Authenticator {
    private val refreshMutex = Mutex()

    override fun authenticate(route: Route?, response: Response): Request? {
        val requestUrl = response.request.url.toString()
        if (requestUrl.contains("/protocol/openid-connect/token") ||
            requestUrl.contains("/protocol/openid-connect/logout")
        ) {
            return null
        }

        val failedToken = response.request.header("Authorization")
            ?.removePrefix("Bearer ")
            ?.trim()
        return runBlocking {
            refreshMutex.withLock {
                val latestToken = tokenProvider()
                if (!latestToken.isNullOrBlank() && latestToken != failedToken) {
                    return@runBlocking response.request.newBuilder()
                        .header("Authorization", "Bearer $latestToken")
                        .build()
                }

                val currentRefreshToken = refreshTokenProvider()
                if (currentRefreshToken.isNullOrBlank()) {
                    onRefreshFailed("No refresh token available")
                    return@runBlocking null
                }

                val refreshed = PkceAuthManager.refreshTokens(currentRefreshToken)
                if (refreshed != null) {
                    onTokenRefreshed(
                        refreshed.accessToken,
                        refreshed.refreshToken,
                        refreshed.idToken
                    )
                    return@runBlocking response.request.newBuilder()
                        .header("Authorization", "Bearer ${refreshed.accessToken}")
                        .build()
                } else {
                    onRefreshFailed("Session expired. Please sign in again.")
                    return@runBlocking null
                }
            }
        }
    }
}
