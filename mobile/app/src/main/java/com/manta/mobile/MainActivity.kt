package com.manta.mobile

import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.google.firebase.messaging.FirebaseMessaging
import com.manta.mobile.core.theme.ui.MobileTheme
import com.manta.mobile.ui.navigation.NavShell

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
            if (task.isSuccessful) {
                val token = task.result
                Log.d("MainActivity", "FCM Registration Token: $token")
            } else {
                Log.w("MainActivity", "Fetching FCM registration token failed", task.exception)
            }
        }

        setContent {
            MobileTheme {
                NavShell()
            }
        }
    }
}
