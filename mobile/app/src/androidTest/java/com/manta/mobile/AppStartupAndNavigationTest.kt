package com.manta.mobile

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.espresso.Espresso
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.manta.mobile.core.theme.ui.MobileTheme
import com.manta.mobile.ui.navigation.NavShell
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Instrumented Compose UI / Widget test suite covering application startup,
 * bottom navigation bar behavior, state retention across tab switches,
 * screen rotation / configuration change, and process restart / state restoration.
 *
 * Mapped to test scenarios documented in docs/app-startup-and-navigation-test-scenarios.md
 * Task: SCRUM-349 (Parent: SCRUM-254 / US-79)
 */
@RunWith(AndroidJUnit4::class)
class AppStartupAndNavigationTest {

    @get:Rule
    val composeTestRule = createAndroidComposeRule<MainActivity>()

    /**
     * TC-MOB-NAV-001: Cold Start & Default Destination Verification
     * Verifies that when the app launches:
     * - The bottom navigation bar is visible.
     * - All 4 navigation tabs (Alerts, Project, Actions, Settings) are present.
     * - The Alerts tab is selected by default.
     * - The Alerts Screen content is displayed.
     */
    @Test
    fun coldStart_defaultDestinationIsAlertsTab() {
        // Assert bottom navigation bar exists and is displayed
        composeTestRule.onNodeWithTag("bottom_nav_bar").assertIsDisplayed()

        // Assert all 4 tabs exist and Alerts is selected by default
        composeTestRule.onNodeWithTag("tab_alerts").assertIsDisplayed().assertIsSelected()
        composeTestRule.onNodeWithTag("tab_project").assertIsDisplayed()
        composeTestRule.onNodeWithTag("tab_actions").assertIsDisplayed()
        composeTestRule.onNodeWithTag("tab_settings").assertIsDisplayed()

        // Assert start screen content
        composeTestRule.onNodeWithTag("screen_alerts").assertIsDisplayed()
        composeTestRule.onNodeWithText("Alerts Screen").assertIsDisplayed()
    }

    /**
     * TC-MOB-NAV-002: Tab Navigation - Alerts to Project Screen
     * Verifies navigating from Alerts to Project screen updates selection and content.
     */
    @Test
    fun navigateToProjectTab_displaysProjectScreen() {
        composeTestRule.onNodeWithTag("tab_project").performClick()

        composeTestRule.onNodeWithTag("tab_project").assertIsSelected()
        composeTestRule.onNodeWithTag("screen_project").assertIsDisplayed()
        composeTestRule.onNodeWithText("Project Screen").assertIsDisplayed()
    }

    /**
     * TC-MOB-NAV-003: Tab Navigation - Project to Actions Screen
     * Verifies navigating to Actions screen updates selection and content.
     */
    @Test
    fun navigateToActionsTab_displaysActionsScreen() {
        composeTestRule.onNodeWithTag("tab_actions").performClick()

        composeTestRule.onNodeWithTag("tab_actions").assertIsSelected()
        composeTestRule.onNodeWithTag("screen_actions").assertIsDisplayed()
        composeTestRule.onNodeWithText("Actions Screen").assertIsDisplayed()
    }

    /**
     * TC-MOB-NAV-004: Tab Navigation - Actions to Settings Screen
     * Verifies navigating to Settings screen updates selection and content.
     */
    @Test
    fun navigateToSettingsTab_displaysSettingsScreen() {
        composeTestRule.onNodeWithTag("tab_settings").performClick()

        composeTestRule.onNodeWithTag("tab_settings").assertIsSelected()
        composeTestRule.onNodeWithTag("screen_settings").assertIsDisplayed()
        composeTestRule.onNodeWithText("Settings Screen").assertIsDisplayed()
    }

    /**
     * TC-MOB-NAV-005: Full Inter-Tab Navigation Cycle & Arbitrary Switching
     * Verifies sequential and arbitrary tab switching across all 4 destinations.
     */
    @Test
    fun navigateBetweenAllTabs_fullCycle() {
        // Start on Alerts
        composeTestRule.onNodeWithTag("screen_alerts").assertIsDisplayed()

        // Alerts -> Project
        composeTestRule.onNodeWithTag("tab_project").performClick()
        composeTestRule.onNodeWithTag("screen_project").assertIsDisplayed()

        // Project -> Actions
        composeTestRule.onNodeWithTag("tab_actions").performClick()
        composeTestRule.onNodeWithTag("screen_actions").assertIsDisplayed()

        // Actions -> Settings
        composeTestRule.onNodeWithTag("tab_settings").performClick()
        composeTestRule.onNodeWithTag("screen_settings").assertIsDisplayed()

        // Settings -> Alerts
        composeTestRule.onNodeWithTag("tab_alerts").performClick()
        composeTestRule.onNodeWithTag("screen_alerts").assertIsDisplayed()
        composeTestRule.onNodeWithTag("tab_alerts").assertIsSelected()
    }

