package com.manta.mobile.features.auth

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import com.manta.mobile.core.theme.ui.MobileTheme
import com.manta.mobile.data.auth.PkceAuthManager
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class SignInScreenTest {
    @get:Rule
    val composeTestRule = createComposeRule()

    @Before
    fun setUp() {
        PkceAuthManager.resetState()
    }

    @Test
    fun signInScreen_displaysHeaderAndAllElements() {
        composeTestRule.setContent {
            MobileTheme {
                SignInScreen()
            }
        }

        composeTestRule.onNodeWithText("M.A.N.T.A.").assertIsDisplayed()
        composeTestRule.onNodeWithText("Organization Email").assertIsDisplayed()
        composeTestRule.onNodeWithText("Password").assertIsDisplayed()
        composeTestRule.onNodeWithText("Remember this device").performScrollTo().assertIsDisplayed()
        composeTestRule.onNodeWithText(
            "Sign In with Organization SSO"
        ).performScrollTo().assertIsDisplayed()
        composeTestRule.onNodeWithText(
            "Quick Biometric Sign-In"
        ).performScrollTo().assertIsDisplayed()
        composeTestRule.onNodeWithText(
            "Protected by M.A.N.T.A. team"
        ).performScrollTo().assertIsDisplayed()
    }

    @Test
    fun signInScreen_clickingBiometricCard_triggersCallback() {
        var clicked = false

        composeTestRule.setContent {
            MobileTheme {
                SignInScreen(
                    onBiometricUnlockClick = { clicked = true }
                )
            }
        }

        composeTestRule.onNodeWithText("Quick Biometric Sign-In").performScrollTo().performClick()
        assertTrue(clicked)
    }
}
