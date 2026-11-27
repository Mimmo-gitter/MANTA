package com.manta.mobile.ui.cards

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manta.mobile.core.theme.ui.BorderDivider
import com.manta.mobile.core.theme.ui.CyanText
import com.manta.mobile.core.theme.ui.Danger
import com.manta.mobile.core.theme.ui.DangerContainer
import com.manta.mobile.core.theme.ui.DarkSurface
import com.manta.mobile.core.theme.ui.GrayText
import com.manta.mobile.core.theme.ui.Info
import com.manta.mobile.core.theme.ui.Primary
import com.manta.mobile.core.theme.ui.SecondaryContainer
import com.manta.mobile.core.theme.ui.SecondaryLight
import com.manta.mobile.core.theme.ui.TextLight

enum class DeploymentStatus {
    Success,
    FailedRollback,
    InProgress
}

@Preview
@Composable
fun TimeLogCard(
    modifier: Modifier = Modifier,
    status: DeploymentStatus = DeploymentStatus.Success,
    environment: String = "PRODUCTION",
    commitHash: String = "a1b2c3d",
    serviceName: String = "auth-service",
    version: String = "v2.4.2",
    timeAgo: String = "14m ago",
    duration: String = "2m 14s",
    authorName: String = "Alex Vance",
    branchName: String = "main",
    errorMessage: String? = null,
    inProgressStage: String? = null,
    inProgressPercent: Int? = null
) {
    val statusColor = when (status) {
        DeploymentStatus.Success -> Primary
        DeploymentStatus.FailedRollback -> Danger
        DeploymentStatus.InProgress -> Info
    }

    val statusText = when (status) {
        DeploymentStatus.Success -> "Success"
        DeploymentStatus.FailedRollback -> "Failed / Rollback"
        DeploymentStatus.InProgress -> "In Progress"
    }

    val statusIcon = when (status) {
        DeploymentStatus.Success -> Icons.Default.CheckCircle
        DeploymentStatus.FailedRollback -> Icons.Default.Error
        DeploymentStatus.InProgress -> Icons.Default.Sync
    }

    Row(
        modifier = modifier
            .fillMaxWidth()
            .height(IntrinsicSize.Min)
    ) {
        Column(
            modifier = Modifier.fillMaxHeight(),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Box(
                modifier = Modifier
                    .size(14.dp)
                    .clip(CircleShape)
                    .background(statusColor)
            )
            Box(
                modifier = Modifier
                    .width(2.dp)
                    .fillMaxHeight()
                    .background(BorderDivider)
            )
        }

        Spacer(modifier = Modifier.width(12.dp))

        Card(
            modifier = Modifier
                .weight(1f)
                .fillMaxHeight(),
            shape = RoundedCornerShape(16.dp),
            colors = CardDefaults.cardColors(containerColor = DarkSurface)
        ) {
            Column(modifier = Modifier.padding(16.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        modifier = Modifier
                            .background(
                                statusColor.copy(alpha = 0.15f),
                                shape = RoundedCornerShape(6.dp)
                            )
                            .padding(horizontal = 8.dp, vertical = 2.dp)
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(
                                imageVector = statusIcon,
                                contentDescription = null,
                                modifier = Modifier.size(12.dp),
                                tint = statusColor
                            )
                            Spacer(modifier = Modifier.width(4.dp))
                            Text(
                                text = statusText,
                                style = MaterialTheme.typography.labelMedium,
                                color = statusColor,
                                fontWeight = FontWeight.Bold,
                                fontSize = 11.sp
                            )
                        }
                    }

                    Spacer(modifier = Modifier.width(6.dp))

                    Box(
                        modifier = Modifier
                            .background(
                                SecondaryContainer,
                                shape = RoundedCornerShape(6.dp)
                            )
                            .padding(horizontal = 8.dp, vertical = 2.dp)
                    ) {
                        Text(
                            text = environment,
                            style = MaterialTheme.typography.labelMedium,
                            color = SecondaryLight,
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Bold
                        )
                    }

                    Spacer(modifier = Modifier.weight(1f))

                    Box(
                        modifier = Modifier
                            .border(
                                1.dp,
                                BorderDivider,
                                shape = RoundedCornerShape(4.dp)
                            )
                            .padding(horizontal = 8.dp, vertical = 2.dp)
                    ) {
                        Text(
                            text = commitHash,
                            style = MaterialTheme.typography.labelMedium,
                            color = CyanText,
                            maxLines = 1,
                            fontSize = 11.sp
                        )
                    }
                }

                Spacer(modifier = Modifier.height(10.dp))

                Text(
                    text = "$serviceName $version",
                    style = MaterialTheme.typography.titleMedium,
                    color = TextLight,
                    fontWeight = FontWeight.Bold
                )

                Spacer(modifier = Modifier.height(6.dp))

                Text(
                    text = "🕒 Deployed • $timeAgo (Duration: $duration)",
                    style = MaterialTheme.typography.bodySmall,
                    color = GrayText
                )

                if (!errorMessage.isNullOrBlank()) {
                    Spacer(modifier = Modifier.height(10.dp))
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(
                                DangerContainer,
                                shape = RoundedCornerShape(8.dp)
                            )
                            .padding(10.dp)
                    ) {
                        Row(verticalAlignment = Alignment.Top) {
                            Icon(
                                imageVector = Icons.Default.Error,
                                contentDescription = null,
                                modifier = Modifier.size(16.dp),
                                tint = Danger
                            )
                            Spacer(modifier = Modifier.width(6.dp))
                            Text(
                                text = errorMessage,
                                style = MaterialTheme.typography.bodySmall,
                                color = Danger,
                                fontSize = 12.sp
                            )
                        }
                    }
                }

                if (inProgressPercent != null) {
                    Spacer(modifier = Modifier.height(10.dp))
                    LinearProgressIndicator(
                        progress = { inProgressPercent / 100f },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(6.dp)
                            .clip(RoundedCornerShape(3.dp)),
                        color = CyanText,
                        trackColor = BorderDivider
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = "Stage: ${inProgressStage ?: "running"} $inProgressPercent%",
                        style = MaterialTheme.typography.labelMedium,
                        color = GrayText,
                        fontSize = 11.sp
                    )
                }

                Spacer(modifier = Modifier.height(10.dp))

                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = "👤 $authorName",
                        style = MaterialTheme.typography.bodySmall,
                        color = TextLight
                    )
                    Spacer(modifier = Modifier.weight(1f))
                    Text(
                        text = "🌿 $branchName",
                        style = MaterialTheme.typography.bodySmall,
                        color = GrayText
                    )
                }
            }
        }
    }
}
