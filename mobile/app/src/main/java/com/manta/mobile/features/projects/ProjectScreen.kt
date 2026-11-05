package com.manta.mobile.features.projects

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import com.manta.mobile.core.theme.ui.AppBackground
import com.manta.mobile.core.theme.ui.TextLight

@Composable
fun ProjectScreen() {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(AppBackground),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = "Project Screen",
            style = MaterialTheme.typography.headlineMedium,
            color = TextLight
        )
    }
}
