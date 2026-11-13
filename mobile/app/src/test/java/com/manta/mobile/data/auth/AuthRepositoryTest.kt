package com.manta.mobile.data.auth

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.google.crypto.tink.Aead
import com.google.crypto.tink.KeyTemplates
import com.google.crypto.tink.KeysetHandle
import com.google.crypto.tink.aead.AeadConfig
import com.manta.mobile.core.security.EncryptedStorage
import com.manta.mobile.features.auth.AuthRepository
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class AuthRepositoryTest {

    @get:Rule
    @JvmField
    val tempFolder = TemporaryFolder()

    private lateinit var encryptedStorage: EncryptedStorage
    private lateinit var authRepository: AuthRepository

    private val mockAccessToken = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9." +
        "eyJzdWIiOiJ1c2VyLXV1aWQtMTIzNCIsImVtYWlsIjoiZGV2ZWxvcGVyQG1hbnRhLmNvbSIs" +
        "Im5hbWUiOiJNYW50YSBEZXZlbG9wZXIiLCJ0ZW5hbnRfaWQiOiJ0ZW5hbnQtdXVpZC01Njc4Iiw" +
        "icmVhbG1fYWNjZXNzIjp7InJvbGVzIjpbIkRFVkVMT1BFUiJdfSwiYXVkIjoibWFudGEtYmFja2VuZCJ9." +
        "signature"

    @Before
    fun setUp() {
        tempFolder.create()
        AeadConfig.register()
        val aead: Aead = KeysetHandle.generateNew(KeyTemplates.get("AES256_GCM"))
            .getPrimitive(Aead::class.java)

        val testFile = tempFolder.newFile("auth_test_datastore.preferences_pb")
        val dataStore = PreferenceDataStoreFactory.create(
            produceFile = { testFile }
        )

        encryptedStorage = EncryptedStorage(dataStore, aead)
        authRepository = AuthRepository(encryptedStorage)
    }

    @Test
    fun checkInitialAuthState_whenNoTokensStored_returnsUnauthenticated() = runTest {
        val state = authRepository.checkInitialAuthState()
        assertEquals(AuthState.Unauthenticated, state)
        assertEquals(AuthState.Unauthenticated, authRepository.authState.value)
    }

    @Test
    fun checkInitialAuthState_whenTokensStored_returnsLockedRequiresBiometric() = runTest {
        encryptedStorage.writeString("auth_access_token", mockAccessToken)

        val state = authRepository.checkInitialAuthState()
        assertTrue(state is AuthState.LockedRequiresBiometric)
        assertTrue(authRepository.authState.value is AuthState.LockedRequiresBiometric)
    }

    @Test
    fun unlockSession_whenLockedAndTokensExist_transitionsToAuthenticated() = runTest {
        encryptedStorage.writeString("auth_access_token", mockAccessToken)
        authRepository.checkInitialAuthState()

        val result = authRepository.unlockSession()

        assertTrue(result)
        val currentState = authRepository.authState.value
        assertTrue(currentState is AuthState.Authenticated)
        val session = (currentState as AuthState.Authenticated).session
        assertEquals("user-uuid-1234", session.userId)
        assertEquals("developer@manta.com", session.email)
        assertEquals("tenant-uuid-5678", session.tenantId)
        assertTrue(session.roles.contains("DEVELOPER"))
    }

    @Test
    fun unlockSession_whenNoTokensExist_returnsFalseAndUnauthenticated() = runTest {
        val result = authRepository.unlockSession()

        assertFalse(result)
        assertEquals(AuthState.Unauthenticated, authRepository.authState.value)
    }

    @Test
    fun saveTokens_persistsTokensAndSetsAuthenticatedState() = runTest {
        authRepository.saveTokens(
            accessToken = mockAccessToken,
            refreshToken = "mock_refresh_token",
            idToken = "mock_id_token"
        )

        val currentState = authRepository.authState.value
        assertTrue(currentState is AuthState.Authenticated)
        val session = (currentState as AuthState.Authenticated).session
        assertEquals("user-uuid-1234", session.userId)
        assertEquals("mock_refresh_token", session.refreshToken)
        assertEquals("mock_id_token", session.idToken)

        // Verify tokens are written to EncryptedStorage
        assertEquals(mockAccessToken, encryptedStorage.readString("auth_access_token"))
        assertEquals("mock_refresh_token", encryptedStorage.readString("auth_refresh_token"))
    }

    @Test
    fun logout_clearsTokensAndResetsToUnauthenticated() = runTest {
        authRepository.saveTokens(mockAccessToken, "refresh_token", "id_token")

        authRepository.logout()

        assertEquals(AuthState.Unauthenticated, authRepository.authState.value)
        assertEquals(null, encryptedStorage.readString("auth_access_token"))
        assertEquals(null, encryptedStorage.readString("auth_refresh_token"))
        assertEquals(null, encryptedStorage.readString("auth_id_token"))
    }
}
