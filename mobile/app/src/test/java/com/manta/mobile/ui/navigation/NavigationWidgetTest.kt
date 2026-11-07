package com.manta.mobile.ui.navigation

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotSelected
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.junit4.createComposeRule
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
    fun appStartup_displaysStartDestinationAndAllTabs() {
        setNavShellContent()

        // Verify startup displays the Alerts destination screen
        composeTestRule.onNodeWithText("Alerts Screen").assertIsDisplayed()
        composeTestRule.onNodeWithText("Project Screen").assertDoesNotExist()
        composeTestRule.onNodeWithText("Actions Screen").assertDoesNotExist()
        composeTestRule.onNodeWithText("Settings Screen").assertDoesNotExist()

        // Verify all bottom navigation tabs are displayed
        composeTestRule.onNodeWithText("Alerts").assertIsDisplayed()
        composeTestRule.onNodeWithText("Project").assertIsDisplayed()
        composeTestRule.onNodeWithText("Actions").assertIsDisplayed()
        composeTestRule.onNodeWithText("Settings").assertIsDisplayed()

        // Verify initial tab selection state
        composeTestRule.onNodeWithText("Alerts").assertIsSelected()
        composeTestRule.onNodeWithText("Project").assertIsNotSelected()
        composeTestRule.onNodeWithText("Actions").assertIsNotSelected()
        composeTestRule.onNodeWithText("Settings").assertIsNotSelected()
    }

    @Test
    fun navigateToProjectTab_displaysProjectScreen() {
        setNavShellContent()

        // Click Project tab
        composeTestRule.onNodeWithText("Project").performClick()

        // Verify destination screen and selection
        composeTestRule.onNodeWithText("Project Screen").assertIsDisplayed()
        composeTestRule.onNodeWithText("Alerts Screen").assertDoesNotExist()
        composeTestRule.onNodeWithText("Project").assertIsSelected()
        composeTestRule.onNodeWithText("Alerts").assertIsNotSelected()
    }

    @Test
    fun navigateToActionsTab_displaysActionsScreen() {
        setNavShellContent()

        // Click Actions tab
        composeTestRule.onNodeWithText("Actions").performClick()

        // Verify destination screen and selection
        composeTestRule.onNodeWithText("Actions Screen").assertIsDisplayed()
        composeTestRule.onNodeWithText("Alerts Screen").assertDoesNotExist()
        composeTestRule.onNodeWithText("Actions").assertIsSelected()
        composeTestRule.onNodeWithText("Alerts").assertIsNotSelected()
    }

    @Test
    fun navigateToSettingsTab_displaysSettingsScreen() {
        setNavShellContent()

        // Click Settings tab
        composeTestRule.onNodeWithText("Settings").performClick()

        // Verify destination screen and selection
        composeTestRule.onNodeWithText("Settings Screen").assertIsDisplayed()
        composeTestRule.onNodeWithText("Alerts Screen").assertDoesNotExist()
        composeTestRule.onNodeWithText("Settings").assertIsSelected()
        composeTestRule.onNodeWithText("Alerts").assertIsNotSelected()
    }

    @Test
    fun navigateAcrossAllTabs_updatesDestinationAndSelectionCorrectly() {
        setNavShellContent()

        // Initial state: Alerts
        composeTestRule.onNodeWithText("Alerts Screen").assertIsDisplayed()
        composeTestRule.onNodeWithText("Alerts").assertIsSelected()

        // Navigate to Project
        composeTestRule.onNodeWithText("Project").performClick()
        composeTestRule.onNodeWithText("Project Screen").assertIsDisplayed()
        composeTestRule.onNodeWithText("Project").assertIsSelected()
        composeTestRule.onNodeWithText("Alerts Screen").assertDoesNotExist()

        // Navigate to Actions
        composeTestRule.onNodeWithText("Actions").performClick()
        composeTestRule.onNodeWithText("Actions Screen").assertIsDisplayed()
        composeTestRule.onNodeWithText("Actions").assertIsSelected()
        composeTestRule.onNodeWithText("Project Screen").assertDoesNotExist()

        // Navigate to Settings
        composeTestRule.onNodeWithText("Settings").performClick()
        composeTestRule.onNodeWithText("Settings Screen").assertIsDisplayed()
        composeTestRule.onNodeWithText("Settings").assertIsSelected()
        composeTestRule.onNodeWithText("Actions Screen").assertDoesNotExist()

        // Navigate back to Alerts
        composeTestRule.onNodeWithText("Alerts").performClick()
        composeTestRule.onNodeWithText("Alerts Screen").assertIsDisplayed()
        composeTestRule.onNodeWithText("Alerts").assertIsSelected()
        composeTestRule.onNodeWithText("Settings Screen").assertDoesNotExist()
    }
}
