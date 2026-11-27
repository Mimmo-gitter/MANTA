package com.manta.mobile.ui.cards

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.SwapHoriz
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manta.mobile.core.theme.ui.Alert
import com.manta.mobile.core.theme.ui.AlertContainer
import com.manta.mobile.core.theme.ui.AppBackground
import com.manta.mobile.core.theme.ui.CyanText
import com.manta.mobile.core.theme.ui.Danger
import com.manta.mobile.core.theme.ui.DangerContainer
import com.manta.mobile.core.theme.ui.DarkSurface
import com.manta.mobile.core.theme.ui.GrayText
import com.manta.mobile.core.theme.ui.InfoContainer
import com.manta.mobile.core.theme.ui.SecondaryLight
import com.manta.mobile.core.theme.ui.TextLight
import com.manta.mobile.ui.buttons.PrimaryButton

enum class ActionSeverity {
    Danger,
    Alert,
    Info
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ActionCard(
    modifier: Modifier = Modifier,
    idNumber: String = "#1402",
    title: String = "PR: Auth JWT refresh token",
    severity: ActionSeverity = ActionSeverity.Danger,
    badgeText: String = "⚠ 54h stuck (Critical SLA breach)",
    serviceTag: String = "auth-service",
    sourcePlatform: String = "GitHub",
    timeAgo: String = "2h ago",
    authorHandle: String = "@alex_k",
    pingPongCycles: Int = 6,
    reviewerHandle: String = "@thijn_lead",
    descriptionTitle: String = "AUTOMATED ANOMALY DETECTION",
    descriptionBody: String =
        "Deadlock risk in token invalidation handler during Redis failover. " +
            "Security review hasn't progressed in 48 hours.",
    onInspectClick: (() -> Unit)? = null,
    onNudgeClick: (() -> Unit)? = null,
    onReassignClick: (() -> Unit)? = null
) {
    val indicatorColor = when (severity) {
        ActionSeverity.Danger -> Danger
        ActionSeverity.Alert -> Alert
        ActionSeverity.Info -> SecondaryLight
    }
    val badgeBg = when (severity) {
        ActionSeverity.Danger -> DangerContainer
        ActionSeverity.Alert -> AlertContainer
        ActionSeverity.Info -> InfoContainer
    }

    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = DarkSurface)
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .drawBehind {
                    val strokeWidthPx = 4.dp.toPx()
                    drawLine(
                        color = indicatorColor,
                        start = Offset(strokeWidthPx / 2, 0f),
                        end = Offset(strokeWidthPx / 2, size.height),
                        strokeWidth = strokeWidthPx
                    )
                }
                .padding(start = 20.dp, top = 16.dp, end = 16.dp, bottom = 16.dp)
        ) {
            FlowRow(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                Text(
                    text = idNumber,
                    style = MaterialTheme.typography.titleMedium,
                    color = TextLight,
                    fontWeight = FontWeight.Bold
                )
                Box(
                    modifier = Modifier
                        .background(badgeBg, shape = RoundedCornerShape(8.dp))
                        .padding(horizontal = 8.dp, vertical = 4.dp)
                ) {
                    Text(
                        text = badgeText,
                        style = MaterialTheme.typography.labelMedium,
                        color = indicatorColor,
                        fontWeight = FontWeight.Bold,
                        fontSize = 11.sp
                    )
                }
                Box(
                    modifier = Modifier
                        .background(InfoContainer, shape = RoundedCornerShape(6.dp))
                        .padding(horizontal = 8.dp, vertical = 2.dp)
                ) {
                    Text(
                        text = serviceTag,
                        style = MaterialTheme.typography.labelMedium,
                        color = CyanText,
                        fontSize = 11.sp
                    )
                }
            }
            Spacer(modifier = Modifier.height(6.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = sourcePlatform,
                    style = MaterialTheme.typography.bodySmall,
                    color = GrayText
                )
                Spacer(modifier = Modifier.weight(1f))
                Text(
                    text = "🕒 $timeAgo",
                    style = MaterialTheme.typography.bodySmall,
                    color = GrayText
                )
            }
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = title,
                style = MaterialTheme.typography.titleLarge,
                color = TextLight,
                fontWeight = FontWeight.Bold,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis
            )
            Spacer(modifier = Modifier.height(6.dp))
            Text(
                text = "Author: $authorHandle • $pingPongCycles ping-pong cycles",
                style = MaterialTheme.typography.bodyMedium,
                color = GrayText
            )
            Spacer(modifier = Modifier.height(6.dp))
            Text(
                text = "Waiting on $reviewerHandle",
                style = MaterialTheme.typography.bodyMedium,
                color = TextLight,
                fontWeight = FontWeight.SemiBold
            )
            Spacer(modifier = Modifier.height(12.dp))
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(AppBackground, shape = RoundedCornerShape(10.dp))
                    .padding(12.dp)
            ) {
                Column {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            imageVector = Icons.Default.Warning,
                            contentDescription = null,
                            modifier = Modifier.size(14.dp),
                            tint = indicatorColor
                        )
                        Spacer(modifier = Modifier.width(6.dp))
                        Text(
                            text = descriptionTitle,
                            style = MaterialTheme.typography.labelMedium,
                            color = TextLight,
                            fontWeight = FontWeight.Bold,
                            fontSize = 11.sp
                        )
                    }
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = descriptionBody,
                        style = MaterialTheme.typography.bodySmall,
                        color = indicatorColor,
                        fontSize = 12.sp
                    )
                }
            }
            Spacer(modifier = Modifier.height(12.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End
            ) {
                if (onInspectClick != null) {
                    OutlinedButton(
                        onClick = onInspectClick,
                        modifier = Modifier
                            .width(120.dp)
                            .height(48.dp),
                        shape = RoundedCornerShape(8.dp),
                        border = BorderStroke(1.dp, SecondaryLight),
                        colors = ButtonDefaults.outlinedButtonColors(
                            containerColor = Color.Transparent,
                            contentColor = TextLight
                        )
                    ) {
                        Text(
                            text = "Inspect",
                            color = TextLight,
                            fontWeight = FontWeight.Bold,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                } else {
                    if (onNudgeClick != null) {
                        PrimaryButton(
                            text = "Nudge $reviewerHandle",
                            onClick = onNudgeClick,
                            modifier = Modifier
                                .weight(1f)
                                .height(48.dp),
                            icon = Icons.AutoMirrored.Filled.Send
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                    }
                    if (onReassignClick != null) {
                        OutlinedButton(
                            onClick = onReassignClick,
                            modifier = Modifier
                                .weight(1f)
                                .height(48.dp),
                            shape = RoundedCornerShape(12.dp),
                            border = BorderStroke(1.dp, SecondaryLight),
                            colors = ButtonDefaults.outlinedButtonColors(
                                containerColor = Color.Transparent,
                                contentColor = TextLight
                            )
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.Center,
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Icon(
                                    imageVector = Icons.Default.SwapHoriz,
                                    contentDescription = null,
                                    tint = TextLight,
                                    modifier = Modifier.size(16.dp)
                                )
                                Spacer(modifier = Modifier.width(4.dp))
                                Text(
                                    "Reassign",
                                    color = TextLight,
                                    fontWeight = FontWeight.Bold,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}
