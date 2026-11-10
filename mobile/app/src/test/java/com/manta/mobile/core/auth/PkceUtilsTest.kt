package com.manta.mobile.core.auth

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class PkceUtilsTest {

    @Test
    fun generateCodeVerifier_defaultLength_creates64CharString() {
        val verifier = PkceUtils.generateCodeVerifier()
        assertEquals(64, verifier.length)
        assertTrue(verifier.matches(Regex("^[A-Za-z0-9\\-._~]+$")))
    }

    @Test
    fun generateCodeVerifier_customLength_createsSpecifiedLengthString() {
        val verifier = PkceUtils.generateCodeVerifier(128)
        assertEquals(128, verifier.length)
    }

    @Test
    fun generateCodeVerifier_generatesUniqueValues() {
        val verifier1 = PkceUtils.generateCodeVerifier()
        val verifier2 = PkceUtils.generateCodeVerifier()
        assertNotEquals(verifier1, verifier2)
    }

    @Test
    fun generateCodeChallenge_producesValidBase64UrlChallenge() {
        val verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        val challenge = PkceUtils.generateCodeChallenge(verifier)

        assertTrue(challenge.isNotEmpty())
        assertTrue(!challenge.contains("="))
        assertTrue(!challenge.contains("+"))
        assertTrue(!challenge.contains("/"))
    }
}
