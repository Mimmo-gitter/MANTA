package com.manta.mobile.ui.sheets

import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.SheetState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ReassignBiometricSheet(
    onDismissRequest: () -> Unit,
    onAuthorizeClick: () -> Unit,
    modifier: Modifier = Modifier,
    sheetState: SheetState = rememberModalBottomSheetState(),
    prId: String = "#1402",
    prTitle: String = "PR: Auth JWT refresh token rotation",
    authorHandle: String = "@alex_k",
    reviewerHandle: String = "@thijn_lead",
    reviewers: List<ReviewerOption> = listOf(
        ReviewerOption("@marcus_lead", "Active • 2 PRs in flight"),
        ReviewerOption("@olena_dev", "Idle • 0 PRs in flight"),
        ReviewerOption("@dev_kenji", "1 PR in flight • 98% Review SLA"),
        ReviewerOption("@sarah_infra", "3 PRs in flight")
    )
) {
    BiometricActionSheet(
        onDismissRequest = onDismissRequest,
        onAuthorizeClick = onAuthorizeClick,
        modifier = modifier,
        sheetState = sheetState,
        prId = prId,
        badgeTitle = "MANAGER INTERVENTION",
        slaBadge = "54h Stuck (>48h SLA)",
        prTitle = prTitle,
        authorHandle = authorHandle,
        reviewerHandle = reviewerHandle,
        impactText = "Assign @marcus_lead as co-reviewer and dispatch Slack notification.",
        reviewers = reviewers,
        buttonText = "Authorize & Dispatch Review"
    )
}
