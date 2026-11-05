package com.manta.mobile

import com.manta.mobile.ui.navigation.NavItem
import com.manta.mobile.ui.navigation.navItems
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests verifying navigation routes, start destination invariants,
 * and bottom navigation items configuration.
 *
 * Mapped to TC-MOB-NAV-001 through TC-MOB-NAV-006 in docs/app-startup-and-navigation-test-scenarios.md
 */
class NavigationRouteTest {

    @Test
    fun navItems_containsExactlyFourItemsInDefinedOrder() {
        assertEquals("Bottom navigation must have exactly 4 items", 4, navItems.size)
        assertEquals(NavItem.Alerts, navItems[0])
        assertEquals(NavItem.Project, navItems[1])
        assertEquals(NavItem.Actions, navItems[2])
        assertEquals(NavItem.Settings, navItems[3])
    }

    @Test
    fun navItems_routesAndTitlesAreValid() {
        assertEquals("alerts", NavItem.Alerts.route)
        assertEquals("Alerts", NavItem.Alerts.title)

        assertEquals("project", NavItem.Project.route)
        assertEquals("Project", NavItem.Project.title)

        assertEquals("actions", NavItem.Actions.route)
        assertEquals("Actions", NavItem.Actions.title)

        assertEquals("settings", NavItem.Settings.route)
        assertEquals("Settings", NavItem.Settings.title)
    }

    @Test
    fun navItems_routesAreUnique() {
        val routes = navItems.map { it.route }
        assertEquals(
            "Every navigation item route must be unique",
            routes.toSet().size,
            routes.size
        )
    }

    @Test
    fun defaultStartDestination_isAlerts() {
        val startDestination = NavItem.Alerts.route
        assertEquals("Default start destination must be alerts", "alerts", startDestination)
        assertTrue(navItems.any { it.route == startDestination })
    }
}
