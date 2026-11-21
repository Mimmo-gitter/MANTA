package com.manta.mobile.core.network

import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class TokenAuthenticatorTest {
    @Test
    fun authenticate_whenRequestIsTokenEndpoint_returnsNullToPreventLoop() {
        val request = Request.Builder()
            .url("http://10.0.2.2:8080/realms/manta/protocol/openid-connect/token")
            .build()
        val response = Response.Builder()
            .request(request)
            .protocol(Protocol.HTTP_1_1)
            .code(401)
            .message("Unauthorized")
            .build()

        val authenticator = TokenAuthenticator(
            tokenProvider = { "access_token" },
            refreshTokenProvider = { "refresh_token" },
            onTokenRefreshed = { _, _, _ -> },
            onRefreshFailed = { }
        )

        val resultRequest = authenticator.authenticate(null, response)
        assertNull(resultRequest)
    }

    @Test
    fun authenticate_whenAnotherThreadAlreadyRefreshedToken_replaysWithNewToken() {
        val request = Request.Builder()
            .url("http://10.0.2.2:8080/api/v1/projects")
            .header("Authorization", "Bearer stale_token_123")
            .build()
        val response = Response.Builder()
            .request(request)
            .protocol(Protocol.HTTP_1_1)
            .code(401)
            .message("Unauthorized")
            .build()

        var tokenRefreshCalled = false
        val newAccessToken = "new_refreshed_token_456"

        val authenticator = TokenAuthenticator(
            tokenProvider = { newAccessToken },
            refreshTokenProvider = { "refresh_token_789" },
            onTokenRefreshed = { _, _, _ -> tokenRefreshCalled = true },
            onRefreshFailed = { }
        )

        val resultRequest = authenticator.authenticate(null, response)
        assertTrue(resultRequest != null)
        assertEquals("Bearer $newAccessToken", resultRequest?.header("Authorization"))
        assertTrue(!tokenRefreshCalled)
    }

    @Test
    fun authenticate_whenNoRefreshTokenAvailable_callsOnRefreshFailed() {
        val request = Request.Builder()
            .url("http://10.0.2.2:8080/api/v1/projects")
            .header("Authorization", "Bearer stale_token_123")
            .build()
        val response = Response.Builder()
            .request(request)
            .protocol(Protocol.HTTP_1_1)
            .code(401)
            .message("Unauthorized")
            .build()

        var refreshFailedReason: String? = null

        val authenticator = TokenAuthenticator(
            tokenProvider = { "stale_token_123" },
            refreshTokenProvider = { null },
            onTokenRefreshed = { _, _, _ -> },
            onRefreshFailed = { reason -> refreshFailedReason = reason }
        )

        val resultRequest = authenticator.authenticate(null, response)

        assertNull(resultRequest)
        assertEquals("No refresh token available", refreshFailedReason)
    }
}
