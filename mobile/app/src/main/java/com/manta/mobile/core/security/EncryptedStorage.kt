package com.manta.mobile.core.security

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.google.crypto.tink.Aead
import com.google.crypto.tink.aead.AeadConfig
import com.google.crypto.tink.aead.AeadKeyTemplates
import com.google.crypto.tink.integration.android.AndroidKeysetManager
import java.util.Base64
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(
    name = "manta_encrypted_prefs"
)

class EncryptedStorage internal constructor(
    private val dataStore: DataStore<Preferences>,
    private val aead: Aead
) {
    constructor(context: Context) : this(
        dataStore = context.applicationContext.dataStore,
        aead = getOrCreateAead(context.applicationContext)
    )

    suspend fun writeString(key: String, value: String) {
        val prefKey = stringPreferencesKey(key)
        val encryptedBytes = aead.encrypt(value.toByteArray(Charsets.UTF_8), null)
        val base64Encrypted = Base64.getEncoder().encodeToString(encryptedBytes)
        dataStore.edit { preferences ->
            preferences[prefKey] = base64Encrypted
        }
    }

    suspend fun readString(key: String): String? {
        val prefKey = stringPreferencesKey(key)
        val preferences = dataStore.data.first()
        val base64Encrypted = preferences[prefKey] ?: return null
        return try {
            val encryptedBytes = Base64.getDecoder().decode(base64Encrypted)
            val decryptedBytes = aead.decrypt(encryptedBytes, null)
            String(decryptedBytes, Charsets.UTF_8)
        } catch (_: Exception) {
            null
        }
    }

    fun observeString(key: String): Flow<String?> {
        val prefKey = stringPreferencesKey(key)
        return dataStore.data.map { preferences ->
            val base64Encrypted = preferences[prefKey] ?: return@map null
            try {
                val encryptedBytes = Base64.getDecoder().decode(base64Encrypted)
                val decryptedBytes = aead.decrypt(encryptedBytes, null)
                String(decryptedBytes, Charsets.UTF_8)
            } catch (_: Exception) {
                null
            }
        }
    }

    suspend fun remove(key: String) {
        val prefKey = stringPreferencesKey(key)
        dataStore.edit { preferences ->
            preferences.remove(prefKey)
        }
    }

    suspend fun clearAll() {
        dataStore.edit { preferences ->
            preferences.clear()
        }
    }

    companion object {
        init {
            AeadConfig.register()
        }

        private const val KEYSET_NAME = "manta_keyset"
        private const val PREF_FILE_NAME = "manta_tink_prefs"
        private const val MASTER_KEY_URI = "android-keystore://manta_master_key"

        fun getOrCreateAead(context: Context): Aead {
            val keysetHandle = AndroidKeysetManager.Builder()
                .withSharedPref(context, KEYSET_NAME, PREF_FILE_NAME)
                .withKeyTemplate(AeadKeyTemplates.AES256_GCM)
                .withMasterKeyUri(MASTER_KEY_URI)
                .build()
                .keysetHandle

            return keysetHandle.getPrimitive(Aead::class.java)
        }
    }
}