    /**
     * TC-MOB-NAV-006: Single-Top Tab Re-Selection
     * Re-tapping active tab maintains single top and does not push duplicate screens.
     */
    @Test
    fun reselectingActiveTab_preservesSingleTop() {
        composeTestRule.onNodeWithTag("tab_project").performClick()
        composeTestRule.onNodeWithTag("screen_project").assertIsDisplayed()

        // Tap Project again
        composeTestRule.onNodeWithTag("tab_project").performClick()
        composeTestRule.onNodeWithTag("screen_project").assertIsDisplayed()
        composeTestRule.onNodeWithTag("tab_project").assertIsSelected()
    }

    /**
     * TC-MOB-NAV-007: State Preservation on Tab Switch
     * Verifies that interactive state in ProjectScreen is preserved when switching
     * to another tab (Alerts) and returning.
     */
    @Test
    fun statePreserved_onTabSwitch() {
        // Navigate to Project tab
        composeTestRule.onNodeWithTag("tab_project").performClick()
        composeTestRule.onNodeWithTag("screen_project").assertIsDisplayed()

        // Initial counter is 0
        composeTestRule.onNodeWithText("Counter: 0").assertIsDisplayed()

        // Increment counter to 1
        composeTestRule.onNodeWithTag("project_counter_button").performClick()
        composeTestRule.onNodeWithText("Counter: 1").assertIsDisplayed()

        // Switch away to Alerts tab
        composeTestRule.onNodeWithTag("tab_alerts").performClick()
        composeTestRule.onNodeWithTag("screen_alerts").assertIsDisplayed()

        // Switch back to Project tab
        composeTestRule.onNodeWithTag("tab_project").performClick()
        composeTestRule.onNodeWithTag("screen_project").assertIsDisplayed()

        // Assert state was preserved
        composeTestRule.onNodeWithText("Counter: 1").assertIsDisplayed()
    }

    /**
     * TC-MOB-NAV-008: System Back Navigation
     * Pressing system back from a secondary tab pops back to the start destination (Alerts).
     */
    @Test
    fun systemBack_fromSecondaryTab_popsToStartDestination() {
        // Navigate to Settings
        composeTestRule.onNodeWithTag("tab_settings").performClick()
        composeTestRule.onNodeWithTag("screen_settings").assertIsDisplayed()
        composeTestRule.onNodeWithTag("tab_settings").assertIsSelected()

        // Press system back
        Espresso.pressBack()

        // Navigation pops to start destination Alerts
        composeTestRule.onNodeWithTag("screen_alerts").assertIsDisplayed()
        composeTestRule.onNodeWithTag("tab_alerts").assertIsSelected()
    }

    /**
     * TC-MOB-NAV-009: Configuration Change / Screen Rotation
     * Simulates device rotation / activity recreation and verifies active tab and state survive.
     */
    @Test
    fun configurationChange_rotation_preservesActiveTabAndState() {
        // Navigate to Project and mutate state
        composeTestRule.onNodeWithTag("tab_project").performClick()
        composeTestRule.onNodeWithTag("project_counter_button").performClick()
        composeTestRule.onNodeWithText("Counter: 1").assertIsDisplayed()

        // Trigger Activity recreation (simulating device rotation portrait <-> landscape)
        composeTestRule.activityRule.scenario.recreate()

        // Assert Project tab is still selected, Project screen visible, and counter state restored
        composeTestRule.onNodeWithTag("tab_project").assertIsSelected()
        composeTestRule.onNodeWithTag("screen_project").assertIsDisplayed()
        composeTestRule.onNodeWithText("Counter: 1").assertIsDisplayed()
    }

    /**
     * TC-MOB-NAV-010: Process Restart & Saved State Restoration
     * Emulates process death and recreation via Compose StateRestorationTester.
     */
    @Test
    fun processRestart_stateRestoration_preservesNavigationAndState() {
        val restorationTester = StateRestorationTester(composeTestRule)

        restorationTester.setContent {
            MobileTheme {
                NavShell()
            }
        }

        // Navigate to Project
        composeTestRule.onNodeWithTag("tab_project").performClick()
        composeTestRule.onNodeWithTag("screen_project").assertIsDisplayed()
        composeTestRule.onNodeWithTag("project_counter_button").performClick()
        composeTestRule.onNodeWithText("Counter: 1").assertIsDisplayed()

        // Emulate process restart / saved instance state restore
        restorationTester.emulateSavedInstanceStateRestore()

        // Verify restoration of screen and counter state
        composeTestRule.onNodeWithTag("tab_project").assertIsSelected()
        composeTestRule.onNodeWithTag("screen_project").assertIsDisplayed()
        composeTestRule.onNodeWithText("Counter: 1").assertIsDisplayed()
    }
}
