package com.manta.mobile.features.settings

import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.manta.mobile.core.network.UserProfileResponse
import com.manta.mobile.core.theme.ui.AppBackground
import com.manta.mobile.core.theme.ui.GrayText
import com.manta.mobile.core.theme.ui.TextLight
import com.manta.mobile.data.auth.AuthState
import com.manta.mobile.data.auth.UserSession
import com.manta.mobile.features.auth.AuthRepository
import com.manta.mobile.ui.buttons.OutlinedDangerButton
import com.manta.mobile.ui.cards.DetailedUserProfileCard
import kotlinx.coroutines.launch

@Preview
@Composable
fun SettingsScreen(authRepository: AuthRepository? = null, fcmToken: String? = null) {
    val context = LocalContext.current
    val coroutineScope = rememberCoroutineScope()
    var isSigningOut by remember { mutableStateOf(false) }
    var fetchedProfile by remember { mutableStateOf<UserProfileResponse?>(null) }

    val authState by (
        authRepository?.authState?.collectAsState()
            ?: remember { mutableStateOf(AuthState.Unauthenticated) }
        )

    LaunchedEffect(authRepository) {
        if (authRepository != null) {
            val result = authRepository.fetchCurrentUser()
            if (result.isSuccess) {
                fetchedProfile = result.getOrNull()
            }
        }
    }

    val baseSession = when (val state = authState) {
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

    val currentSession = if (fetchedProfile != null) {
        val profile = fetchedProfile!!
        baseSession.copy(
            name = profile.name.ifBlank { profile.preferredUsername.ifBlank { baseSession.name } },
            email = profile.email.ifBlank { baseSession.email },
            roles = profile.roles.ifEmpty { baseSession.roles },
            tenantId = profile.tenantId.ifBlank { baseSession.tenantId }
        )
    } else {
        baseSession
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(AppBackground)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 24.dp)
    ) {
        Text(
            text = "Settings",
            style = MaterialTheme.typography.headlineLarge,
            color = TextLight,
            fontWeight = FontWeight.Bold
        )
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            text = "Manage session access and platform preferences.",
            style = MaterialTheme.typography.bodyMedium,
            color = GrayText
        )
        Spacer(modifier = Modifier.height(24.dp))

        DetailedUserProfileCard(session = currentSession)
        Spacer(modifier = Modifier.weight(1f, fill = false))
        Spacer(modifier = Modifier.height(32.dp))

        OutlinedDangerButton(
            text = "Sign Out",
            isLoading = isSigningOut,
            onClick = {
                if (authRepository != null) {
                    isSigningOut = true
                    coroutineScope.launch {
                        val result = authRepository.signOut(fcmToken)
                        isSigningOut = false
                        if (result.isFailure) {
                            val errorMsg = result.exceptionOrNull()?.message
                                ?: "Sign-out failed. Please check network connection."
                            Toast.makeText(context, errorMsg, Toast.LENGTH_LONG).show()
                        } else {
                            Toast.makeText(
                                context,
                                "Successfully signed out. Session data cleared.",
                                Toast.LENGTH_SHORT
                            ).show()
                        }
                    }
                } else {
                    Toast.makeText(
                        context,
                        "Successfully signed out. Session data cleared.",
                        Toast.LENGTH_SHORT
                    ).show()
                }
            }
        )
        Spacer(modifier = Modifier.height(16.dp))
    }
}
