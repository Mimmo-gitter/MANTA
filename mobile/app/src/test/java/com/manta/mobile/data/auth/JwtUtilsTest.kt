package com.manta.mobile.data.auth

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class JwtUtilsTest {

    @Test
    fun parseClaims_extractsKeycloakClaimsCorrectly() {
        // Mock Keycloak JWT Token
        val mockJwt = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9." +
            "eyJzdWIiOiJ1c2VyLXV1aWQtMTIzNCIsImVtYWlsIjoiZGV2ZWxvcGVyQG1hbnRhLmNvbSIs" +
            "Im5hbWUiOiJNYW50YSBEZXZlbG9wZXIiLCJ0ZW5hbnRfaWQiOiJ0ZW5hbnQtdXVpZC01Njc4Iiw" +
            "icmVhbG1fYWNjZXNzIjp7InJvbGVzIjpbIkRFVkVMT1BFUiIsIlRFTkFOVF9BRE1JTiJdfSw" +
            "iYXVkIjoibWFudGEtYmFja2VuZCJ9." +
            "signature"

        val claims = JwtUtils.parseClaims(mockJwt)

        assertNotNull(claims)
        assertEquals("user-uuid-1234", claims?.userId)
        assertEquals("developer@manta.com", claims?.email)
        assertEquals("Manta Developer", claims?.name)
        assertEquals("tenant-uuid-5678", claims?.tenantId)
        assertTrue(claims?.roles?.contains("DEVELOPER") == true)
        assertTrue(claims?.roles?.contains("TENANT_ADMIN") == true)
    }
}
