package com.manta.mobile.data.auth

import android.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

object JwtUtils {
    private val json = Json { ignoreUnknownKeys = true }

    fun parseClaims(accessToken: String): UserClaims? {
        return try {
            val parts = accessToken.split(".")
            if (parts.size < 2) return null
            val payloadBase64 = parts[1]
            val payloadBytes = Base64.decode(payloadBase64, Base64.URL_SAFE)
            val payloadJsonString = String(payloadBytes, Charsets.UTF_8)
            val jsonObject = json.parseToJsonElement(payloadJsonString).jsonObject

            val sub = jsonObject["sub"]?.jsonPrimitive?.content ?: ""
            val email = jsonObject["email"]?.jsonPrimitive?.content ?: ""
            val name = jsonObject["name"]?.jsonPrimitive?.content ?: ""
            val tenantId = jsonObject["tenant_id"]?.jsonPrimitive?.content ?: ""

            val rolesList = mutableListOf<String>()
            val realmAccess = jsonObject["realm_access"]?.jsonObject
            realmAccess?.get("roles")?.jsonArray?.forEach {
                rolesList.add(it.jsonPrimitive.content)
            }

            UserClaims(
                userId = sub,
                email = email,
                name = name,
                tenantId = tenantId,
                roles = rolesList
            )
        } catch (_: Exception) {
            null
        }
    }
}

data class UserClaims(
    val userId: String,
    val email: String,
    val name: String,
    val tenantId: String,
    val roles: List<String>
)
