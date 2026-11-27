package com.manta.mobile.ui.sheets

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
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
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.FlashOn
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.SheetState
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manta.mobile.R
import com.manta.mobile.core.theme.ui.AppBackground
import com.manta.mobile.core.theme.ui.BorderDivider
import com.manta.mobile.core.theme.ui.CircleSurface
import com.manta.mobile.core.theme.ui.CyanText
import com.manta.mobile.core.theme.ui.Danger
import com.manta.mobile.core.theme.ui.DangerContainer
import com.manta.mobile.core.theme.ui.DarkSurface
import com.manta.mobile.core.theme.ui.GrayText
import com.manta.mobile.core.theme.ui.InfoContainer
import com.manta.mobile.core.theme.ui.Primary
import com.manta.mobile.core.theme.ui.SecondaryLight
import com.manta.mobile.core.theme.ui.TextLight
import com.manta.mobile.ui.buttons.PrimaryButton

data class ReviewerOption(val handle: String, val status: String)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BiometricActionSheet(
    onDismissRequest: () -> Unit,
    onAuthorizeClick: () -> Unit,
    modifier: Modifier = Modifier,
    sheetState: SheetState = rememberModalBottomSheetState(),
    prId: String = "#1402",
    badgeTitle: String = "NUDGE REVIEWER",
    slaBadge: String = "54h Stuck (>48h SLA)",
    prTitle: String = "PR: Auth JWT refresh token rotation",
    authorHandle: String = "@alex_k",
    reviewerHandle: String = "@thijn_lead (Idle)",
    impactText: String = "Target: Direct Slack DM.",
    reviewers: List<ReviewerOption>? = null,
    buttonText: String = "Authorize & Dispatch Slack Nudge"
) {
    var selectedReviewer by remember { mutableStateOf(reviewers?.firstOrNull()?.handle) }

    ModalBottomSheet(
        onDismissRequest = onDismissRequest,
        sheetState = sheetState,
        containerColor = DarkSurface,
        scrimColor = Color.Black.copy(alpha = 0.6f)
    ) {
        Column(
            modifier = modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 12.dp)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = prId,
                    style = MaterialTheme.typography.titleMedium,
                    color = TextLight,
                    fontWeight = FontWeight.Bold
                )
                Spacer(modifier = Modifier.weight(1f))
                IconButton(onClick = onDismissRequest) {
                    Icon(
                        imageVector = Icons.Default.Close,
                        contentDescription = "Close",
                        tint = GrayText
                    )
                }
            }
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(AppBackground, shape = RoundedCornerShape(12.dp))
                    .padding(16.dp)
            ) {
                Column {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .background(
                                    InfoContainer,
                                    shape = RoundedCornerShape(6.dp)
                                )
                                .padding(horizontal = 8.dp, vertical = 4.dp)
                        ) {
                            Text(
                                text = badgeTitle,
                                style = MaterialTheme.typography.labelMedium,
                                color = CyanText,
                                fontWeight = FontWeight.Bold,
                                fontSize = 11.sp
                            )
                        }
                        Spacer(modifier = Modifier.width(8.dp))
                        Box(
                            modifier = Modifier
                                .background(
                                    DangerContainer,
                                    shape = RoundedCornerShape(6.dp)
                                )
                                .padding(horizontal = 8.dp, vertical = 4.dp)
                        ) {
                            Text(
                                text = slaBadge,
                                style = MaterialTheme.typography.labelMedium,
                                color = Danger,
                                fontWeight = FontWeight.Bold,
                                fontSize = 11.sp
                            )
                        }
                    }
                    Spacer(modifier = Modifier.height(10.dp))
                    Text(
                        text = prTitle,
                        style = MaterialTheme.typography.titleLarge,
                        color = TextLight,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = "Author: $authorHandle • Reviewer: $reviewerHandle",
                        style = MaterialTheme.typography.bodyMedium,
                        color = GrayText
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    HorizontalDivider(color = BorderDivider)
                    Spacer(modifier = Modifier.height(12.dp))
                    Text(
                        text = "ACTION & OPERATIONAL IMPACT",
                        style = MaterialTheme.typography.labelMedium,
                        color = GrayText,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(
                        text = impactText,
                        style = MaterialTheme.typography.bodyMedium,
                        color = TextLight
                    )
                }
            }
            if (reviewers != null) {
                Spacer(modifier = Modifier.height(16.dp))
                Text(
                    text = "ASSIGN NEW REVIEWER",
                    style = MaterialTheme.typography.labelMedium,
                    color = GrayText,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold
                )
                Spacer(modifier = Modifier.height(8.dp))
                Column {
                    reviewers.forEach { reviewer ->
                        val isSelected = reviewer.handle == selectedReviewer
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(vertical = 4.dp)
                                .clip(RoundedCornerShape(10.dp))
                                .background(
                                    if (isSelected) {
                                        InfoContainer
                                    } else {
                                        AppBackground
                                    }
                                )
                                .border(
                                    width = if (isSelected) 1.dp else 0.dp,
                                    color = if (isSelected) Primary else Color.Transparent,
                                    shape = RoundedCornerShape(10.dp)
                                )
                                .clickable { selectedReviewer = reviewer.handle }
                                .padding(12.dp)
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    text = reviewer.handle,
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = TextLight,
                                    fontWeight = FontWeight.Bold
                                )
                                Spacer(modifier = Modifier.width(8.dp))
                                Text(
                                    text = reviewer.status,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = SecondaryLight
                                )
                                Spacer(modifier = Modifier.weight(1f))
                                if (isSelected) {
                                    Icon(
                                        imageVector = Icons.Default.Check,
                                        contentDescription = "Selected",
                                        tint = Primary,
                                        modifier = Modifier.size(18.dp)
                                    )
                                }
                            }
                        }
                    }
                }
            }
            Spacer(modifier = Modifier.height(20.dp))
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(AppBackground, shape = RoundedCornerShape(16.dp))
                    .padding(20.dp),
                contentAlignment = Alignment.Center
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = "Biometric Approval Required",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextLight,
                        fontWeight = FontWeight.Bold,
                        textAlign = TextAlign.Center
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = "Tap sensor or glance at camera for Face ID.",
                        style = MaterialTheme.typography.bodySmall,
                        color = GrayText,
                        textAlign = TextAlign.Center
                    )
                    Spacer(modifier = Modifier.height(16.dp))
                    Box(
                        modifier = Modifier
                            .size(90.dp)
                            .clip(CircleShape)
                            .background(CircleSurface)
                            .border(2.dp, SecondaryLight.copy(alpha = 0.5f), CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Image(
                            painter = painterResource(id = R.drawable.fingerprint_default),
                            contentDescription = "Fingerprint Sensor",
                            modifier = Modifier.size(56.dp)
                        )
                    }
                    Spacer(modifier = Modifier.height(12.dp))
                    Box(
                        modifier = Modifier
                            .background(
                                BorderDivider,
                                shape = RoundedCornerShape(12.dp)
                            )
                            .padding(horizontal = 12.dp, vertical = 4.dp)
                    ) {
                        Text(
                            text = "● Sensor is Ready",
                            style = MaterialTheme.typography.labelMedium,
                            color = CyanText,
                            fontSize = 11.sp
                        )
                    }
                }
            }
            Spacer(modifier = Modifier.height(20.dp))
            PrimaryButton(
                text = buttonText,
                onClick = onAuthorizeClick,
                icon = Icons.Default.FlashOn
            )
            Spacer(modifier = Modifier.height(8.dp))
            OutlinedButton(
                onClick = onDismissRequest,
                modifier = Modifier.fillMaxWidth().height(48.dp),
                shape = RoundedCornerShape(12.dp),
                border = BorderStroke(1.dp, Danger),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = Danger)
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.Center,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Icon(
                        imageVector = Icons.Default.Close,
                        contentDescription = null,
                        tint = Danger,
                        modifier = Modifier.size(16.dp)
                    )
                    Spacer(modifier = Modifier.width(6.dp))
                    Text("Cancel Action", color = Danger, fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}
