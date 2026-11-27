package com.manta.mobile.features.projects

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performScrollTo
import com.manta.mobile.core.theme.ui.MobileTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class ProjectScreenTest {

    @get:Rule
    val composeTestRule = createComposeRule()

    @Test
    fun projectScreen_displaysHeaderDoraMetricCardsAndLiveTimeLog() {
        composeTestRule.setContent {
            MobileTheme {
                ProjectScreen()
            }
        }

        composeTestRule.onNodeWithText("Deployment Status").assertIsDisplayed()
        composeTestRule.onNodeWithText(
            "Continuous Delivery & DORA Velocity metrics"
        ).assertIsDisplayed()
        composeTestRule.onNodeWithText("Deploy Frequency").assertIsDisplayed()
        composeTestRule.onNodeWithText("Lead Time").assertIsDisplayed()
        composeTestRule.onNodeWithText("Live Time Log").performScrollTo().assertIsDisplayed()
        composeTestRule.onNodeWithText("auth-service v2.4.2").performScrollTo().assertIsDisplayed()
    }
}
