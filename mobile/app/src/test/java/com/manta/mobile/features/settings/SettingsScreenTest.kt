package com.manta.mobile.features.settings

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import com.manta.mobile.core.theme.ui.MobileTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class SettingsScreenTest {

    @get:Rule
    val composeTestRule = createComposeRule()

    @Test
    fun settingsScreen_displaysHeaderSubtitleUserProfileCardAndSignOutButton() {
        composeTestRule.setContent {
            MobileTheme {
                SettingsScreen()
            }
        }

        composeTestRule.onNodeWithText("Settings").assertIsDisplayed()
        composeTestRule.onNodeWithText(
            "Manage session access and platform preferences."
        ).assertIsDisplayed()
        composeTestRule.onNodeWithText("Alex Mercer").assertIsDisplayed()
        composeTestRule.onNodeWithText("Lead").assertIsDisplayed()
        composeTestRule.onNodeWithText("alex.vance@manta-labs.io").assertIsDisplayed()
        composeTestRule.onNodeWithText("Core Infrastructure Squad").assertIsDisplayed()
        composeTestRule.onNodeWithText("Hardware Token Authenticated").assertIsDisplayed()
        composeTestRule.onNodeWithText("Sign Out").performScrollTo().assertIsDisplayed()
    }

    @Test
    fun settingsScreen_clickingSignOutButton_doesNotCrash() {
        composeTestRule.setContent {
            MobileTheme {
                SettingsScreen()
            }
        }

        composeTestRule.onNodeWithText("Sign Out").performScrollTo().performClick()
    }
}
