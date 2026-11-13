package com.manta.mobile.data.auth

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.net.toUri
import com.manta.mobile.BuildConfig
import com.manta.mobile.core.auth.PkceUtils
import com.manta.mobile.features.auth.AuthRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.FormBody
import okhttp3.OkHttpClient
import okhttp3.Request

sealed interface PkceFlowState {
    data object Idle : PkceFlowState
    data object Authorizing : PkceFlowState
    data object ExchangingCode : PkceFlowState
    data object Success : PkceFlowState
    data class Error(val message: String) : PkceFlowState
}

object PkceAuthManager {

    private const val CLIENT_ID = "manta-mobile"
    private const val REDIRECT_URI = "manta://auth/callback"
    private const val SCOPE = "openid profile email offline_access"

    private val json = Json { ignoreUnknownKeys = true }
    private val httpClient by lazy { OkHttpClient() }

    private var currentCodeVerifier: String? = null

    private val _flowState = MutableStateFlow<PkceFlowState>(PkceFlowState.Idle)
    val flowState: StateFlow<PkceFlowState> = _flowState.asStateFlow()

    fun launchOidcFlow(context: Context) {
        val verifier = PkceUtils.generateCodeVerifier()
        currentCodeVerifier = verifier

        val challenge = PkceUtils.generateCodeChallenge(verifier)
        val authUrl = "${BuildConfig.KEYCLOAK_URL}/realms/manta/protocol/openid-connect/auth"
            .toUri()
            .buildUpon()
            .appendQueryParameter("response_type", "code")
            .appendQueryParameter("client_id", CLIENT_ID)
            .appendQueryParameter("redirect_uri", REDIRECT_URI)
            .appendQueryParameter("code_challenge", challenge)
            .appendQueryParameter("code_challenge_method", "S256")
            .appendQueryParameter("scope", SCOPE)
            .build()

        _flowState.value = PkceFlowState.Authorizing

        val intent = Intent(Intent.ACTION_VIEW, authUrl).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
    }

    suspend fun handleAuthorizationResponse(uri: Uri, authRepository: AuthRepository): Boolean =
        withContext(Dispatchers.IO) {
            val error = uri.getQueryParameter("error")
            if (error != null) {
                val description = uri.getQueryParameter("error_description") ?: error
                _flowState.value = PkceFlowState.Error("Authorization failed: $description")
                return@withContext false
            }

            val code = uri.getQueryParameter("code")
            if (code == null) {
                _flowState.value = PkceFlowState.Error("No authorization code received in callback")
                return@withContext false
            }

            val verifier = currentCodeVerifier
            if (verifier == null) {
                _flowState.value = PkceFlowState.Error("Session error: Code verifier missing")
                return@withContext false
            }

            _flowState.value = PkceFlowState.ExchangingCode

            try {
                val baseUrl = BuildConfig.KEYCLOAK_URL
                val tokenUrl = "$baseUrl/realms/manta/protocol/openid-connect/token"
                val formBody = FormBody.Builder()
                    .add("grant_type", "authorization_code")
                    .add("client_id", CLIENT_ID)
                    .add("code", code)
                    .add("redirect_uri", REDIRECT_URI)
                    .add("code_verifier", verifier)
                    .build()

                val request = Request.Builder()
                    .url(tokenUrl)
                    .post(formBody)
                    .build()

                httpClient.newCall(request).execute().use { response ->
                    val responseBody = response.body?.string()
                    if (!response.isSuccessful || responseBody == null) {
                        _flowState.value =
                            PkceFlowState.Error("Token exchange failed (${response.code})")
                        return@withContext false
                    }

                    val jsonObject = json.parseToJsonElement(responseBody).jsonObject
                    val accessToken = jsonObject["access_token"]?.jsonPrimitive?.content
                    val refreshToken = jsonObject["refresh_token"]?.jsonPrimitive?.content
                    val idToken = jsonObject["id_token"]?.jsonPrimitive?.content

                    if (accessToken.isNullOrBlank()) {
                        _flowState.value =
                            PkceFlowState.Error("Invalid token response: Missing access token")
                        return@withContext false
                    }

                    authRepository.saveTokens(
                        accessToken = accessToken,
                        refreshToken = refreshToken,
                        idToken = idToken
                    )

                    currentCodeVerifier = null
                    _flowState.value = PkceFlowState.Success
                    return@withContext true
                }
            } catch (e: Exception) {
                _flowState.value =
                    PkceFlowState.Error("Network error: ${e.localizedMessage ?: "Unknown error"}")
                return@withContext false
            }
        }

    fun resetState() {
        _flowState.value = PkceFlowState.Idle
    }
}
