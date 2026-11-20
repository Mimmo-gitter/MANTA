package com.manta.mobile.core.theme.ui

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.googlefonts.Font
import androidx.compose.ui.text.googlefonts.GoogleFont
import androidx.compose.ui.unit.sp
import com.manta.mobile.R

val fontProvider = GoogleFont.Provider(
    providerAuthority = "com.google.android.gms.fonts",
    providerPackage = "com.google.android.gms",
    certificates = R.array.com_google_android_gms_fonts_certs
)

val ManropeFont = GoogleFont("Manrope")
val PlusJakartaSansFont = GoogleFont("Plus Jakarta Sans")

val ManropeFontFamily = FontFamily(
    Font(googleFont = ManropeFont, fontProvider = fontProvider, weight = FontWeight.Bold),
    Font(googleFont = ManropeFont, fontProvider = fontProvider, weight = FontWeight.SemiBold),
    Font(googleFont = ManropeFont, fontProvider = fontProvider, weight = FontWeight.Medium),
    Font(googleFont = ManropeFont, fontProvider = fontProvider, weight = FontWeight.Normal)
)

val PlusJakartaSansFontFamily = FontFamily(
    Font(
        googleFont = PlusJakartaSansFont,
        fontProvider = fontProvider,
        weight = FontWeight.SemiBold
    ),
    Font(googleFont = PlusJakartaSansFont, fontProvider = fontProvider, weight = FontWeight.Medium),
    Font(googleFont = PlusJakartaSansFont, fontProvider = fontProvider, weight = FontWeight.Normal)
)

val Typography = Typography(
    headlineLarge = TextStyle(
        fontFamily = ManropeFontFamily,
        fontWeight = FontWeight.Bold,
        fontSize = 24.sp
    ),
    headlineMedium = TextStyle(
        fontFamily = ManropeFontFamily,
        fontWeight = FontWeight.SemiBold,
        fontSize = 20.sp
    ),
    titleLarge = TextStyle(
        fontFamily = ManropeFontFamily,
        fontWeight = FontWeight.SemiBold,
        fontSize = 18.sp
    ),
    titleMedium = TextStyle(
        fontFamily = ManropeFontFamily,
        fontWeight = FontWeight.Medium,
        fontSize = 16.sp
    ),
    titleSmall = TextStyle(
        fontFamily = ManropeFontFamily,
        fontWeight = FontWeight.Medium,
        fontSize = 14.sp
    ),
    bodyLarge = TextStyle(
        fontFamily = PlusJakartaSansFontFamily,
        fontWeight = FontWeight.Normal,
        fontSize = 16.sp,
        lineHeight = 24.sp
    ),
    bodyMedium = TextStyle(
        fontFamily = PlusJakartaSansFontFamily,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 20.sp
    ),
    bodySmall = TextStyle(
        fontFamily = PlusJakartaSansFontFamily,
        fontWeight = FontWeight.Normal,
        fontSize = 12.sp,
        lineHeight = 16.sp
    ),
    labelMedium = TextStyle(
        fontFamily = PlusJakartaSansFontFamily,
        fontWeight = FontWeight.Medium,
        fontSize = 12.sp
    )
)
