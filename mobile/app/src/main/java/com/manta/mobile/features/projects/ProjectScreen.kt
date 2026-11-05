package com.manta.mobile.features.projects

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import com.manta.mobile.core.theme.ui.AppBackground
import com.manta.mobile.core.theme.ui.SecondaryTransparent
import com.manta.mobile.core.theme.ui.TextLight

@Composable
fun ProjectScreen(modifier: Modifier = Modifier) {
    var count by rememberSaveable { mutableIntStateOf(0) }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(AppBackground)
            .testTag("screen_project"),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text(
            text = "Project Screen",
            style = MaterialTheme.typography.headlineMedium,
            color = TextLight
        )
        Spacer(modifier = Modifier.height(16.dp))
        Button(
            onClick = { count++ },
            modifier = Modifier.testTag("project_counter_button"),
            colors = ButtonDefaults.buttonColors(containerColor = SecondaryTransparent)
        ) {
            Text(
                text = "Counter: $count",
                color = TextLight
            )
        }
    }
}
