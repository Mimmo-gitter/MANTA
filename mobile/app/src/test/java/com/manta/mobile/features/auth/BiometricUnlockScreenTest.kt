package com.manta.mobile.features.auth

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import com.manta.mobile.core.theme.ui.MobileTheme
import com.manta.mobile.data.auth.BiometricUnlockSubState
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class BiometricUnlockScreenTest {
    @get:Rule
    val composeTestRule = createComposeRule()

    private val userName: String = "Alex Mercer"

    @Test
    fun stateReady_displaysVerifyIdentityTitleAndSensorReadyChip() {
        val userEmail = "Alex Mercer"
        val primaryRole = "Lead"

        composeTestRule.setContent {
            MobileTheme {
                BiometricUnlockScreen(
                    userName = userName,
                    userEmail = userEmail,
                    primaryRole = primaryRole,
                    subState = BiometricUnlockSubState.Ready
                )
            }
        }

        composeTestRule.onNodeWithText("Verify Identity").assertIsDisplayed()
        composeTestRule.onNodeWithText(userName).assertIsDisplayed()
        composeTestRule.onNodeWithText(userEmail).assertIsDisplayed()
        composeTestRule.onNodeWithText(primaryRole).assertIsDisplayed()
        composeTestRule.onNodeWithText("● Sensor is Ready").performScrollTo().assertIsDisplayed()
        composeTestRule.onNodeWithText("Switch").assertIsDisplayed()
    }

    @Test
    fun stateSuccess_displaysIdentityVerifiedTitleAndProgress() {
        composeTestRule.setContent {
            MobileTheme {
                BiometricUnlockScreen(
                    userName = userName,
                    subState = BiometricUnlockSubState.Success
                )
            }
        }

        composeTestRule.onNodeWithText("Identity Verified").assertIsDisplayed()
        composeTestRule.onNodeWithText(
            "● Successfully Verified"
        ).performScrollTo().assertIsDisplayed()
        composeTestRule.onNodeWithText("Redirecting...").performScrollTo().assertIsDisplayed()
    }

    @Test
    fun stateFailed_displaysVerificationFailedTitleAttemptCountAndRetryButton() {
        composeTestRule.setContent {
            MobileTheme {
                BiometricUnlockScreen(
                    userName = userName,
                    subState = BiometricUnlockSubState.Failed(attemptsLeft = 2)
                )
            }
        }

        composeTestRule.onNodeWithText("Verification Failed").assertIsDisplayed()
        composeTestRule.onNodeWithText("Attempt 2 of 3").assertIsDisplayed()
        composeTestRule.onNodeWithText("LOCKOUT IN").performScrollTo().assertIsDisplayed()
        composeTestRule.onNodeWithText("2 Attempts").performScrollTo().assertIsDisplayed()
        composeTestRule.onNodeWithText("Retry Biometrics").performScrollTo().assertIsDisplayed()
    }

    @Test
    fun stateFailed_clickingRetryButton_triggersRetryCallback() {
        var retried = false

        composeTestRule.setContent {
            MobileTheme {
                BiometricUnlockScreen(
                    subState = BiometricUnlockSubState.Failed(attemptsLeft = 1),
                    onRetryClick = { retried = true }
                )
            }
        }

        composeTestRule.onNodeWithText("Retry Biometrics").performScrollTo().performClick()
        assertTrue(retried)
    }

    @Test
    fun stateUnavailable_displaysBiometricsUnavailableTitleAndSsoButton() {
        composeTestRule.setContent {
            MobileTheme {
                BiometricUnlockScreen(
                    subState = BiometricUnlockSubState.Unavailable
                )
            }
        }

        composeTestRule.onNodeWithText("Biometrics Unavailable").assertIsDisplayed()
        composeTestRule.onNodeWithText("● SENSOR UNAVAILABLE").performScrollTo().assertIsDisplayed()
        composeTestRule.onNodeWithText(
            "SIGN IN WITH ORGANIZATION SSO"
        ).performScrollTo().assertIsDisplayed()
    }

    @Test
    fun stateUnavailable_clickingSsoButton_triggersSsoCallback() {
        var ssoClicked = false

        composeTestRule.setContent {
            MobileTheme {
                BiometricUnlockScreen(
                    subState = BiometricUnlockSubState.Unavailable,
                    onSsoClick = { ssoClicked = true }
                )
            }
        }

        composeTestRule.onNodeWithText(
            "SIGN IN WITH ORGANIZATION SSO"
        ).performScrollTo().performClick()
        assertTrue(ssoClicked)
    }
}
