package com.manta.mobile.ui.navigation

import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.manta.mobile.core.theme.ui.AppBackground
import com.manta.mobile.core.theme.ui.MutedText
import com.manta.mobile.core.theme.ui.SecondaryTransparent
import com.manta.mobile.core.theme.ui.TextLight
import com.manta.mobile.features.actions.ActionsScreen
import com.manta.mobile.features.alerts.AlertsScreen
import com.manta.mobile.features.projects.ProjectScreen
import com.manta.mobile.features.settings.SettingsScreen

@Composable
fun NavShell(
    modifier: Modifier = Modifier,
    navController: NavHostController = rememberNavController()
) {
    Scaffold(
        modifier = modifier,
        bottomBar = {
            NavigationBar(
                modifier = Modifier.testTag("bottom_nav_bar"),
                containerColor = AppBackground,
                contentColor = TextLight
            ) {
                val navBackStackEntry by navController.currentBackStackEntryAsState()
                val currentDestination = navBackStackEntry?.destination

                navItems.forEach { item ->
                    val selected =
                        currentDestination?.hierarchy?.any { it.route == item.route } == true
                    NavigationBarItem(
                        modifier = Modifier.testTag("tab_${item.route}"),
                        selected = selected,
                        onClick = {
                            navController.navigate(item.route) {
                                popUpTo(navController.graph.findStartDestination().id) {
                                    saveState = true
                                }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                        icon = {
                            Icon(imageVector = item.icon, contentDescription = item.title)
                        },
                        label = {
                            Text(
                                text = item.title,
                                style = MaterialTheme.typography.labelMedium
                            )
                        },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = TextLight,
                            selectedTextColor = TextLight,
                            indicatorColor = SecondaryTransparent,
                            unselectedIconColor = MutedText,
                            unselectedTextColor = MutedText
                        )
                    )
                }
            }
        }
    ) { innerPadding ->
        NavHost(
            navController = navController,
            startDestination = NavItem.Alerts.route,
            modifier = Modifier.padding(innerPadding)
        ) {
            composable(NavItem.Alerts.route) { AlertsScreen() }
            composable(NavItem.Project.route) { ProjectScreen() }
            composable(NavItem.Actions.route) { ActionsScreen() }
            composable(NavItem.Settings.route) { SettingsScreen() }
        }
    }
}
