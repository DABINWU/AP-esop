import java.util.Properties

plugins { id("com.android.application"); id("org.jetbrains.kotlin.android"); id("org.jetbrains.kotlin.kapt") }
val releasePropertiesFile=rootProject.file("signing/release.properties")
val releaseProperties=Properties().apply {if(releasePropertiesFile.isFile)releasePropertiesFile.inputStream().use {load(it)}}
check(!gradle.startParameter.taskNames.any {it.contains("release",ignoreCase=true)} || releasePropertiesFile.isFile) {"Release signing configuration missing: android/signing/release.properties"}
android {
    namespace = "com.ap.esop"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.ap.esop"
        resValue("string","app_name","艾普 ESOP")
        minSdk = 23
        targetSdk = 34
        versionCode = 6
        versionName = "1.0.5"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    signingConfigs {if(releasePropertiesFile.isFile)create("esopRelease") {
        storeFile=rootProject.file(releaseProperties.getProperty("storeFile"))
        storePassword=releaseProperties.getProperty("storePassword")
        keyAlias=releaseProperties.getProperty("keyAlias")
        keyPassword=releaseProperties.getProperty("keyPassword")
    }}
    buildTypes { release { isMinifyEnabled = false;isDebuggable=false;signingConfig=signingConfigs.findByName("esopRelease") } }
    testOptions { unitTests.isReturnDefaultValues = true }
}
kapt { arguments { arg("room.schemaLocation", "$projectDir/schemas") } }
dependencies {
    implementation("androidx.activity:activity-ktx:1.9.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.6.2")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    kapt("androidx.room:room-compiler:2.6.1")
    implementation("androidx.media3:media3-exoplayer:1.4.1")
    implementation("androidx.media3:media3-ui:1.4.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.google.code.gson:gson:2.10.1")
    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    androidTestImplementation("androidx.test:runner:1.5.2")
    androidTestImplementation("androidx.test.ext:junit:1.1.5")
}
