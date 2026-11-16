package com.manta.mobile.core.network

import retrofit2.Response
import retrofit2.http.DELETE
import retrofit2.http.Path

interface DeviceApiService {
    @DELETE("api/v1/mobile/devices/{token}")
    suspend fun unregisterDevice(@Path("token") token: String): Response<Unit>
}
