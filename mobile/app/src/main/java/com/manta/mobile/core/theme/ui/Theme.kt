package com.manta.mobile.core.theme.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable

private val DarkColorScheme = darkColorScheme(
    primary = Primary,
    secondary = Secondary,
    secondaryContainer = SecondaryTransparent,
    background = AppBackground,
    surface = SurfaceDark,
    onBackground = TextLight,
    onSurface = TextLight,
    onPrimary = TextLight,
    onSecondary = TextLight
)

@Composable
fun MobileTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DarkColorScheme,
        typography = Typography,
        content = content
    )
}
