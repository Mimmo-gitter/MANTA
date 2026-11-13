package com.manta.mobile.features.auth

import android.net.Uri
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.google.crypto.tink.Aead
import com.google.crypto.tink.KeyTemplates
import com.google.crypto.tink.KeysetHandle
import com.google.crypto.tink.aead.AeadConfig
import com.manta.mobile.core.security.EncryptedStorage
import com.manta.mobile.data.auth.PkceAuthManager
import com.manta.mobile.data.auth.PkceFlowState
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
class PkceAuthManagerTest {
    @get:Rule
    @JvmField
    val tempFolder = TemporaryFolder()

    private lateinit var authRepository: AuthRepository

    @Before
    fun setUp() {
        tempFolder.create()
        AeadConfig.register()
        val aead: Aead = KeysetHandle.generateNew(KeyTemplates.get("AES256_GCM"))
            .getPrimitive(Aead::class.java)

        val testFile = tempFolder.newFile("pkce_test_datastore.preferences_pb")
        val dataStore = PreferenceDataStoreFactory.create(
            produceFile = { testFile }
        )

        val encryptedStorage = EncryptedStorage(dataStore, aead)
        authRepository = AuthRepository(encryptedStorage)
        PkceAuthManager.resetState()
    }

    @Test
    fun resetState_resetsFlowStateToIdle() {
        PkceAuthManager.resetState()
        assertEquals(PkceFlowState.Idle, PkceAuthManager.flowState.value)
    }

    @Test
    fun handleAuthorizationResponse_whenErrorParameterPresent_setsErrorState() = runTest {
        val uri = Uri.parse(
            "manta://auth/callback?error=access_denied&error_description=User+cancelled"
        )
        val result = PkceAuthManager.handleAuthorizationResponse(uri, authRepository)

        assertFalse(result)
        assertTrue(PkceAuthManager.flowState.value is PkceFlowState.Error)
        val errorMessage = (PkceAuthManager.flowState.value as PkceFlowState.Error).message
        assertTrue(errorMessage.contains("User cancelled"))
    }

    @Test
    fun handleAuthorizationResponse_whenCodeMissing_setsErrorState() = runTest {
        val uri = Uri.parse("manta://auth/callback")
        val result = PkceAuthManager.handleAuthorizationResponse(uri, authRepository)

        assertFalse(result)
        assertTrue(PkceAuthManager.flowState.value is PkceFlowState.Error)
        val errorMessage = (PkceAuthManager.flowState.value as PkceFlowState.Error).message
        assertTrue(errorMessage.contains("No authorization code"))
    }

    @Test
    fun handleAuthorizationResponse_whenVerifierMissing_setsErrorState() = runTest {
        val uri = Uri.parse("manta://auth/callback?code=valid_code_123")
        val result = PkceAuthManager.handleAuthorizationResponse(uri, authRepository)

        assertFalse(result)
        assertTrue(PkceAuthManager.flowState.value is PkceFlowState.Error)
        val errorMessage = (PkceAuthManager.flowState.value as PkceFlowState.Error).message
        assertTrue(errorMessage.contains("Code verifier missing"))
    }
}
