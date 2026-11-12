package com.manta.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Error
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manta.mobile.core.theme.ui.Danger
import com.manta.mobile.core.theme.ui.DarkSurface
import com.manta.mobile.core.theme.ui.GrayText
import com.manta.mobile.core.theme.ui.Primary
import com.manta.mobile.core.theme.ui.SecondaryLight
import com.manta.mobile.core.theme.ui.SecondaryTransparent
import com.manta.mobile.core.theme.ui.TextLight

@Composable
fun UserProfileCard(
    name: String,
    email: String,
    modifier: Modifier = Modifier,
    role: String? = null,
    showSwitchButton: Boolean = true,
    isSuccessState: Boolean = false,
    isErrorState: Boolean = false,
    onSwitchClick: () -> Unit = {}
) {
    val initials = getInitials(name)
    val displayRole = formatRole(role)
    val displayEmail = if (email.contains("@")) email else "Staff Engineering Manager • $email"

    Card(
        modifier = modifier,
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = DarkSurface)
    ) {
        Row(
            modifier = Modifier
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                modifier = Modifier.size(48.dp),
                contentAlignment = Alignment.Center
            ) {
                Box(
                    modifier = Modifier
                        .size(44.dp)
                        .background(color = SecondaryTransparent, shape = CircleShape),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        text = initials,
                        style = MaterialTheme.typography.titleMedium,
                        color = TextLight,
                        fontWeight = FontWeight.Bold
                    )
                }

                val dotColor = when {
                    isErrorState -> Danger
                    isSuccessState -> Primary
                    else -> SecondaryLight
                }
                Box(
                    modifier = Modifier
                        .size(12.dp)
                        .background(dotColor, shape = CircleShape)
                        .align(Alignment.BottomEnd)
                )
            }
            Spacer(modifier = Modifier.width(16.dp))
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = name.ifBlank { "Alex Mercer" },
                        style = MaterialTheme.typography.titleMedium,
                        color = TextLight,
                        fontWeight = FontWeight.Bold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false)
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Box(
                        modifier = Modifier
                            .background(Color(0xFF2D2447), shape = RoundedCornerShape(6.dp))
                            .padding(horizontal = 8.dp, vertical = 2.dp)
                    ) {
                        Text(
                            text = displayRole,
                            style = MaterialTheme.typography.labelMedium,
                            color = SecondaryLight,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.SemiBold,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }
                Text(
                    text = displayEmail.ifBlank { "manta-labs.io" },
                    style = MaterialTheme.typography.bodySmall,
                    color = GrayText,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
            }
            Spacer(modifier = Modifier.width(8.dp))
            when {
                isSuccessState -> {
                    Icon(
                        imageVector = Icons.Default.CheckCircle,
                        contentDescription = "Verified",
                        tint = Primary,
                        modifier = Modifier.size(20.dp)
                    )
                }
                isErrorState -> {
                    Icon(
                        imageVector = Icons.Default.Error,
                        contentDescription = "Error",
                        tint = Danger,
                        modifier = Modifier.size(20.dp)
                    )
                }
                showSwitchButton -> {
                    Text(
                        text = "Switch",
                        style = MaterialTheme.typography.labelLarge,
                        color = SecondaryLight,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.clickable { onSwitchClick() }
                    )
                }
            }
        }
    }
}

private fun formatRole(role: String?): String {
    if (role.isNullOrBlank()) return "Lead"
    val clean = role.replace("default-roles-", "")
        .replace("offline_access", "")
        .replace("uma_authorization", "")
        .trim()
    return if (clean.isBlank()) {
        "Lead"
    } else {
        clean.split(" ").joinToString(" ") { it.replaceFirstChar { c -> c.uppercase() } }
    }
}

private fun getInitials(name: String): String {
    if (name.isBlank()) return "N.A."
    val parts = name.trim().split(" ")
    if (parts.size >= 2) {
        val first = parts[0].firstOrNull()?.uppercase() ?: ""
        val second = parts[1].firstOrNull()?.uppercase() ?: ""
        return "$first$second"
    }
    return name.take(2).uppercase()
}
