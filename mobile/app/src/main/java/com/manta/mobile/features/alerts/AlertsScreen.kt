package com.manta.mobile.features.alerts

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.manta.mobile.core.theme.ui.AppBackground
import com.manta.mobile.core.theme.ui.TextLight

@Composable
fun AlertsScreen() {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(AppBackground),
        contentAlignment = Alignment.Center
    ) {
        Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
            Text(
                text = "Alerts Screen",
                style = MaterialTheme.typography.headlineMedium,
                color = TextLight
            )
            val message by MessageRepository.latestMessage.collectAsState()
            Spacer(modifier = Modifier.height(16.dp))
            Text("Latest FCM Message: $message", style = MaterialTheme.typography.bodyLarge)
        }
    }
}
