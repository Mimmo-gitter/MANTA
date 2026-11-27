package com.manta.mobile.features.actions

import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.ExperimentalMaterial3Api
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manta.mobile.core.security.BiometricPromptManager
import com.manta.mobile.core.theme.ui.AppBackground
import com.manta.mobile.core.theme.ui.Danger
import com.manta.mobile.core.theme.ui.DangerContainer
import com.manta.mobile.core.theme.ui.GrayText
import com.manta.mobile.core.theme.ui.TextLight
import com.manta.mobile.features.auth.AuthRepository
import com.manta.mobile.ui.FilterPillItem
import com.manta.mobile.ui.FilterPillRow
import com.manta.mobile.ui.PaginationFooter
import com.manta.mobile.ui.cards.ActionCard
import com.manta.mobile.ui.cards.ActionSeverity
import com.manta.mobile.ui.sheets.NudgeBiometricSheet
import com.manta.mobile.ui.sheets.ReassignBiometricSheet
import com.manta.mobile.ui.sheets.ReviewerOption

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ActionsScreen(
    modifier: Modifier = Modifier,
    authRepository: AuthRepository? = null,
    biometricPromptManager: BiometricPromptManager? = null
) {
    val context = LocalContext.current
    var selectedFilterId by remember { mutableStateOf("all") }
    var currentPage by remember { mutableIntStateOf(1) }

    var activeSheetMode by remember { mutableStateOf<String?>(null) } // "nudge" or "reassign"
    var selectedPrId by remember { mutableStateOf("#1402") }

    val filterPills = listOf(
        FilterPillItem("all", "All stale PRs", 5),
        FilterPillItem("blocked", "Blocked (>48h)", 2)
    )

    val reviewersList = listOf(
        ReviewerOption("@marcus_lead", "Active • 2 PRs in flight"),
        ReviewerOption("@olena_dev", "Idle • 0 PRs in flight"),
        ReviewerOption("@dev_kenji", "1 PR in flight • 98% Review SLA"),
        ReviewerOption("@sarah_infra", "3 PRs in flight")
    )

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(AppBackground)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp)
            .testTag("screen_actions")
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = "Stale PRs",
                style = MaterialTheme.typography.headlineLarge,
                color = TextLight,
                fontWeight = FontWeight.Bold
            )
            Spacer(modifier = Modifier.width(8.dp))

            Box(
                modifier = Modifier
                    .background(DangerContainer, shape = RoundedCornerShape(12.dp))
                    .padding(horizontal = 10.dp, vertical = 4.dp)
            ) {
                Text(
                    text = "● 5 Actionable • 2 Blocked",
                    style = MaterialTheme.typography.labelMedium,
                    color = Danger,
                    fontWeight = FontWeight.Bold,
                    fontSize = 11.sp
                )
            }

            Spacer(modifier = Modifier.weight(1f))

            IconButton(onClick = { }) {
                Icon(
                    imageVector = Icons.Default.Refresh,
                    contentDescription = "Refresh PRs",
                    tint = TextLight
                )
            }
        }

        Spacer(modifier = Modifier.height(4.dp))

        Text(
            text = "Real-time stale pull requests radar. " +
                "Resolve critical issues to recover delivery lead times.",
            style = MaterialTheme.typography.bodyMedium,
            color = GrayText
        )

        Spacer(modifier = Modifier.height(16.dp))

        FilterPillRow(
            items = filterPills,
            selectedId = selectedFilterId,
            onItemSelected = { selectedFilterId = it }
        )

        Spacer(modifier = Modifier.height(16.dp))

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
            onNudgeClick = {
                selectedPrId = "#1402"
                activeSheetMode = "nudge"
            },
            onReassignClick = {
                selectedPrId = "#1402"
                activeSheetMode = "reassign"
            }
        )

        Spacer(modifier = Modifier.height(12.dp))

        ActionCard(
            idNumber = "#1389",
            title = "Kafka consumer rebalance optimization",
            severity = ActionSeverity.Danger,
            badgeText = "🏆 49h inactive (High severity)",
            serviceTag = "event-stream",
            sourcePlatform = "GitHub",
            timeAgo = "3h ago",
            authorHandle = "@sophie_dev",
            pingPongCycles = 4,
            reviewerHandle = "@marcus_lead",
            descriptionTitle = "UNADDRESSED ISSUES",
            descriptionBody = "4 unaddressed security issues pending review.",
            onNudgeClick = {
                selectedPrId = "#1389"
                activeSheetMode = "nudge"
            },
            onReassignClick = {
                selectedPrId = "#1389"
                activeSheetMode = "reassign"
            }
        )

        Spacer(modifier = Modifier.height(12.dp))

        ActionCard(
            idNumber = "#1411",
            title = "Billing webhook idempotency lock",
            severity = ActionSeverity.Alert,
            badgeText = "⏳ 36h delayed (Needs QA approval)",
            serviceTag = "payment-service",
            sourcePlatform = "GitHub",
            timeAgo = "5h ago",
            authorHandle = "@david_m",
            pingPongCycles = 3,
            reviewerHandle = "QA Lead",
            descriptionTitle = "FLAKY TESTS",
            descriptionBody = "Stripe Mock Gateway: Timeout exception at step 4",
            onNudgeClick = {
                selectedPrId = "#1411"
                activeSheetMode = "nudge"
            },
            onReassignClick = {
                selectedPrId = "#1411"
                activeSheetMode = "reassign"
            }
        )

        Spacer(modifier = Modifier.height(12.dp))

        ActionCard(
            idNumber = "#1408",
            title = "GraphQL batching query resolver",
            severity = ActionSeverity.Info,
            badgeText = "📅 28h waiting",
            serviceTag = "api-gateway",
            sourcePlatform = "GitHub",
            timeAgo = "1d ago",
            authorHandle = "@elena_r",
            pingPongCycles = 2,
            reviewerHandle = "Reviewer",
            descriptionTitle = "VACATION LEAVE",
            descriptionBody = "Reviewer on vacation leave • SLA timer warning",
            onNudgeClick = {
                selectedPrId = "#1408"
                activeSheetMode = "nudge"
            },
            onReassignClick = {
                selectedPrId = "#1408"
                activeSheetMode = "reassign"
            }
        )

        Spacer(modifier = Modifier.height(16.dp))

        PaginationFooter(
            currentPage = currentPage,
            totalPages = 3,
            showingRangeText = "Showing 1-4 of 11 PRs",
            onPageSelected = { currentPage = it }
        )
    }

    // Biometric Action Sheets using NudgeBiometricSheet & ReassignBiometricSheet
    val onAuthorizeAction = {
        if (biometricPromptManager != null) {
            val status = biometricPromptManager.canAuthenticate()
            if (status is BiometricPromptManager.BiometricStatus.Ready) {
                biometricPromptManager.showBiometricPrompt(
                    title = "M.A.N.T.A. Action Authorization",
                    description = "Confirm identity to dispatch operational action."
                )
            } else {
                Toast.makeText(
                    context,
                    "Action Authorized & Dispatched!",
                    Toast.LENGTH_SHORT
                ).show()
            }
        } else {
            Toast.makeText(
                context,
                "Action Authorized & Dispatched!",
                Toast.LENGTH_SHORT
            ).show()
        }
        activeSheetMode = null
    }

    if (activeSheetMode == "nudge") {
        NudgeBiometricSheet(
            prId = selectedPrId,
            onDismissRequest = { activeSheetMode = null },
            onAuthorizeClick = onAuthorizeAction
        )
    } else if (activeSheetMode == "reassign") {
        ReassignBiometricSheet(
            prId = selectedPrId,
            reviewers = reviewersList,
            onDismissRequest = { activeSheetMode = null },
            onAuthorizeClick = onAuthorizeAction
        )
    }
}
