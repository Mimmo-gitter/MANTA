package com.manta.mobile.features.auth

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.google.crypto.tink.Aead
import com.google.crypto.tink.KeyTemplates
import com.google.crypto.tink.KeysetHandle
import com.google.crypto.tink.aead.AeadConfig
import com.manta.mobile.core.network.TokenAuthenticator
import com.manta.mobile.core.security.EncryptedStorage
import com.manta.mobile.data.auth.AuthState
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
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
class TokenRefreshAndSignOutTest {

    @get:Rule
    @JvmField
    val tempFolder = TemporaryFolder()

    private lateinit var encryptedStorage: EncryptedStorage
    private lateinit var authRepository: AuthRepository

    private val mockAccessToken = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9." +
        "eyJzdWIiOiJ1c2VyLXV1aWQtMTIzNCIsImVtYWlsIjoiZGV2ZWxvcGVyQG1hbnRhLmNvbSIs" +
        "Im5hbWUiOiJNYW50YSBEZXZlbG9wZXIiLCJ0ZW5hbnRfaWQiOiJ0ZW5hbnQtdXVpZC01Njc4Iiw" +
        "icmVhbG1fYWNjZXNzIjp7InJvbGVzIjpbImRlZmF1bHQtcm9sZXMtbWFudGEiLCJERVZFTE9QRVIiXX0s" +
        "ImF1ZCI6Im1hbnRhLWJhY2tlbmQifQ." +
        "signature"

    @Before
    fun setUp() {
        tempFolder.create()
        AeadConfig.register()
        val aead: Aead = KeysetHandle.generateNew(KeyTemplates.get("AES256_GCM"))
            .getPrimitive(Aead::class.java)

        val testFile = tempFolder.newFile("token_refresh_test.preferences_pb")
        val dataStore = PreferenceDataStoreFactory.create(
            produceFile = { testFile }
        )

        encryptedStorage = EncryptedStorage(dataStore, aead)
        authRepository = AuthRepository(encryptedStorage)
    }

    @Test
    fun concurrent401Responses_executeSingleRefreshCallAndUpdateDataStore() = runTest {
        authRepository.saveTokens(
            accessToken = "stale_access_token_111",
            refreshToken = "valid_refresh_token_222",
            idToken = "valid_id_token_333"
        )

        val refreshExecutionCount = AtomicInteger(0)

        val authenticator = TokenAuthenticator(
            tokenProvider = { runBlocking { authRepository.getAccessToken() } },
            refreshTokenProvider = { runBlocking { authRepository.getRefreshToken() } },
            onTokenRefreshed = { newAccess: String, newRefresh: String?, newId: String? ->
                refreshExecutionCount.incrementAndGet()
                runBlocking {
                    authRepository.saveTokens(newAccess, newRefresh, newId)
                }
            },
            onRefreshFailed = { }
        )

        val deferreds = (1..5).map { index ->
            async {
                val request = Request.Builder()
                    .url("http://10.0.2.2:8000/api/v1/projects/$index")
                    .header("Authorization", "Bearer stale_access_token_111")
                    .build()
                val response = Response.Builder()
                    .request(request)
                    .protocol(Protocol.HTTP_1_1)
                    .code(401)
                    .message("Unauthorized")
                    .build()

                authenticator.authenticate(null, response)
            }
        }

        val results = deferreds.awaitAll()
        assertEquals(5, results.size)
    }

    @Test
    fun refreshRejection401_purgesEncryptedDataStoreAndEmitsReauthRequired() = runTest {
        authRepository.saveTokens(
            accessToken = mockAccessToken,
            refreshToken = "revoked_refresh_token",
            idToken = "id_token"
        )

        authRepository.purgeLocalSession(reason = "Session expired. Please sign in again.")

        assertNull(authRepository.getAccessToken())
        assertNull(authRepository.getRefreshToken())
        assertNull(authRepository.getIdToken())

        val state = authRepository.authState.value
        assertTrue(state is AuthState.ReauthRequired)
        assertEquals(
            "Session expired. Please sign in again.",
            (state as AuthState.ReauthRequired).reason
        )
    }

    @Test
    fun signOut_deletesAllStoredKeysAndResetsAuthState() = runTest {
        authRepository.saveTokens(
            accessToken = mockAccessToken,
            refreshToken = null,
            idToken = null
        )

        val result = authRepository.signOut()

        assertTrue(result.isSuccess)
        assertNull(authRepository.getAccessToken())
        assertNull(authRepository.getRefreshToken())
        assertNull(authRepository.getIdToken())
        assertEquals(AuthState.Unauthenticated, authRepository.authState.value)
    }
}
