plugins { id("com.android.application"); id("org.jetbrains.kotlin.android") }
android { namespace = "com.phoneagent.team"; compileSdk = 34; defaultConfig { applicationId = "com.phoneagent.team"; minSdk = 26; targetSdk = 34; versionCode = 1; versionName = "3.0.0" } }
dependencies { implementation("androidx.core:core-ktx:1.13.1"); implementation("com.google.android.material:material:1.12.0"); implementation("androidx.appcompat:appcompat:1.7.0"); testImplementation("junit:junit:4.13.2") }
