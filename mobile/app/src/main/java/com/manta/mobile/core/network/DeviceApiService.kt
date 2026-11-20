package com.manta.mobile.core.network

import com.manta.mobile.BuildConfig
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.POST
import retrofit2.http.Path

@Serializable
data class RegisterMobileDeviceRequest(
    @SerialName("fcm_token") val fcmToken: String,
    val platform: String = "android",
    @SerialName("device_name") val deviceName: String? =
        "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}",
    @SerialName("app_version") val appVersion: String? = BuildConfig.VERSION_NAME
)

interface DeviceApiService {
    @POST("mobile/devices")
    suspend fun registerDevice(@Body request: RegisterMobileDeviceRequest): Response<Unit>

    @DELETE("mobile/devices/{token}")
    suspend fun unregisterDevice(@Path("token") token: String): Response<Unit>
}
