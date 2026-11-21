package com.manta.mobile.data.push

import android.util.Log
import com.google.firebase.messaging.FirebaseMessaging
import com.manta.mobile.core.network.DeviceApiService
import com.manta.mobile.core.network.RegisterMobileDeviceRequest
import com.manta.mobile.data.auth.AuthState
import com.manta.mobile.features.auth.AuthRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withContext

class PushRegistrationManager(
    private val deviceApiService: DeviceApiService,
    private val authRepository: AuthRepository
) {

    suspend fun registerDeviceToken(token: String): Result<Unit> = withContext(Dispatchers.IO) {
        val trimmedToken = token.trim()
        if (trimmedToken.isBlank()) {
            return@withContext Result.failure(Exception("FCM token must not be blank"))
        }

        if (authRepository.authState.value !is AuthState.Authenticated) {
            Log.d(TAG, "User is not authenticated. Postponing FCM registration until sign-in.")
            return@withContext Result.success(Unit)
        }

        try {
            val request = RegisterMobileDeviceRequest(fcmToken = trimmedToken)
            val response = deviceApiService.registerDevice(request)
            if (response.isSuccessful) {
                Log.d(TAG, "Successfully registered FCM token with backend: $trimmedToken")
                Result.success(Unit)
            } else {
                val errorBody = response.errorBody()?.string()
                Log.e(
                    TAG,
                    "Failed to register FCM token with backend (${response.code()}): $errorBody"
                )
                Result.failure(
                    Exception("FCM token registration failed (${response.code()}): $errorBody")
                )
            }
        } catch (e: Exception) {
            Log.e(TAG, "Network error registering FCM token: ${e.message}", e)
            Result.failure(e)
        }
    }

    suspend fun registerCurrentTokenIfAuthenticated(): Result<Unit> = withContext(Dispatchers.IO) {
        if (authRepository.authState.value !is AuthState.Authenticated) {
            return@withContext Result.success(Unit)
        }

        return@withContext try {
            val token = FirebaseMessaging.getInstance().token.await()
            if (!token.isNullOrBlank()) {
                registerDeviceToken(token)
            } else {
                Result.success(Unit)
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to retrieve current FCM token: ${e.message}")
            Result.failure(e)
        }
    }

    companion object {
        private const val TAG = "PushRegistrationManager"
    }
}
