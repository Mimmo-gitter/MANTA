package com.manta.mobile.core.auth

import android.util.Base64
import java.security.MessageDigest
import java.security.SecureRandom

object PkceUtils {
    private const val ALLOWED_CHARACTERS =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"

    fun generateCodeVerifier(length: Int = 64): String {
        require(length in 43..128) { "Code verifier length must be between 43 and 128 characters" }
        val random = SecureRandom()
        val verifier = StringBuilder(length)
        for (i in 0 until length) {
            verifier.append(ALLOWED_CHARACTERS[random.nextInt(ALLOWED_CHARACTERS.length)])
        }
        return verifier.toString()
    }

    fun generateCodeChallenge(codeVerifier: String): String {
        val bytes = codeVerifier.toByteArray(Charsets.US_ASCII)
        val messageDigest = MessageDigest.getInstance("SHA-256")
        val digest = messageDigest.digest(bytes)
        return Base64.encodeToString(digest, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
    }
}
