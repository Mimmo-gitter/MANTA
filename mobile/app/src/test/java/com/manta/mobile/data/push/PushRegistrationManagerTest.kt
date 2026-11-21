package com.manta.mobile.data.push

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.google.crypto.tink.Aead
import com.google.crypto.tink.KeyTemplates
import com.google.crypto.tink.KeysetHandle
import com.google.crypto.tink.aead.AeadConfig
import com.manta.mobile.core.network.DeviceApiService
import com.manta.mobile.core.network.RegisterMobileDeviceRequest
import com.manta.mobile.core.security.EncryptedStorage
import com.manta.mobile.features.auth.AuthRepository
import kotlinx.coroutines.test.runTest
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
import retrofit2.Response

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class PushRegistrationManagerTest {
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

        val testFile = tempFolder.newFile("push_test.preferences_pb")
        val dataStore = PreferenceDataStoreFactory.create(
            produceFile = { testFile }
        )

        encryptedStorage = EncryptedStorage(dataStore, aead)
        authRepository = AuthRepository(encryptedStorage)
    }

    @Test
    fun registerDeviceToken_whenAuthenticated_sendsPostRequestWithPayload() = runTest {
        var capturedRequest: RegisterMobileDeviceRequest? = null
        val mockDeviceApi = object : DeviceApiService {
            override suspend fun registerDevice(
                request: RegisterMobileDeviceRequest
            ): Response<Unit> {
                capturedRequest = request
                return Response.success(Unit)
            }

            override suspend fun unregisterDevice(token: String): Response<Unit> =
                Response.success(Unit)
        }

        val pushRegistrationManager = PushRegistrationManager(mockDeviceApi, authRepository)

        // Authenticate user
        authRepository.saveTokens(mockAccessToken, "refresh_token", "id_token")

        val result = pushRegistrationManager.registerDeviceToken("test_fcm_token_123")

        assertTrue(result.isSuccess)
        assertEquals("test_fcm_token_123", capturedRequest?.fcmToken)
        assertEquals("android", capturedRequest?.platform)
    }

    @Test
    fun registerDeviceToken_whenUnauthenticated_skipsApiCall() = runTest {
        var capturedRequest: RegisterMobileDeviceRequest? = null
        val mockDeviceApi = object : DeviceApiService {
            override suspend fun registerDevice(
                request: RegisterMobileDeviceRequest
            ): Response<Unit> {
                capturedRequest = request
                return Response.success(Unit)
            }

            override suspend fun unregisterDevice(token: String): Response<Unit> =
                Response.success(Unit)
        }

        val pushRegistrationManager = PushRegistrationManager(mockDeviceApi, authRepository)

        val result = pushRegistrationManager.registerDeviceToken("test_fcm_token_123")

        assertTrue(result.isSuccess)
        assertNull(capturedRequest)
    }

    @Test
    fun registerDeviceToken_whenBlankToken_returnsFailure() = runTest {
        val mockDeviceApi = object : DeviceApiService {
            override suspend fun registerDevice(
                request: RegisterMobileDeviceRequest
            ): Response<Unit> = Response.success(Unit)

            override suspend fun unregisterDevice(token: String): Response<Unit> =
                Response.success(Unit)
        }

        val pushRegistrationManager = PushRegistrationManager(mockDeviceApi, authRepository)
        authRepository.saveTokens(mockAccessToken, "refresh_token", "id_token")

        val result = pushRegistrationManager.registerDeviceToken("   ")

        assertTrue(result.isFailure)
        assertEquals("FCM token must not be blank", result.exceptionOrNull()?.message)
    }
}
