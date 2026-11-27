package com.manta.mobile.features.projects

import androidx.compose.foundation.background
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
import androidx.compose.material.icons.filled.RocketLaunch
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
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
import com.manta.mobile.core.network.DoraMetricsResponse
import com.manta.mobile.core.network.ProjectApiService
import com.manta.mobile.core.network.RetrofitClientFactory
import com.manta.mobile.core.theme.ui.AppBackground
import com.manta.mobile.core.theme.ui.BorderDivider
import com.manta.mobile.core.theme.ui.CyanText
import com.manta.mobile.core.theme.ui.Danger
import com.manta.mobile.core.theme.ui.DangerContainer
import com.manta.mobile.core.theme.ui.DarkSurface
import com.manta.mobile.core.theme.ui.GrayText
import com.manta.mobile.core.theme.ui.Primary
import com.manta.mobile.core.theme.ui.PrimaryContainer
import com.manta.mobile.core.theme.ui.SecondaryContainer
import com.manta.mobile.core.theme.ui.SecondaryLight
import com.manta.mobile.core.theme.ui.TextLight
import com.manta.mobile.features.auth.AuthRepository
import com.manta.mobile.ui.FilterPillItem
import com.manta.mobile.ui.FilterPillRow
import com.manta.mobile.ui.PaginationFooter
import com.manta.mobile.ui.buttons.PrimaryButton
import com.manta.mobile.ui.cards.DeploymentStatus
import com.manta.mobile.ui.cards.TimeLogCard
import com.patrykandpatrick.vico.compose.cartesian.CartesianChartHost
import com.patrykandpatrick.vico.compose.cartesian.data.CartesianChartModelProducer
import com.patrykandpatrick.vico.compose.cartesian.data.columnModel
import com.patrykandpatrick.vico.compose.cartesian.data.lineModel
import com.patrykandpatrick.vico.compose.cartesian.layer.rememberColumnCartesianLayer
import com.patrykandpatrick.vico.compose.cartesian.layer.rememberLineCartesianLayer
import com.patrykandpatrick.vico.compose.cartesian.rememberCartesianChart

enum class ProjectScreenState {
    LiveTelemetry,
    EmptyState,
    ErrorState,
    LoadingState
}

