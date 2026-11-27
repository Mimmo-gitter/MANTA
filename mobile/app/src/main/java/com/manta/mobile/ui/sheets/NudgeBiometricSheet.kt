package com.manta.mobile.ui.sheets

import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.SheetState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NudgeBiometricSheet(
    onDismissRequest: () -> Unit,
    onAuthorizeClick: () -> Unit,
    modifier: Modifier = Modifier,
    sheetState: SheetState = rememberModalBottomSheetState(),
    prId: String = "#1402",
    prTitle: String = "PR: Auth JWT refresh token rotation",
    authorHandle: String = "@alex_k",
    reviewerHandle: String = "@thijn_lead (Idle)"
) {
    BiometricActionSheet(
        onDismissRequest = onDismissRequest,
        onAuthorizeClick = onAuthorizeClick,
        modifier = modifier,
        sheetState = sheetState,
        prId = prId,
        badgeTitle = "NUDGE REVIEWER",
        slaBadge = "54h Stuck (>48h SLA)",
        prTitle = prTitle,
        authorHandle = authorHandle,
        reviewerHandle = reviewerHandle,
        impactText = "Target: Direct Slack DM.",
        reviewers = null,
        buttonText = "Authorize & Dispatch Slack Nudge"
    )
}
