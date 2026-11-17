package com.manta.mobile.core.network

import com.manta.mobile.BuildConfig
import com.manta.mobile.features.auth.AuthRepository
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import okhttp3.Authenticator
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

object RetrofitClientFactory {
    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        encodeDefaults = true
    }

    fun createOkHttpClient(
        authInterceptor: Interceptor? = null,
        authenticator: Authenticator? = null
    ): OkHttpClient {
        val loggingInterceptor = HttpLoggingInterceptor().apply {
            level = if (BuildConfig.DEBUG) {
                HttpLoggingInterceptor.Level.BODY
            } else {
                HttpLoggingInterceptor.Level.NONE
            }
        }

        val builder = OkHttpClient.Builder()
            .addInterceptor(loggingInterceptor)
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)

        if (authInterceptor != null) {
            builder.addInterceptor(authInterceptor)
        }
        if (authenticator != null) {
            builder.authenticator(authenticator)
        }

        return builder.build()
    }

    fun createAuthenticatedOkHttpClient(authRepository: AuthRepository): OkHttpClient {
        val authInterceptor = AuthHeaderInterceptor {
            runBlocking { authRepository.getAccessToken() }
        }
        val authenticator = TokenAuthenticator(
            tokenProvider = { runBlocking { authRepository.getAccessToken() } },
            refreshTokenProvider = { runBlocking { authRepository.getRefreshToken() } },
            onTokenRefreshed = { accessToken, refreshToken, idToken ->
                runBlocking { authRepository.saveTokens(accessToken, refreshToken, idToken) }
            },
            onRefreshFailed = { reason ->
                runBlocking { authRepository.purgeLocalSession(reason) }
            }
        )
        return createOkHttpClient(
            authInterceptor = authInterceptor,
            authenticator = authenticator
        )
    }

    fun createRetrofit(
        baseUrl: String = BuildConfig.API_URL,
        okHttpClient: OkHttpClient = createOkHttpClient()
    ): Retrofit {
        val normalizedBaseUrl = if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/"
        val contentType = "application/json".toMediaType()
        return Retrofit.Builder()
            .baseUrl(normalizedBaseUrl)
            .client(okHttpClient)
            .addConverterFactory(json.asConverterFactory(contentType))
            .build()
    }

    inline fun <reified T> createService(
        baseUrl: String = BuildConfig.API_URL,
        okHttpClient: OkHttpClient = createOkHttpClient()
    ): T = createRetrofit(baseUrl, okHttpClient).create(T::class.java)

    inline fun <reified T> createAuthenticatedService(
        authRepository: AuthRepository,
        baseUrl: String = BuildConfig.API_URL
    ): T {
        val okHttpClient = createAuthenticatedOkHttpClient(authRepository)
        return createRetrofit(baseUrl, okHttpClient).create(T::class.java)
    }
}