@Preview
@Composable
fun ProjectScreen(
    modifier: Modifier = Modifier,
    authRepository: AuthRepository? = null,
    selectedProjectId: String = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
) {
    var screenState by remember { mutableStateOf(ProjectScreenState.LiveTelemetry) }
    var selectedEnvId by remember { mutableStateOf("all") }
    var currentPage by remember { mutableIntStateOf(1) }

    var doraMetrics by remember { mutableStateOf<DoraMetricsResponse?>(null) }

    LaunchedEffect(authRepository, selectedProjectId) {
        if (authRepository != null) {
            try {
                val service =
                    RetrofitClientFactory.createAuthenticatedService<ProjectApiService>(
                        authRepository
                    )
                val response = service.getDoraMetrics(selectedProjectId)
                if (response.isSuccessful && response.body() != null) {
                    doraMetrics = response.body()
                }
            } catch (_: Exception) {
            }
        }
    }

    val envPills = listOf(
        FilterPillItem("all", "All Environments"),
        FilterPillItem("production", "Production"),
        FilterPillItem("staging", "Staging")
    )

    val freqValues = doraMetrics?.trend?.mapNotNull { it.deploymentFrequencyPerDay }
        ?.ifEmpty { null } ?: listOf(1.2, 2.0, 3.4, 2.8, 3.4)
    val leadValues = doraMetrics?.trend?.mapNotNull { it.leadTimeHours }
        ?.ifEmpty { null } ?: listOf(28.0, 23.5, 18.0, 16.0, 14.2)

    val barProducer = remember { CartesianChartModelProducer() }
    val lineProducer = remember { CartesianChartModelProducer() }

    LaunchedEffect(freqValues, leadValues) {
        barProducer.runTransaction {
            columnModel { series(freqValues) }
        }
        lineProducer.runTransaction {
            lineModel { series(leadValues) }
        }
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(AppBackground)
            .testTag("screen_project")
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 12.dp)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column {
                    Text(
                        text = "Deployment Status",
                        style = MaterialTheme.typography.headlineLarge,
                        color = TextLight,
                        fontWeight = FontWeight.Bold
                    )
                    Text(
                        text = "Continuous Delivery & DORA Velocity metrics",
                        style = MaterialTheme.typography.bodyMedium,
                        color = GrayText
                    )
                }

                Spacer(modifier = Modifier.weight(1f))

                IconButton(onClick = {
                    screenState = when (screenState) {
                        ProjectScreenState.LiveTelemetry -> ProjectScreenState.EmptyState
                        ProjectScreenState.EmptyState -> ProjectScreenState.ErrorState
                        ProjectScreenState.ErrorState -> ProjectScreenState.LoadingState
                        ProjectScreenState.LoadingState -> ProjectScreenState.LiveTelemetry
                    }
                }) {
                    Icon(
                        imageVector = Icons.Default.Refresh,
                        contentDescription = "Cycle State",
                        tint = TextLight
                    )
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            FilterPillRow(
                items = envPills,
                selectedId = selectedEnvId,
                onItemSelected = { selectedEnvId = it }
            )

            Spacer(modifier = Modifier.height(16.dp))

            when (screenState) {
                ProjectScreenState.LiveTelemetry -> {
                    Column {
                        Row(modifier = Modifier.fillMaxWidth()) {
                            // Card 1: Deploy Frequency
                            Card(
                                modifier = Modifier.weight(1f).height(175.dp),
                                shape = RoundedCornerShape(16.dp),
                                colors = CardDefaults.cardColors(containerColor = DarkSurface)
                            ) {
                                Column(modifier = Modifier.padding(12.dp)) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Text(
                                            text = "Deploy Frequency",
                                            style = MaterialTheme.typography.labelMedium,
                                            color = GrayText,
                                            fontSize = 10.sp,
                                            maxLines = 1
                                        )
                                        Spacer(modifier = Modifier.weight(1f))
                                        Text(
                                            text = "↑18%",
                                            style = MaterialTheme.typography.labelMedium,
                                            color = Primary,
                                            fontWeight = FontWeight.Bold,
                                            fontSize = 11.sp,
                                            maxLines = 1
                                        )
                                    }
                                    val freqVal = doraMetrics?.aggregates?.deploymentFrequencyPerDay
                                    val freqText = if (freqVal !=
                                        null
                                    ) {
                                        "$freqVal / day"
                                    } else {
                                        "3.4 / day"
                                    }
                                    Text(
                                        text = freqText,
                                        style = MaterialTheme.typography.headlineSmall,
                                        color = TextLight,
                                        fontWeight = FontWeight.Bold
                                    )
                                    Box(
                                        modifier = Modifier
                                            .background(
                                                SecondaryContainer,
                                                shape = RoundedCornerShape(6.dp)
                                            )
                                            .padding(horizontal = 6.dp, vertical = 2.dp)
                                    ) {
                                        Text(
                                            text = "Elite Velocity",
                                            style = MaterialTheme.typography.labelMedium,
                                            color = SecondaryLight,
                                            fontSize = 10.sp
                                        )
                                    }
                                    Spacer(modifier = Modifier.height(6.dp))
                                    CartesianChartHost(
                                        chart = rememberCartesianChart(
                                            rememberColumnCartesianLayer()
                                        ),
                                        modelProducer = barProducer,
                                        modifier = Modifier.fillMaxWidth().height(40.dp)
                                    )
                                }
                            }

                            Spacer(modifier = Modifier.width(8.dp))

                            // Card 2: Lead Time (Commit-to-Prod)
                            Card(
                                modifier = Modifier.weight(1f).height(175.dp),
                                shape = RoundedCornerShape(16.dp),
                                colors = CardDefaults.cardColors(containerColor = DarkSurface)
                            ) {
                                Column(modifier = Modifier.padding(12.dp)) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Text(
                                            text = "Lead Time",
                                            style = MaterialTheme.typography.labelMedium,
                                            color = GrayText,
                                            fontSize = 10.sp,
                                            maxLines = 1
                                        )
                                        Spacer(modifier = Modifier.weight(1f))
                                        Text(
                                            text = "↓4.5h",
                                            style = MaterialTheme.typography.labelMedium,
                                            color = Primary,
                                            fontWeight = FontWeight.Bold,
                                            fontSize = 11.sp,
                                            maxLines = 1
                                        )
                                    }
                                    val leadVal = doraMetrics?.aggregates?.leadTimeHours
                                    val leadText = if (leadVal !=
                                        null
                                    ) {
                                        "$leadVal hrs"
                                    } else {
                                        "14.2 hrs"
                                    }
                                    Text(
                                        text = leadText,
                                        style = MaterialTheme.typography.headlineSmall,
                                        color = TextLight,
                                        fontWeight = FontWeight.Bold
                                    )
                                    Box(
                                        modifier = Modifier
                                            .background(
                                                PrimaryContainer,
                                                shape = RoundedCornerShape(6.dp)
                                            )
                                            .padding(horizontal = 6.dp, vertical = 2.dp)
                                    ) {
                                        Text(
                                            text = "Optimal Pace",
                                            style = MaterialTheme.typography.labelMedium,
                                            color = Primary,
                                            fontSize = 10.sp
                                        )
                                    }
                                    Spacer(modifier = Modifier.height(6.dp))
                                    CartesianChartHost(
                                        chart = rememberCartesianChart(
                                            rememberLineCartesianLayer()
                                        ),
                                        modelProducer = lineProducer,
                                        modifier = Modifier.fillMaxWidth().height(40.dp)
                                    )
                                }
                            }
                        }

                        Spacer(modifier = Modifier.height(8.dp))

                        Row(modifier = Modifier.fillMaxWidth()) {
                            // Card 3: Change Failure
                            Card(
                                modifier = Modifier.weight(1f).height(130.dp),
                                shape = RoundedCornerShape(16.dp),
                                colors = CardDefaults.cardColors(containerColor = DarkSurface)
                            ) {
                                Column(modifier = Modifier.padding(12.dp)) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Text(
                                            text = "Change Failure",
                                            style = MaterialTheme.typography.labelMedium,
                                            color = GrayText,
                                            fontSize = 10.sp
                                        )
                                        Spacer(modifier = Modifier.weight(1f))
                                        Text(
                                            text = "-0.8%",
                                            style = MaterialTheme.typography.labelMedium,
                                            color = Primary,
                                            fontWeight = FontWeight.Bold,
                                            fontSize = 11.sp
                                        )
                                    }
                                    val failVal = doraMetrics?.aggregates?.changeFailureRatePct
                                    val failText = if (failVal != null) "$failVal %" else "2.1 %"
                                    Text(
                                        text = failText,
                                        style = MaterialTheme.typography.headlineSmall,
                                        color = TextLight,
                                        fontWeight = FontWeight.Bold
                                    )
                                    Box(
                                        modifier = Modifier
                                            .background(
                                                PrimaryContainer,
                                                shape = RoundedCornerShape(6.dp)
                                            )
                                            .padding(horizontal = 6.dp, vertical = 2.dp)
                                    ) {
                                        Text(
                                            text = "Low Risk (<5%)",
                                            style = MaterialTheme.typography.labelMedium,
                                            color = Primary,
                                            fontSize = 10.sp
                                        )
                                    }
                                    Spacer(modifier = Modifier.height(8.dp))
                                    LinearProgressIndicator(
                                        progress = { 0.21f },
                                        modifier = Modifier.fillMaxWidth().height(
                                            4.dp
                                        ).clip(RoundedCornerShape(2.dp)),
                                        color = Primary,
                                        trackColor = BorderDivider
                                    )
                                }
                            }

                            Spacer(modifier = Modifier.width(8.dp))

                            // Card 4: MTTR Recovery
                            Card(
                                modifier = Modifier.weight(1f).height(130.dp),
                                shape = RoundedCornerShape(16.dp),
                                colors = CardDefaults.cardColors(containerColor = DarkSurface)
                            ) {
                                Column(modifier = Modifier.padding(12.dp)) {
                                    Text(
                                        text = "MTTR Recovery",
                                        style = MaterialTheme.typography.labelMedium,
                                        color = GrayText,
                                        fontSize = 10.sp
                                    )
                                    val mttrVal = doraMetrics?.aggregates?.mttrHours
                                    val mttrText = if (mttrVal != null) "$mttrVal hrs" else "32 min"
                                    Text(
                                        text = mttrText,
                                        style = MaterialTheme.typography.headlineSmall,
                                        color = TextLight,
                                        fontWeight = FontWeight.Bold
                                    )
                                    Box(
                                        modifier = Modifier
                                            .background(
                                                SecondaryContainer,
                                                shape = RoundedCornerShape(6.dp)
                                            )
                                            .padding(horizontal = 6.dp, vertical = 2.dp)
                                    ) {
                                        Text(
                                            text = "Elite Target",
                                            style = MaterialTheme.typography.labelMedium,
                                            color = SecondaryLight,
                                            fontSize = 10.sp
                                        )
                                    }
                                    Spacer(modifier = Modifier.height(6.dp))
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Icon(
                                            imageVector = Icons.Default.CheckCircle,
                                            contentDescription = null,
                                            modifier = Modifier.size(12.dp),
                                            tint = CyanText
                                        )
                                        Spacer(modifier = Modifier.width(4.dp))
                                        Text(
                                            text = "99.98% Running",
                                            style = MaterialTheme.typography.labelMedium,
                                            color = CyanText,
                                            fontSize = 10.sp
                                        )
                                    }
                                }
                            }
                        }
                    }

                    Spacer(modifier = Modifier.height(20.dp))

                    Text(
                        text = "Live Time Log",
                        style = MaterialTheme.typography.titleLarge,
                        color = TextLight,
                        fontWeight = FontWeight.Bold
                    )

                    Spacer(modifier = Modifier.height(12.dp))

                    TimeLogCard(
                        status = DeploymentStatus.Success,
                        environment = "PRODUCTION",
                        commitHash = "a1b2c3d",
                        serviceName = "auth-service",
                        version = "v2.4.2",
                        timeAgo = "14m ago",
                        duration = "2m 14s",
                        authorName = "Alex Vance",
                        branchName = "main"
                    )

                    TimeLogCard(
                        status = DeploymentStatus.FailedRollback,
                        environment = "PRODUCTION",
                        commitHash = "e8f901b",
                        serviceName = "payment-gateway",
                        version = "v1.19.0",
                        timeAgo = "1h 25m ago",
                        duration = "4m 02s",
                        authorName = "Sophie Chen",
                        branchName = "release/v1.19",
                        errorMessage = "Database migration failure on table payments_v2. " +
                            "Auto-rollback triggered."
                    )

                    TimeLogCard(
                        status = DeploymentStatus.InProgress,
                        environment = "STAGING",
                        commitHash = "7c4d12a",
                        serviceName = "telemetry-stream",
                        version = "v3.0.1-rc2",
                        timeAgo = "1m 45s",
                        duration = "Elapsed: 1m 45s",
                        authorName = "Marcus Reed",
                        branchName = "feature/kafka-buffer",
                        inProgressStage = "unit-test-suite",
                        inProgressPercent = 68
                    )

                    TimeLogCard(
                        status = DeploymentStatus.Success,
                        environment = "PRODUCTION",
                        commitHash = "3e5a88f",
                        serviceName = "frontend-spa",
                        version = "v4.12.0",
                        timeAgo = "4h ago",
                        duration = "3m 10s",
                        authorName = "Olena Ros",
                        branchName = "main"
                    )

                    Spacer(modifier = Modifier.height(16.dp))

                    PaginationFooter(
                        currentPage = currentPage,
                        totalPages = 3,
                        showingRangeText = "Showing 1-8 of 24 deployments",
                        onPageSelected = { currentPage = it }
                    )
                }

                ProjectScreenState.EmptyState -> {
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
                                    .background(Color(0xFF1E2838)),
                                contentAlignment = Alignment.Center
                            ) {
                                Icon(
                                    imageVector = Icons.Default.RocketLaunch,
                                    contentDescription = "No Deployments",
                                    tint = CyanText,
                                    modifier = Modifier.size(36.dp)
                                )
                            }

                            Spacer(modifier = Modifier.height(16.dp))

                            Text(
                                text = "No deployment events recorded",
                                style = MaterialTheme.typography.headlineMedium,
                                color = TextLight,
                                fontWeight = FontWeight.Bold,
                                textAlign = TextAlign.Center
                            )

                            Spacer(modifier = Modifier.height(8.dp))

                            Text(
                                text = "There are no active or recent continuous delivery " +
                                    "pipelines recorded for this environment in the past 30 days.",
                                style = MaterialTheme.typography.bodyMedium,
                                color = GrayText,
                                textAlign = TextAlign.Center
                            )

                            Spacer(modifier = Modifier.height(24.dp))

                            OutlinedButton(
                                onClick = { },
                                shape = RoundedCornerShape(12.dp)
                            ) {
                                Icon(
                                    imageVector = Icons.Default.Settings,
                                    contentDescription = null,
                                    tint = TextLight,
                                    modifier = Modifier.size(18.dp)
                                )
                                Spacer(modifier = Modifier.width(6.dp))
                                Text("View Webhook Settings", color = TextLight)
                            }
                        }
                    }
                }

                ProjectScreenState.ErrorState -> {
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
                                    .background(DangerContainer),
                                contentAlignment = Alignment.Center
                            ) {
                                Icon(
                                    imageVector = Icons.Default.Warning,
                                    contentDescription = "Error",
                                    tint = Danger,
                                    modifier = Modifier.size(36.dp)
                                )
                            }

                            Spacer(modifier = Modifier.height(16.dp))

                            Text(
                                text = "Failed to fetch deployment logs",
                                style = MaterialTheme.typography.headlineMedium,
                                color = TextLight,
                                fontWeight = FontWeight.Bold,
                                textAlign = TextAlign.Center
                            )

                            Spacer(modifier = Modifier.height(8.dp))

                            Text(
                                text = "Network timeout or API error occurred while querying " +
                                    "GitHub Actions and ArgoCD deployment telemetry streams.",
                                style = MaterialTheme.typography.bodyMedium,
                                color = GrayText,
                                textAlign = TextAlign.Center
                            )

                            Spacer(modifier = Modifier.height(12.dp))

                            Box(
                                modifier = Modifier
                                    .background(
                                        DangerContainer,
                                        shape = RoundedCornerShape(8.dp)
                                    )
                                    .padding(horizontal = 10.dp, vertical = 4.dp)
                            ) {
                                Text(
                                    text = "● ERR_CD_PIPELINE_TIMEOUT (504)",
                                    style = MaterialTheme.typography.labelMedium,
                                    color = Danger,
                                    fontWeight = FontWeight.Bold,
                                    fontSize = 11.sp
                                )
                            }

                            Spacer(modifier = Modifier.height(24.dp))

                            PrimaryButton(
                                text = "Refresh Deployment Logs",
                                onClick = { screenState = ProjectScreenState.LiveTelemetry },
                                icon = Icons.Default.Refresh
                            )

                            Spacer(modifier = Modifier.height(12.dp))

                            Text(
                                text = "🕒 Last sync: 1m ago",
                                style = MaterialTheme.typography.bodySmall,
                                color = GrayText
                            )
                        }
                    }
                }

                ProjectScreenState.LoadingState -> {
                    Column(modifier = Modifier.fillMaxWidth()) {
                        repeat(4) {
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(100.dp)
                                    .padding(vertical = 4.dp)
                                    .clip(RoundedCornerShape(12.dp))
                                    .background(DarkSurface.copy(alpha = 0.5f))
                            )
                        }
                    }
                }
            }
        }
    }
}
