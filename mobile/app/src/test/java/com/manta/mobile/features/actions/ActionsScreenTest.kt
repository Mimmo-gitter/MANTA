package com.manta.mobile.features.actions

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
class ActionsScreenTest {

    @get:Rule
    val composeTestRule = createComposeRule()

    @Test
    fun actionsScreen_displaysHeaderAndStalePrCards() {
        composeTestRule.setContent {
            MobileTheme {
                ActionsScreen()
            }
        }

        composeTestRule.onNodeWithText("Stale PRs").assertIsDisplayed()
        composeTestRule.onNodeWithText("● 5 Actionable • 2 Blocked").assertIsDisplayed()
        composeTestRule.onNodeWithText("All stale PRs 5").assertIsDisplayed()
        composeTestRule.onNodeWithText("#1402").performScrollTo().assertIsDisplayed()
    }

    @Test
    fun actionsScreen_clickingNudgeButton_doesNotCrash() {
        composeTestRule.setContent {
            MobileTheme {
                ActionsScreen()
            }
        }

        composeTestRule.onNodeWithText("Nudge @thijn_lead").performScrollTo().performClick()
    }
}
