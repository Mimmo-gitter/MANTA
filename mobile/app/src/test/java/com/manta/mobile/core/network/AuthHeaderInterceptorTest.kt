package com.manta.mobile.core.network

import okhttp3.Interceptor
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AuthHeaderInterceptorTest {

    @Test
    fun intercept_whenTokenPresent_appendsAuthorizationHeader() {
        val originalRequest = Request.Builder()
            .url("https://api.manta.com/v1/projects")
            .build()

        var capturedRequest: Request? = null

        val chain = object : Interceptor.Chain {
            override fun request(): Request = originalRequest
            override fun proceed(request: Request): Response {
                capturedRequest = request
                return Response.Builder()
                    .request(request)
                    .protocol(Protocol.HTTP_1_1)
                    .code(200)
                    .message("OK")
                    .build()
            }
            override fun connection() = null
            override fun call() = throw UnsupportedOperationException()
            override fun connectTimeoutMillis() = 0
            override fun withConnectTimeout(timeout: Int, unit: java.util.concurrent.TimeUnit) =
                this
            override fun readTimeoutMillis() = 0
            override fun withReadTimeout(timeout: Int, unit: java.util.concurrent.TimeUnit) = this
            override fun writeTimeoutMillis() = 0
            override fun withWriteTimeout(timeout: Int, unit: java.util.concurrent.TimeUnit) = this
        }

        val interceptor = AuthHeaderInterceptor { "token_abc_123" }
        interceptor.intercept(chain)

        assertEquals("Bearer token_abc_123", capturedRequest?.header("Authorization"))
    }

    @Test
    fun intercept_whenTokenNull_doesNotAppendAuthorizationHeader() {
        val originalRequest = Request.Builder()
            .url("https://api.manta.com/v1/projects")
            .build()

        var capturedRequest: Request? = null

        val chain = object : Interceptor.Chain {
            override fun request(): Request = originalRequest
            override fun proceed(request: Request): Response {
                capturedRequest = request
                return Response.Builder()
                    .request(request)
                    .protocol(Protocol.HTTP_1_1)
                    .code(200)
                    .message("OK")
                    .build()
            }
            override fun connection() = null
            override fun call() = throw UnsupportedOperationException()
            override fun connectTimeoutMillis() = 0
            override fun withConnectTimeout(timeout: Int, unit: java.util.concurrent.TimeUnit) =
                this
            override fun readTimeoutMillis() = 0
            override fun withReadTimeout(timeout: Int, unit: java.util.concurrent.TimeUnit) = this
            override fun writeTimeoutMillis() = 0
            override fun withWriteTimeout(timeout: Int, unit: java.util.concurrent.TimeUnit) = this
        }

        val interceptor = AuthHeaderInterceptor { null }
        interceptor.intercept(chain)

        assertNull(capturedRequest?.header("Authorization"))
    }
}
