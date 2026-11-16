package com.manta.mobile.core.network

import com.manta.mobile.BuildConfig
import java.util.concurrent.TimeUnit
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

    fun createRetrofit(
        baseUrl: String = BuildConfig.API_URL,
        okHttpClient: OkHttpClient = createOkHttpClient()
    ): Retrofit {
        val contentType = "application/json".toMediaType()
        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(okHttpClient)
            .addConverterFactory(json.asConverterFactory(contentType))
            .build()
    }

    inline fun <reified T> createService(
        baseUrl: String = BuildConfig.API_URL,
        okHttpClient: OkHttpClient = createOkHttpClient()
    ): T = createRetrofit(baseUrl, okHttpClient).create(T::class.java)
}
