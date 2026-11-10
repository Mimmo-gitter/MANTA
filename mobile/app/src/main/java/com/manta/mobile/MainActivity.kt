package com.manta.mobile

import android.os.Bundle
import android.util.Log
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.fragment.app.FragmentActivity
import com.google.firebase.messaging.FirebaseMessaging
import com.manta.mobile.core.security.BiometricPromptManager
import com.manta.mobile.core.security.EncryptedStorage
import com.manta.mobile.core.theme.ui.MobileTheme
import com.manta.mobile.data.auth.AuthState
import com.manta.mobile.features.auth.AuthRepository
import com.manta.mobile.ui.navigation.NavShell

class MainActivity : FragmentActivity() {
    private lateinit var encryptedStorage: EncryptedStorage
    private lateinit var authRepository: AuthRepository
    private lateinit var biometricPromptManager: BiometricPromptManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        encryptedStorage = EncryptedStorage(applicationContext)
        authRepository = AuthRepository(encryptedStorage)
        biometricPromptManager = BiometricPromptManager(this)

        FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
            if (task.isSuccessful) {
                val token = task.result
                Log.d("MainActivity", "FCM Registration Token: $token")
            } else {
                Log.w("MainActivity", "Fetching FCM registration token failed", task.exception)
            }
        }

        setContent {
            val authState by authRepository.authState.collectAsState()

            LaunchedEffect(Unit) {
                authRepository.checkInitialAuthState()
            }
            MobileTheme {
                when (authState) {
                    is AuthState.Unauthenticated -> {
                        // Render Login Screen / Trigger Keycloak PKCE Flow
                    }
                    is AuthState.LockedRequiresBiometric -> {
                        // Trigger BiometricPrompt and call authRepository.unlockSession() on success
                    }
                    is AuthState.Authenticated -> {
                        NavShell()
                    }
                    is AuthState.ReauthRequired -> {
                        // Show session expired message + Login Button
                    }
                }
            }
        }
    }
}
