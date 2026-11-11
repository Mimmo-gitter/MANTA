package com.manta.mobile

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.widget.Toast
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.lifecycleScope
import com.google.firebase.messaging.FirebaseMessaging
import com.manta.mobile.core.security.BiometricPromptManager
import com.manta.mobile.core.security.EncryptedStorage
import com.manta.mobile.core.theme.ui.MobileTheme
import com.manta.mobile.data.auth.AuthState
import com.manta.mobile.features.auth.AuthRepository
import com.manta.mobile.features.auth.PkceAuthManager
import com.manta.mobile.features.auth.SignInScreen
import com.manta.mobile.ui.navigation.NavShell
import kotlinx.coroutines.launch

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

        val isDeepLink = intent?.data?.scheme == "manta" && intent?.data?.host == "auth"
        if (!isDeepLink && savedInstanceState == null) {
            lifecycleScope.launch {
                authRepository.checkInitialAuthState()
            }
        }

        handleDeepLinkIntent(intent)

        FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
            if (task.isSuccessful) {
                Log.d("MainActivity", "FCM Registration Token: ${task.result}")
            } else {
                Log.w("MainActivity", "Fetching FCM registration token failed", task.exception)
            }
        }

        lifecycleScope.launch {
            biometricPromptManager.promptResults.collect { result ->
                when (result) {
                    is BiometricPromptManager.BiometricResult.AuthenticationSuccess -> {
                        authRepository.unlockSession()
                    }
                    is BiometricPromptManager.BiometricResult.AuthenticationError -> {
                        Toast.makeText(
                            this@MainActivity,
                            "Biometric failed: ${result.error}",
                            Toast.LENGTH_SHORT
                        ).show()
                    }
                    BiometricPromptManager.BiometricResult.AuthenticationFailed -> {
                        Toast.makeText(
                            this@MainActivity,
                            "Authentication failed",
                            Toast.LENGTH_SHORT
                        ).show()
                    }
                }
            }
        }

        setContent {
            val authState by authRepository.authState.collectAsState()

            MobileTheme {
                when (authState) {
                    is AuthState.Unauthenticated, is AuthState.ReauthRequired -> {
                        SignInScreen(
                            onBiometricUnlockClick = { triggerBiometricUnlock() }
                        )
                    }
                    is AuthState.LockedRequiresBiometric -> {
                        LaunchedEffect(Unit) {
                            triggerBiometricUnlock()
                        }
                        SignInScreen(
                            onBiometricUnlockClick = { triggerBiometricUnlock() }
                        )
                    }
                    is AuthState.Authenticated -> {
                        NavShell()
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleDeepLinkIntent(intent)
    }

    private fun handleDeepLinkIntent(intent: Intent?) {
        val uri: Uri? = intent?.data
        if (uri != null && uri.scheme == "manta" && uri.host == "auth") {
            lifecycleScope.launch {
                PkceAuthManager.handleAuthorizationResponse(uri, authRepository)
            }
        }
    }

    private fun triggerBiometricUnlock() {
        when (val status = biometricPromptManager.canAuthenticate()) {
            is BiometricPromptManager.BiometricStatus.Ready -> {
                biometricPromptManager.showBiometricPrompt(
                    title = "Unlock MANTA Workspace",
                    description = "Scan your fingerprint or face to proceed"
                )
            }
            is BiometricPromptManager.BiometricStatus.NotEnrolled -> {
                Toast.makeText(
                    this,
                    "No biometric/PIN enrolled. Please set up device lock in the settings.",
                    Toast.LENGTH_LONG
                ).show()
            }
            is BiometricPromptManager.BiometricStatus.FeatureUnavailable,
            is BiometricPromptManager.BiometricStatus.HardwareUnavailable -> {
                Toast.makeText(
                    this,
                    "Biometric hardware unavailable on this device.",
                    Toast.LENGTH_SHORT
                ).show()
            }
            else -> {
                Toast.makeText(
                    this,
                    "Biometric authentication status: $status",
                    Toast.LENGTH_SHORT
                ).show()
            }
        }
    }
}
