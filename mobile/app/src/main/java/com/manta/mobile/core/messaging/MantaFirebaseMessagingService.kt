package com.manta.mobile.core.messaging

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.manta.mobile.features.alerts.MessageRepository

class MantaFirebaseMessagingService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        super.onNewToken(token)
        Log.d(TAG, "New FCM Token: $token")
        // TODO: Send token to backend via API when user is authenticated
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

    companion object {
        private const val TAG = "MantaFCMService"
    }
}
