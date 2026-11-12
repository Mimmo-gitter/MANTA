package com.manta.mobile.features.auth

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manta.mobile.R
import com.manta.mobile.core.theme.ui.Alert
import com.manta.mobile.core.theme.ui.AppBackground
import com.manta.mobile.core.theme.ui.Danger
import com.manta.mobile.core.theme.ui.DarkSurface
import com.manta.mobile.core.theme.ui.GrayText
import com.manta.mobile.core.theme.ui.Primary
import com.manta.mobile.core.theme.ui.PrimaryTransparent
import com.manta.mobile.core.theme.ui.SecondaryLight
import com.manta.mobile.core.theme.ui.TextLight
import com.manta.mobile.data.auth.BiometricUnlockSubState
import com.manta.mobile.ui.Logo
import com.manta.mobile.ui.PrimaryButton
import com.manta.mobile.ui.ProtectedBadge
import com.manta.mobile.ui.UserProfileCard

@Preview
@Composable
fun BiometricUnlockScreen(
    userName: String? = null,
    userEmail: String? = null,
    primaryRole: String? = null,
    subState: BiometricUnlockSubState = BiometricUnlockSubState.Ready,
    onRetryClick: () -> Unit = {},
    onSwitchClick: () -> Unit = {},
    onSsoClick: () -> Unit = {}
) {
    val name = userName?.ifBlank { null } ?: "NA"
    val email = userEmail?.ifBlank { null } ?: "manta-labs.io"
    val role = primaryRole?.ifBlank { null } ?: "Lead"

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(AppBackground)
            .padding(horizontal = 24.dp, vertical = 16.dp),
        contentAlignment = Alignment.Center
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState()),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            Logo()
            Spacer(modifier = Modifier.height(16.dp))
            when (subState) {
                BiometricUnlockSubState.Ready, BiometricUnlockSubState.Verifying -> {
                    Text(
                        text = buildAnnotatedString {
                            append("Verify ")
                            withStyle(
                                style = SpanStyle(
                                    fontWeight = FontWeight.ExtraBold,
                                    color = TextLight
                                )
                            ) {
                                append("Identity")
                            }
                        },
                        style = MaterialTheme.typography.displayLarge,
                        color = TextLight,
                        textAlign = TextAlign.Center
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = "Confirm your biometric key to unlock M.A.N.T.A. as $name.",
                        style = MaterialTheme.typography.bodySmall,
                        color = TextLight,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.padding(horizontal = 16.dp)
                    )
                }
                BiometricUnlockSubState.Success -> {
                    Text(
                        text = buildAnnotatedString {
                            append("Identity ")
                            withStyle(
                                style = SpanStyle(
                                    color = Primary,
                                    fontWeight = FontWeight.ExtraBold
                                )
                            ) {
                                append("Verified")
                            }
                        },
                        style = MaterialTheme.typography.displayLarge,
                        color = TextLight,
                        textAlign = TextAlign.Center
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    val firstName = name.split(" ").firstOrNull() ?: name
                    Text(
                        text = "Welcome back, $firstName. Unlocking M.A.N.T.A. workspace...",
                        style = MaterialTheme.typography.bodyMedium,
                        color = TextLight,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.padding(horizontal = 16.dp)
                    )
                }
                is BiometricUnlockSubState.Failed -> {
                    val attemptNum = 4 - subState.attemptsLeft
                    Text(
                        text = buildAnnotatedString {
                            append("Verification ")
                            withStyle(
                                style = SpanStyle(color = Danger, fontWeight = FontWeight.ExtraBold)
                            ) {
                                append("Failed")
                            }
                        },
                        style = MaterialTheme.typography.displayLarge,
                        color = TextLight,
                        textAlign = TextAlign.Center
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = "Biometric sensor did not recognize your fingerprint or face.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = TextLight,
                        textAlign = TextAlign.Center
                    )
                    Spacer(modifier = Modifier.height(6.dp))
                    Box(
                        modifier = Modifier
                            .background(Color(0x33FF383C), shape = RoundedCornerShape(12.dp))
                            .padding(horizontal = 12.dp, vertical = 4.dp)
                    ) {
                        Text(
                            text = "Attempt $attemptNum of 3",
                            style = MaterialTheme.typography.labelMedium,
                            color = Danger,
                            fontWeight = FontWeight.Bold
                        )
                    }
                }
                BiometricUnlockSubState.Unavailable -> {
                    Text(
                        text = buildAnnotatedString {
                            append("Biometrics ")
                            withStyle(
                                style = SpanStyle(color = Alert, fontWeight = FontWeight.ExtraBold)
                            ) {
                                append("Unavailable")
                            }
                        },
                        style = MaterialTheme.typography.displayLarge,
                        color = TextLight,
                        textAlign = TextAlign.Center
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = "Hardware sensor is disabled, locked after too many attempts, " +
                            "or not available on this device.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = TextLight,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.padding(horizontal = 16.dp)
                    )
                }
            }

            Spacer(modifier = Modifier.height(20.dp))
            UserProfileCard(
                name = name,
                email = email,
                showSwitchButton = subState != BiometricUnlockSubState.Success,
                isSuccessState = subState == BiometricUnlockSubState.Success,
                isErrorState = subState is BiometricUnlockSubState.Failed,
                onSwitchClick = onSwitchClick,
                role = role
            )

            Spacer(modifier = Modifier.height(32.dp))
            val graphicRes = when (subState) {
                BiometricUnlockSubState.Ready,
                BiometricUnlockSubState.Verifying -> R.drawable.fingerprint_default
                BiometricUnlockSubState.Success -> R.drawable.fingerprint_verified
                is BiometricUnlockSubState.Failed -> R.drawable.fingerprint_error
                BiometricUnlockSubState.Unavailable -> R.drawable.fingerprint_unavailable
            }
            Image(
                painter = painterResource(id = graphicRes),
                contentDescription = "Fingerprint Sensor",
                modifier = Modifier.size(208.dp)
            )

            Spacer(modifier = Modifier.height(20.dp))
            when (subState) {
                BiometricUnlockSubState.Ready, BiometricUnlockSubState.Verifying -> {
                    StatusChip(
                        text = if (subState ==
                            BiometricUnlockSubState.Verifying
                        ) {
                            "● Sensor is Checking"
                        } else {
                            "● Sensor is Ready"
                        },
                        backgroundColor = PrimaryTransparent,
                        textColor = Primary
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    Text(
                        text = "Tap sensor or glance at camera for Face ID",
                        style = MaterialTheme.typography.bodySmall,
                        color = GrayText
                    )
                }
                BiometricUnlockSubState.Success -> {
                    StatusChip(
                        text = "● Successfully Verified",
                        backgroundColor = Color(0xFF1B2E28),
                        textColor = Primary
                    )
                    Spacer(modifier = Modifier.height(24.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.End
                    ) {
                        Text(
                            text = "Redirecting...",
                            style = MaterialTheme.typography.bodySmall,
                            textAlign = TextAlign.End,
                            color = SecondaryLight
                        )
                    }
                    Spacer(modifier = Modifier.height(4.dp))

                    var progressTarget by remember { mutableFloatStateOf(0f) }
                    val animatedProgress by animateFloatAsState(
                        targetValue = progressTarget,
                        animationSpec = tween(durationMillis = 1000),
                        label = "RedirectProgress"
                    )
                    LaunchedEffect(Unit) {
                        progressTarget = 1f
                    }

                    LinearProgressIndicator(
                        progress = { animatedProgress },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(6.dp),
                        color = Primary,
                        trackColor = DarkSurface
                    )
                }
                is BiometricUnlockSubState.Failed -> {
                    StatusChip(
                        text = "● Not Recognized • Try Again",
                        backgroundColor = Color(0x33FF383C),
                        textColor = Danger
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    Text(
                        text = "Clean sensor surface or position finger squarely on the reader. " +
                            "Keep still during the scan cycle.",
                        style = MaterialTheme.typography.bodySmall,
                        color = TextLight,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.padding(horizontal = 16.dp)
                    )
                    Spacer(modifier = Modifier.height(20.dp))
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(DarkSurface, shape = RoundedCornerShape(12.dp))
                            .padding(vertical = 12.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text(
                                text = "LOCKOUT IN",
                                style = MaterialTheme.typography.labelMedium,
                                color = GrayText,
                                fontSize = 10.sp
                            )
                            val attemptsText = "${subState.attemptsLeft} Attempt" +
                                if (subState.attemptsLeft > 1) "s" else ""
                            Text(
                                text = attemptsText,
                                style = MaterialTheme.typography.titleMedium,
                                color = Danger,
                                fontWeight = FontWeight.Bold
                            )
                        }
                    }

                    Spacer(modifier = Modifier.height(20.dp))
                    PrimaryButton(
                        text = "Retry Biometrics",
                        onClick = onRetryClick,
                        icon = Icons.Default.Refresh
                    )
                }
                BiometricUnlockSubState.Unavailable -> {
                    StatusChip(
                        text = "● SENSOR UNAVAILABLE",
                        backgroundColor = DarkSurface,
                        textColor = Alert
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    Text(
                        text = "Biometric is disabled. Re-verify identity using Organization SSO.",
                        style = MaterialTheme.typography.bodySmall,
                        color = GrayText,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.padding(horizontal = 16.dp)
                    )
                    Spacer(modifier = Modifier.height(24.dp))
                    PrimaryButton(
                        text = "SIGN IN WITH ORGANIZATION SSO",
                        onClick = onSsoClick
                    )
                }
            }
            Spacer(modifier = Modifier.height(32.dp))
            ProtectedBadge()
            Spacer(modifier = Modifier.height(32.dp))
        }
    }
}

@Composable
private fun StatusChip(text: String, backgroundColor: Color, textColor: Color) {
    Box(
        modifier = Modifier
            .background(backgroundColor, shape = CircleShape)
            .padding(horizontal = 16.dp, vertical = 6.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.labelMedium,
            color = textColor,
            fontWeight = FontWeight.Bold
        )
    }
}
