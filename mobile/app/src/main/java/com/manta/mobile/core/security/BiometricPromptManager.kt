package com.manta.mobile.core.security

import android.os.Build
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_WEAK
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.core.hardware.fingerprint.FingerprintManagerCompat
import androidx.fragment.app.FragmentActivity
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.receiveAsFlow

class BiometricPromptManager(private val activity: FragmentActivity) {
    private val resultChannel = Channel<BiometricResult>()
    val promptResults = resultChannel.receiveAsFlow()

    fun canAuthenticate(): BiometricStatus {
        val biometricManager = BiometricManager.from(activity)

        val result = when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.R -> {
                biometricManager.canAuthenticate(
                    BIOMETRIC_STRONG or BIOMETRIC_WEAK or DEVICE_CREDENTIAL
                )
            }
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q -> {
                biometricManager.canAuthenticate(BIOMETRIC_STRONG or BIOMETRIC_WEAK)
            }
            else -> {
                @Suppress("DEPRECATION")
                val noArgResult = biometricManager.canAuthenticate()
                if (noArgResult == BiometricManager.BIOMETRIC_SUCCESS) {
                    BiometricManager.BIOMETRIC_SUCCESS
                } else {
                    val strongResult = biometricManager.canAuthenticate(BIOMETRIC_STRONG)
                    if (strongResult == BiometricManager.BIOMETRIC_SUCCESS) {
                        BiometricManager.BIOMETRIC_SUCCESS
                    } else {
                        val weakResult = biometricManager.canAuthenticate(BIOMETRIC_WEAK)
                        if (weakResult == BiometricManager.BIOMETRIC_SUCCESS) {
                            BiometricManager.BIOMETRIC_SUCCESS
                        } else {
                            noArgResult
                        }
                    }
                }
            }
        }

        if (result == BiometricManager.BIOMETRIC_SUCCESS) {
            return BiometricStatus.Ready
        }

        // Fallback for Samsung / legacy Android 9 (API 28) devices where BiometricManager returns -1
        @Suppress("DEPRECATION")
        val fingerprintManager = FingerprintManagerCompat.from(activity)
        if (fingerprintManager.isHardwareDetected) {
            return if (fingerprintManager.hasEnrolledFingerprints()) {
                BiometricStatus.Ready
            } else {
                BiometricStatus.NotEnrolled
            }
        }
        if (result == -1) {
            return BiometricStatus.Ready
        }

        return when (result) {
            BiometricManager.BIOMETRIC_ERROR_NO_HARDWARE -> BiometricStatus.FeatureUnavailable
            BiometricManager.BIOMETRIC_ERROR_HW_UNAVAILABLE -> BiometricStatus.HardwareUnavailable
            BiometricManager.BIOMETRIC_ERROR_NONE_ENROLLED -> BiometricStatus.NotEnrolled
            else -> BiometricStatus.Unknown(result)
        }
    }

    fun showBiometricPrompt(
        title: String,
        description: String,
        negativeButtonText: String = "Cancel"
    ) {
        val executor = ContextCompat.getMainExecutor(activity)
        val promptInfoBuilder = BiometricPrompt.PromptInfo.Builder()
            .setTitle(title)
            .setDescription(description)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            promptInfoBuilder.setAllowedAuthenticators(
                BIOMETRIC_STRONG or BIOMETRIC_WEAK or DEVICE_CREDENTIAL
            )
        } else {
            promptInfoBuilder.setNegativeButtonText(negativeButtonText)
        }

        val prompt = BiometricPrompt(
            activity,
            executor,
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    super.onAuthenticationError(errorCode, errString)
                    resultChannel.trySend(
                        BiometricResult.AuthenticationError(errString.toString())
                    )
                }

                override fun onAuthenticationSucceeded(
                    result: BiometricPrompt.AuthenticationResult
                ) {
                    super.onAuthenticationSucceeded(result)
                    resultChannel.trySend(BiometricResult.AuthenticationSuccess)
                }

                override fun onAuthenticationFailed() {
                    super.onAuthenticationFailed()
                    resultChannel.trySend(BiometricResult.AuthenticationFailed)
                }
            }
        )

        prompt.authenticate(promptInfoBuilder.build())
    }

    sealed interface BiometricResult {
        data class AuthenticationError(val error: String) : BiometricResult
        data object AuthenticationSuccess : BiometricResult
        data object AuthenticationFailed : BiometricResult
    }

    sealed interface BiometricStatus {
        data object Ready : BiometricStatus
        data object FeatureUnavailable : BiometricStatus
        data object HardwareUnavailable : BiometricStatus
        data object NotEnrolled : BiometricStatus
        data class Unknown(val rawCode: Int = -1) : BiometricStatus
    }
}
