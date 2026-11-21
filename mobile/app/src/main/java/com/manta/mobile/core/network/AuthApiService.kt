package com.manta.mobile.core.network

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import retrofit2.Response
import retrofit2.http.GET

@Serializable
data class UserProfileResponse(
    val sub: String = "",
    val email: String = "",
    @SerialName("preferred_username") val preferredUsername: String = "",
    val name: String = "",
    @SerialName("tenant_id") val tenantId: String = "",
    val roles: List<String> = emptyList()
)

interface AuthApiService {
    @GET("auth/me")
    suspend fun getCurrentUser(): Response<UserProfileResponse>
}
