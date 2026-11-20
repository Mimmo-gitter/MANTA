package com.manta.mobile.core.messaging

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.manta.mobile.core.network.DeviceApiService
import com.manta.mobile.core.network.RetrofitClientFactory
import com.manta.mobile.core.security.EncryptedStorage
import com.manta.mobile.data.push.PushRegistrationManager
import com.manta.mobile.features.alerts.MessageRepository
import com.manta.mobile.features.auth.AuthRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class MantaFirebaseMessagingService : FirebaseMessagingService() {
    private val serviceJob = SupervisorJob()
    private val serviceScope = CoroutineScope(Dispatchers.IO + serviceJob)

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        Log.d(TAG, "New FCM Token received: $token")

        serviceScope.launch {
            try {
                val encryptedStorage = EncryptedStorage(applicationContext)
                val authRepository = AuthRepository(encryptedStorage)
                val deviceApiService: DeviceApiService =
                    RetrofitClientFactory.createAuthenticatedService(authRepository)
                val pushRegistrationManager =
                    PushRegistrationManager(deviceApiService, authRepository)

                pushRegistrationManager.registerDeviceToken(token)
            } catch (e: Exception) {
                Log.e(TAG, "Failed to register new FCM token: ${e.message}", e)
            }
        }
    }

    override fun onMessageReceived(remoteMessage: RemoteMessage) {
        super.onMessageReceived(remoteMessage)
        Log.d(TAG, "From: ${remoteMessage.from}")
        remoteMessage.notification?.let { notification ->
            if (notification.title.isNullOrEmpty() || notification.body.isNullOrEmpty()) {
                return
            }
            MessageRepository.updateMessage(notification.title!!, notification.body!!)
            Log.d(TAG, "Notification Title: ${notification.title}, Body: ${notification.body}")
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        serviceJob.cancel()
    }

    companion object {
        private const val TAG = "MantaFCMService"
    }
}
