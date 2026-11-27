package com.manta.mobile.features.alerts

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manta.mobile.core.theme.ui.AppBackground
import com.manta.mobile.core.theme.ui.Danger
import com.manta.mobile.core.theme.ui.DarkSurface
import com.manta.mobile.core.theme.ui.GrayText
import com.manta.mobile.core.theme.ui.Primary
import com.manta.mobile.core.theme.ui.TextLight
import com.manta.mobile.features.auth.AuthRepository
import com.manta.mobile.ui.FilterPillItem
import com.manta.mobile.ui.FilterPillRow
import com.manta.mobile.ui.PaginationFooter
import com.manta.mobile.ui.buttons.PrimaryButton
import com.manta.mobile.ui.cards.ActionCard
import com.manta.mobile.ui.cards.ActionSeverity

enum class AlertsScreenState {
    LiveFeed,
    EmptyAllClear,
    OfflineError,
    LoadingState
}

@Preview
@Composable
fun AlertsScreen(
    authRepository: AuthRepository? = null,
    onNavigateToProject: () -> Unit = {},
    onInspectAction: (String) -> Unit = {}
) {
    var screenState by remember { mutableStateOf(AlertsScreenState.LiveFeed) }
    var selectedSourceId by remember { mutableStateOf("all") }
    var currentPage by remember { mutableIntStateOf(1) }

    val filterPills = listOf(
        FilterPillItem("all", "All Sources", 16),
        FilterPillItem("github", "GitHub", 4),
        FilterPillItem("jira", "Jira", 6),
        FilterPillItem("slack", "Slack", 6)
    )

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(AppBackground)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp)
            .testTag("screen_alerts")
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = "Alerts",
                style = MaterialTheme.typography.headlineLarge,
                color = TextLight,
                fontWeight = FontWeight.Bold
            )

            Spacer(modifier = Modifier.width(10.dp))

            val (statusBadgeBg, statusBadgeText, statusBadgeColor) = when (screenState) {
                AlertsScreenState.LiveFeed -> Triple(
                    Color(0xFF3B1E2B),
                    "● 5 Active Risks",
                    Danger
                )

                AlertsScreenState.EmptyAllClear -> Triple(
                    Color(0xFF1E3A2B),
                    "● 0 Active Risks",
                    Primary
                )

                AlertsScreenState.OfflineError -> Triple(
                    Color(0xFF3B1E2B),
                    "● Offline",
                    Danger
                )

                AlertsScreenState.LoadingState -> Triple(
                    Color(0xFF1E2838),
                    "● Loading...",
                    Color(0xFF38BDF8)
                )
            }

            Box(
                modifier = Modifier
                    .background(statusBadgeBg, shape = RoundedCornerShape(12.dp))
                    .padding(horizontal = 10.dp, vertical = 4.dp)
                    .clickable {
                        screenState = when (screenState) {
                            AlertsScreenState.LiveFeed -> AlertsScreenState.EmptyAllClear
                            AlertsScreenState.EmptyAllClear -> AlertsScreenState.OfflineError
                            AlertsScreenState.OfflineError -> AlertsScreenState.LoadingState
                            AlertsScreenState.LoadingState -> AlertsScreenState.LiveFeed
                        }
                    }
            ) {
                Text(
                    text = statusBadgeText,
                    style = MaterialTheme.typography.labelMedium,
                    color = statusBadgeColor,
                    fontWeight = FontWeight.Bold,
                    fontSize = 11.sp
                )
            }

            Spacer(modifier = Modifier.weight(1f))

            IconButton(onClick = { screenState = AlertsScreenState.LiveFeed }) {
                Icon(
                    imageVector = Icons.Default.Refresh,
                    contentDescription = "Refresh Alerts",
                    tint = TextLight
                )
            }
        }

        Spacer(modifier = Modifier.height(12.dp))

        FilterPillRow(
            items = filterPills,
            selectedId = selectedSourceId,
            onItemSelected = { selectedSourceId = it }
        )

        Spacer(modifier = Modifier.height(16.dp))

        when (screenState) {
            AlertsScreenState.LiveFeed -> {
                ActionCard(
                    idNumber = "#1402",
                    title = "PR: Auth JWT refresh token",
                    severity = ActionSeverity.Danger,
                    badgeText = "⚠ 54h stuck (Critical SLA breach)",
                    serviceTag = "auth-service",
                    sourcePlatform = "GitHub",
                    timeAgo = "2h ago",
                    authorHandle = "@alex_k",
                    pingPongCycles = 6,
                    reviewerHandle = "@thijn_lead",
                    descriptionTitle = "AUTOMATED ANOMALY DETECTION",
                    descriptionBody =
                    "Deadlock risk in token invalidation handler during Redis failover. " +
                        "Security review hasn't progressed in 48 hours.",
                    onInspectClick = { onInspectAction("#1402") }
                )

                Spacer(modifier = Modifier.height(12.dp))

                ActionCard(
                    idNumber = "#1381",
                    title = "PR: Awaiting SecOps Approval",
                    severity = ActionSeverity.Alert,
                    badgeText = "● sec-ops-approval",
                    serviceTag = "auth-service",
                    sourcePlatform = "Jira",
                    timeAgo = "2h ago",
                    authorHandle = "@olena_r",
                    pingPongCycles = 1,
                    reviewerHandle = "@secops_lead",
                    descriptionTitle = "SLA TIMER WARNING",
                    descriptionBody =
                    "Dependency bump on crypto libraries pending zero-trust " +
                        "enclave review. Reviewer on vacation leave.",
                    onInspectClick = { onInspectAction("#1381") }
                )

                Spacer(modifier = Modifier.height(12.dp))

                ActionCard(
                    idNumber = "#1409",
                    title = "Code Coverage Drop Below 80%",
                    severity = ActionSeverity.Info,
                    badgeText = "testing",
                    serviceTag = "slack",
                    sourcePlatform = "Slack",
                    timeAgo = "2h ago",
                    authorHandle = "@david_m",
                    pingPongCycles = 2,
                    reviewerHandle = "Lead time +2.1h",
                    descriptionTitle = "COVERAGE REGRESSION",
                    descriptionBody = "Coverage regression in auth middleware unit tests.",
                    onInspectClick = { onInspectAction("#1409") }
                )

                Spacer(modifier = Modifier.height(16.dp))

                PaginationFooter(
                    currentPage = currentPage,
                    totalPages = 2,
                    showingRangeText = "Showing 1-8 of 16 alerts",
                    onPageSelected = { currentPage = it }
                )
            }

            AlertsScreenState.EmptyAllClear -> {
                Card(
                    modifier = Modifier.fillMaxWidth().padding(vertical = 24.dp),
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(containerColor = DarkSurface)
                ) {
                    Column(
                        modifier = Modifier.fillMaxWidth().padding(32.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Box(
                            modifier = Modifier
                                .size(72.dp)
                                .clip(CircleShape)
                                .background(Color(0xFF1E3A2B)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(
                                imageVector = Icons.Default.CheckCircle,
                                contentDescription = "All Clear",
                                tint = Primary,
                                modifier = Modifier.size(40.dp)
                            )
                        }

                        Spacer(modifier = Modifier.height(16.dp))

                        Text(
                            text = "All clear!",
                            style = MaterialTheme.typography.headlineMedium,
                            color = TextLight,
                            fontWeight = FontWeight.Bold
                        )

                        Spacer(modifier = Modifier.height(8.dp))

                        Text(
                            text = "No active risks or bottlenecks detected " +
                                "across monitored repositories & pipelines.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = GrayText,
                            textAlign = TextAlign.Center
                        )

                        Spacer(modifier = Modifier.height(24.dp))

                        PrimaryButton(
                            text = "Run Health Check",
                            onClick = { },
                            icon = Icons.Default.Shield
                        )

                        Spacer(modifier = Modifier.height(12.dp))

                        Text(
                            text = "Back to Project Screen",
                            style = MaterialTheme.typography.bodyMedium,
                            color = GrayText,
                            modifier = Modifier.clickable { onNavigateToProject() }
                        )
                    }
                }
            }

            AlertsScreenState.OfflineError -> {
                Card(
                    modifier = Modifier.fillMaxWidth().padding(vertical = 24.dp),
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(containerColor = DarkSurface)
                ) {
                    Column(
                        modifier = Modifier.fillMaxWidth().padding(32.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Box(
                            modifier = Modifier
                                .size(72.dp)
                                .clip(CircleShape)
                                .background(Color(0xFF3B1E2B)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(
                                imageVector = Icons.Default.Warning,
                                contentDescription = "Error",
                                tint = Danger,
                                modifier = Modifier.size(40.dp)
                            )
                        }

                        Spacer(modifier = Modifier.height(16.dp))

                        Text(
                            text = "Unable to load alerts",
                            style = MaterialTheme.typography.headlineMedium,
                            color = TextLight,
                            fontWeight = FontWeight.Bold
                        )

                        Spacer(modifier = Modifier.height(8.dp))

                        Text(
                            text = "Failed to establish real-time websocket " +
                                "connection with telemetry stream.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = GrayText,
                            textAlign = TextAlign.Center
                        )

                        Spacer(modifier = Modifier.height(24.dp))

                        PrimaryButton(
                            text = "RETRY CONNECTION",
                            onClick = { screenState = AlertsScreenState.LiveFeed },
                            icon = Icons.Default.Refresh
                        )

                        Spacer(modifier = Modifier.height(12.dp))

                        Text(
                            text = "View Diagnostics",
                            style = MaterialTheme.typography.bodyMedium,
                            color = GrayText,
                            modifier = Modifier.clickable { }
                        )
                    }
                }
            }

            AlertsScreenState.LoadingState -> {
                Column(modifier = Modifier.fillMaxWidth()) {
                    repeat(3) {
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(160.dp)
                                .padding(vertical = 6.dp)
                                .clip(RoundedCornerShape(16.dp))
                                .background(DarkSurface.copy(alpha = 0.5f))
                        )
                    }
                }
            }
        }
    }
}
