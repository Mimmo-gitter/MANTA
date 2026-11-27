package com.manta.mobile.features.alerts

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import com.manta.mobile.core.theme.ui.MobileTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class AlertsScreenTest {

    @get:Rule
    val composeTestRule = createComposeRule()

    @Test
    fun alertsScreen_displaysHeaderAndLiveFeedCards() {
        composeTestRule.setContent {
            MobileTheme {
                AlertsScreen()
            }
        }

        composeTestRule.onNodeWithText("Alerts").assertIsDisplayed()
        composeTestRule.onNodeWithText("● 5 Active Risks").assertIsDisplayed()
        composeTestRule.onNodeWithText("All Sources 16").assertIsDisplayed()
        composeTestRule.onNodeWithText("#1402").performScrollTo().assertIsDisplayed()
        composeTestRule.onNodeWithText(
            "PR: Auth JWT refresh token"
        ).performScrollTo().assertIsDisplayed()
    }

    @Test
    fun alertsScreen_emptyStateRedirectsToProject() {
        var redirected = false

        composeTestRule.setContent {
            MobileTheme {
                AlertsScreen(onNavigateToProject = { redirected = true })
            }
        }

        // Click status badge to cycle to Empty state
        composeTestRule.onNodeWithText("● 5 Active Risks").performClick()
        composeTestRule.onNodeWithText("All clear!").assertIsDisplayed()

        // Click Back to Project Screen
        composeTestRule.onNodeWithText("Back to Project Screen").performScrollTo().performClick()
        assertTrue(redirected)
    }
}
