package com.manta.mobile.ui.navigation

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotSelected
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.isSelectable
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.manta.mobile.core.theme.ui.MobileTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class NavigationWidgetTest {

    @get:Rule
    val composeTestRule = createComposeRule()

    private fun setNavShellContent() {
        composeTestRule.setContent {
            MobileTheme {
                NavShell()
            }
        }
    }

    @Test
    fun initialScreen_displaysAlertsScreenAndSelectsAlertsTab() {
        setNavShellContent()

        // Verify initial route is Alerts
        composeTestRule.onNodeWithTag("screen_alerts").assertIsDisplayed()
        composeTestRule.onNode(hasText("Alerts") and isSelectable()).assertIsSelected()
    }

    @Test
    fun navigateToProjectTab_displaysProjectScreen() {
        setNavShellContent()

        // Click Project tab
        composeTestRule.onNodeWithText("Project").performClick()

        // Verify destination screen and selection
        composeTestRule.onNodeWithTag("screen_project").assertIsDisplayed()
        composeTestRule.onNodeWithText("Project").assertIsSelected()
        composeTestRule.onNode(hasText("Alerts") and isSelectable()).assertIsNotSelected()
    }

    @Test
    fun navigateToActionsTab_displaysActionsScreen() {
        setNavShellContent()

        // Click Actions tab
        composeTestRule.onNodeWithText("Actions").performClick()

        // Verify destination screen and selection
        composeTestRule.onNodeWithTag("screen_actions").assertIsDisplayed()
        composeTestRule.onNodeWithText("Actions").assertIsSelected()
        composeTestRule.onNode(hasText("Alerts") and isSelectable()).assertIsNotSelected()
    }

    @Test
    fun navigateToSettingsTab_displaysSettingsScreen() {
        setNavShellContent()

        // Click Settings tab
        composeTestRule.onNode(hasText("Settings") and isSelectable()).performClick()

        // Verify destination screen and selection
        composeTestRule.onNodeWithText(
            "Manage session access and platform preferences."
        ).assertIsDisplayed()
        composeTestRule.onNode(hasText("Settings") and isSelectable()).assertIsSelected()
        composeTestRule.onNode(hasText("Alerts") and isSelectable()).assertIsNotSelected()
    }

    @Test
    fun navigateAcrossAllTabs_updatesDestinationAndSelectionCorrectly() {
        setNavShellContent()

        // Initial state: Alerts
        composeTestRule.onNodeWithTag("screen_alerts").assertIsDisplayed()
        composeTestRule.onNode(hasText("Alerts") and isSelectable()).assertIsSelected()

        // Navigate to Project
        composeTestRule.onNodeWithText("Project").performClick()
        composeTestRule.onNodeWithTag("screen_project").assertIsDisplayed()
        composeTestRule.onNodeWithText("Project").assertIsSelected()

        // Navigate to Actions
        composeTestRule.onNodeWithText("Actions").performClick()
        composeTestRule.onNodeWithTag("screen_actions").assertIsDisplayed()
        composeTestRule.onNodeWithText("Actions").assertIsSelected()

        // Navigate to Settings
        composeTestRule.onNode(hasText("Settings") and isSelectable()).performClick()
        composeTestRule.onNodeWithText(
            "Manage session access and platform preferences."
        ).assertIsDisplayed()
        composeTestRule.onNode(hasText("Settings") and isSelectable()).assertIsSelected()

        // Navigate back to Alerts
        composeTestRule.onNode(hasText("Alerts") and isSelectable()).performClick()
        composeTestRule.onNodeWithTag("screen_alerts").assertIsDisplayed()
        composeTestRule.onNode(hasText("Alerts") and isSelectable()).assertIsSelected()
    }
}
