package com.manta.mobile.features.alerts

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import com.manta.mobile.core.theme.ui.AppBackground
import com.manta.mobile.core.theme.ui.TextLight

@Composable
fun AlertsScreen(modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(AppBackground)
            .testTag("screen_alerts"),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = "Alerts Screen",
            style = MaterialTheme.typography.headlineMedium,
            color = TextLight
        )
    }
}
