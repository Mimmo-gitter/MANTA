package com.manta.mobile.core.security

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.google.crypto.tink.Aead
import com.google.crypto.tink.KeyTemplates
import com.google.crypto.tink.KeysetHandle
import com.google.crypto.tink.aead.AeadConfig
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class EncryptedStorageTest {

    @get:Rule
    @JvmField
    val tempFolder = TemporaryFolder()

    private lateinit var encryptedStorage: EncryptedStorage

    @Before
    fun setUp() {
        tempFolder.create()
        AeadConfig.register()
        val aead: Aead = KeysetHandle.generateNew(KeyTemplates.get("AES256_GCM"))
            .getPrimitive(Aead::class.java)

        val testFile = tempFolder.newFile("test_datastore.preferences_pb")
        val dataStore = PreferenceDataStoreFactory.create(
            produceFile = { testFile }
        )

        encryptedStorage = EncryptedStorage(dataStore, aead)
    }

    @Test
    fun writeAndReadString_returnsEncryptedValue() = runTest {
        val key = "auth_token"
        val expectedValue = "secret_jwt_token_12345"

        encryptedStorage.writeString(key, expectedValue)

        val actualValue = encryptedStorage.readString(key)
        assertEquals(expectedValue, actualValue)
    }

    @Test
    fun readNonExistingKey_returnsNull() = runTest {
        val actualValue = encryptedStorage.readString("non_existing_key")
        assertNull(actualValue)
    }

    @Test
    fun removeKey_deletesValue() = runTest {
        val key = "temp_key"
        encryptedStorage.writeString(key, "temp_val")

        encryptedStorage.remove(key)

        val actualValue = encryptedStorage.readString(key)
        assertNull(actualValue)
    }
}
