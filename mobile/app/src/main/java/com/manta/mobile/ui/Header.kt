package com.manta.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manta.mobile.core.network.ProjectApiService
import com.manta.mobile.core.network.ProjectItemResponse
import com.manta.mobile.core.network.RetrofitClientFactory
import com.manta.mobile.core.theme.ui.AppBackground
import com.manta.mobile.core.theme.ui.DarkSurface
import com.manta.mobile.core.theme.ui.GrayText
import com.manta.mobile.core.theme.ui.Primary
import com.manta.mobile.core.theme.ui.Secondary
import com.manta.mobile.core.theme.ui.SecondaryLight
import com.manta.mobile.core.theme.ui.TextLight
import com.manta.mobile.data.auth.AuthState
import com.manta.mobile.data.auth.UserSession
import com.manta.mobile.features.auth.AuthRepository

@Composable
fun AppHeader(
    modifier: Modifier = Modifier,
    authRepository: AuthRepository? = null,
    selectedProjectName: String = "Core Platform Squad",
    onProjectSelected: (ProjectItemResponse) -> Unit = {},
    notificationCount: Int = 5
) {
    val authState by (
        authRepository?.authState?.collectAsState()
            ?: remember { mutableStateOf(AuthState.Unauthenticated) }
        )
    val currentSession = when (val state = authState) {
        is AuthState.Authenticated -> state.session
        is AuthState.LockedRequiresBiometric -> UserSession(
            userId = "",
            email = state.userEmail ?: "",
            name = state.userName ?: "",
            tenantId = "",
            roles = listOfNotNull(state.primaryRole),
            accessToken = ""
        )
        else -> UserSession.DEFAULT
    }
    var projects by remember {
        mutableStateOf(
            listOf(
                ProjectItemResponse(
                    id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    name = "Core Platform Squad",
                    slug = "core-platform-squad",
                    alertCount = 5
                ),
                ProjectItemResponse(
                    id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                    name = "Auth Service",
                    slug = "auth-service",
                    alertCount = 2
                ),
                ProjectItemResponse(
                    id = "cccccccc-cccc-cccc-cccc-cccccccccccc",
                    name = "Payment Gateway",
                    slug = "payment-gateway",
                    alertCount = 1
                )
            )
        )
    }
    var dropdownExpanded by remember { mutableStateOf(false) }

    LaunchedEffect(authRepository) {
        if (authRepository != null) {
            try {
                val service =
                    RetrofitClientFactory.createAuthenticatedService<ProjectApiService>(
                        authRepository
                    )
                val response = service.getProjects()
                if (response.isSuccessful && !response.body().isNullOrEmpty()) {
                    projects = response.body()!!
                    if (selectedProjectName == "Core Platform Squad") {
                        onProjectSelected(projects.first())
                    }
                }
            } catch (_: Exception) {}
        }
    }
    val initials = currentSession.displayName
        .split(" ")
        .mapNotNull { it.firstOrNull()?.uppercase() }
        .take(2)
        .joinToString("")
        .ifEmpty { "AM" }

    Row(
        modifier = modifier
            .fillMaxWidth()
            .background(AppBackground)
            .statusBarsPadding()
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Logo(modifier = Modifier.size(32.dp))
        Spacer(modifier = Modifier.width(12.dp))

        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = "M.A.N.T.A.",
                style = MaterialTheme.typography.titleMedium,
                color = TextLight,
                fontWeight = FontWeight.Bold
            )
            Box {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.clickable { dropdownExpanded = !dropdownExpanded }
                ) {
                    Text(
                        text = selectedProjectName,
                        style = MaterialTheme.typography.bodySmall,
                        color = GrayText,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                    Icon(
                        imageVector = Icons.Default.ArrowDropDown,
                        contentDescription = "Select Project",
                        tint = GrayText,
                        modifier = Modifier.size(18.dp)
                    )
                }

                DropdownMenu(
                    expanded = dropdownExpanded,
                    onDismissRequest = { dropdownExpanded = false },
                    modifier = Modifier.background(DarkSurface)
                ) {
                    projects.forEach { proj ->
                        DropdownMenuItem(
                            text = {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(
                                        text = proj.name,
                                        color = TextLight,
                                        fontWeight = FontWeight.SemiBold
                                    )
                                    Spacer(modifier = Modifier.weight(1f))
                                    Box(
                                        modifier = Modifier
                                            .background(
                                                Secondary,
                                                shape = RoundedCornerShape(10.dp)
                                            )
                                            .padding(horizontal = 8.dp, vertical = 2.dp)
                                    ) {
                                        Text(
                                            text = "${proj.alertCount} risks",
                                            style = MaterialTheme.typography.labelMedium,
                                            color = TextLight,
                                            fontSize = 10.sp
                                        )
                                    }
                                }
                            },
                            onClick = {
                                dropdownExpanded = false
                                onProjectSelected(proj)
                            }
                        )
                    }
                }
            }
        }
        Box(contentAlignment = Alignment.TopEnd) {
            IconButton(onClick = { }) {
                Icon(
                    imageVector = Icons.Default.Notifications,
                    contentDescription = "Notifications",
                    tint = TextLight,
                    modifier = Modifier.size(24.dp)
                )
            }
            if (notificationCount > 0) {
                Box(
                    modifier = Modifier
                        .size(18.dp)
                        .clip(CircleShape)
                        .background(Secondary),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        text = "$notificationCount",
                        style = MaterialTheme.typography.labelMedium,
                        color = TextLight,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Bold
                    )
                }
            }
        }
        Spacer(modifier = Modifier.width(8.dp))
        Box(modifier = Modifier.size(40.dp)) {
            Box(
                modifier = Modifier
                    .size(36.dp)
                    .clip(CircleShape)
                    .background(Color(0xFF2D2447)),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    text = initials,
                    style = MaterialTheme.typography.titleSmall,
                    color = SecondaryLight,
                    fontWeight = FontWeight.Bold
                )
            }
            Box(
                modifier = Modifier
                    .size(10.dp)
                    .clip(CircleShape)
                    .background(Primary)
                    .border(1.5.dp, AppBackground, CircleShape)
                    .align(Alignment.BottomEnd)
            )
        }
    }
}
