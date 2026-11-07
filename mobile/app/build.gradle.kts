plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.google.services)
    alias(libs.plugins.ktlint)
}

val robolectricAndroidAll: Configuration = configurations.create("robolectricAndroidAll")

android {
    namespace = "com.manta.mobile"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        applicationId = "com.manta.mobile"
        minSdk = 27
        targetSdk = 37
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            optimization {
                enable = true
                packageScope = setOf("androidx.**", "kotlin.**", "kotlinx.**")
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        buildConfig = true
        compose = true
    }
    flavorDimensions += listOf("environment")
    productFlavors {
        create("dev") {
            dimension = "environment"
            applicationIdSuffix = ".dev"
            versionNameSuffix = "-dev"
            buildConfigField("String", "API_URL", "\"http://localhost:8000/api\"")
        }
        create("stage") {
            dimension = "environment"
            applicationIdSuffix = ".stage"
            versionNameSuffix = "-stage"
            buildConfigField("String", "API_URL", "\"https://stage-api.yourorg.com/api/v1/\"")
        }
    }
    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            all { test ->
                test.systemProperty(
                    "robolectric.dependency.dir",
                    robolectricAndroidAll.singleFile.parentFile.absolutePath
                )
            }
        }
    }
}

tasks.getByPath("preBuild").dependsOn("ktlintFormat")

tasks.withType<Test>().configureEach {
    if (System.getProperty("os.name").contains("Windows", ignoreCase = true)) {
        jvmArgs("-Djavax.net.ssl.trustStoreType=WINDOWS-ROOT")
    }
}

ktlint {
    ignoreFailures.set(false)
    reporters {
        reporter(org.jlleitschuh.gradle.ktlint.reporter.ReporterType.PLAIN)
        reporter(org.jlleitschuh.gradle.ktlint.reporter.ReporterType.CHECKSTYLE)
        reporter(org.jlleitschuh.gradle.ktlint.reporter.ReporterType.SARIF)
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(platform(libs.firebase.bom))

    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.extended)
    implementation(libs.androidx.compose.ui.text.google.fonts)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)

    // Networking (Retrofit + OkHttp + Serialization)
    implementation(libs.retrofit)
    implementation(libs.retrofit.converter.kotlinx.serialization)
    implementation(libs.okhttp)
    implementation(libs.okhttp.logging.interceptor)
    implementation(libs.kotlinx.serialization.json)
    // Encrypted DataStore + Tink
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.tink.android)
    // Biometrics
    implementation(libs.androidx.biometric)
    // Firebase
    implementation(libs.firebase.messaging)
    // Vico
    implementation(libs.vico.compose)
    implementation(libs.vico.compose.m2)
    implementation(libs.vico.compose.m3)
    implementation(libs.vico.compose.glance)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(platform(libs.androidx.compose.bom))
    testImplementation(libs.androidx.compose.ui.test.junit4)
    testImplementation(libs.robolectric)
    robolectricAndroidAll(libs.robolectric.androidall)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.junit)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
