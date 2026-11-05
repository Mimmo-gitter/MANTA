package com.manta.mobile.ui.navigation

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Bolt
import androidx.compose.material.icons.outlined.Folder
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.ui.graphics.vector.ImageVector

sealed class NavItem(val route: String, val title: String, val icon: ImageVector) {
    data object Alerts : NavItem("alerts", "Alerts", Icons.Outlined.Notifications)
    data object Project : NavItem("project", "Project", Icons.Outlined.Folder)
    data object Actions : NavItem("actions", "Actions", Icons.Outlined.Bolt)
    data object Settings : NavItem("settings", "Settings", Icons.Outlined.Tune)
}

val navItems = listOf(
    NavItem.Alerts,
    NavItem.Project,
    NavItem.Actions,
    NavItem.Settings
)
