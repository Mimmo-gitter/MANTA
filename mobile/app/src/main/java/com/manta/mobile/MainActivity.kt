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
import com.manta.mobile.data.auth.BiometricUnlockSubState
import com.manta.mobile.features.auth.AuthRepository
import com.manta.mobile.features.auth.BiometricUnlockScreen
import com.manta.mobile.features.auth.PkceAuthManager
import com.manta.mobile.features.auth.SignInScreen
import com.manta.mobile.ui.navigation.NavShell
import kotlin.time.Duration.Companion.milliseconds
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class MainActivity : FragmentActivity() {

    private lateinit var encryptedStorage: EncryptedStorage
    private lateinit var authRepository: AuthRepository
    private lateinit var biometricPromptManager: BiometricPromptManager

    private var failedAttemptCount = 0

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
                        failedAttemptCount = 0
                        authRepository.updateBiometricSubState(BiometricUnlockSubState.Success)
                        delay(1000.milliseconds)
                        authRepository.unlockSession()
                    }
                    is BiometricPromptManager.BiometricResult.AuthenticationError -> {
                        failedAttemptCount++
                        val attemptsLeft = (3 - failedAttemptCount).coerceAtLeast(0)
                        if (attemptsLeft == 0 ||
                            result.error.contains("lockout", ignoreCase = true)
                        ) {
                            authRepository.updateBiometricSubState(
                                BiometricUnlockSubState.Unavailable
                            )
                        } else {
                            authRepository.updateBiometricSubState(
                                BiometricUnlockSubState.Failed(attemptsLeft)
                            )
                        }
                    }
                    BiometricPromptManager.BiometricResult.AuthenticationFailed -> {
                        failedAttemptCount++
                        val attemptsLeft = (3 - failedAttemptCount).coerceAtLeast(0)
                        if (attemptsLeft == 0) {
                            authRepository.updateBiometricSubState(
                                BiometricUnlockSubState.Unavailable
                            )
                        } else {
                            authRepository.updateBiometricSubState(
                                BiometricUnlockSubState.Failed(attemptsLeft)
                            )
                        }
                    }
                }
            }
        }

        setContent {
            val authState by authRepository.authState.collectAsState()

            MobileTheme {
                when (val state = authState) {
                    is AuthState.Unauthenticated, is AuthState.ReauthRequired -> {
                        SignInScreen(
                            onBiometricUnlockClick = { triggerBiometricUnlock() }
                        )
                    }
                    is AuthState.LockedRequiresBiometric -> {
                        LaunchedEffect(Unit) {
                            if (state.subState == BiometricUnlockSubState.Ready) {
                                triggerBiometricUnlock()
                            }
                        }
                        BiometricUnlockScreen(
                            userName = state.userName,
                            userEmail = state.userEmail,
                            primaryRole = state.primaryRole,
                            subState = state.subState,
                            onRetryClick = {
                                authRepository.updateBiometricSubState(
                                    BiometricUnlockSubState.Ready
                                )
                                triggerBiometricUnlock()
                            },
                            onSwitchClick = {
                                lifecycleScope.launch {
                                    authRepository.logout()
                                }
                            },
                            onSsoClick = {
                                lifecycleScope.launch {
                                    authRepository.logout()
                                    PkceAuthManager.launchOidcFlow(this@MainActivity)
                                }
                            }
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
                authRepository.updateBiometricSubState(BiometricUnlockSubState.Verifying)
                biometricPromptManager.showBiometricPrompt(
                    title = "Unlock MANTA Workspace",
                    description = "Scan your fingerprint or face to proceed"
                )
            }
            is BiometricPromptManager.BiometricStatus.NotEnrolled -> {
                authRepository.updateBiometricSubState(BiometricUnlockSubState.Unavailable)
                Toast.makeText(
                    this,
                    "No biometric/PIN enrolled on this device. Please set up device lock in the settings.",
                    Toast.LENGTH_SHORT
                ).show()
            }
            is BiometricPromptManager.BiometricStatus.FeatureUnavailable,
            is BiometricPromptManager.BiometricStatus.HardwareUnavailable -> {
                authRepository.updateBiometricSubState(BiometricUnlockSubState.Unavailable)
                Toast.makeText(
                    this,
                    "Biometric hardware unavailable on this device.",
                    Toast.LENGTH_SHORT
                ).show()
            }
            else -> {
                authRepository.updateBiometricSubState(BiometricUnlockSubState.Unavailable)
                Toast.makeText(
                    this,
                    "Biometric status: $status",
                    Toast.LENGTH_SHORT
                ).show()
            }
        }
    }
}
