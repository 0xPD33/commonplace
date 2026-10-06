plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "app.commonplace"
    compileSdk = 37
    buildToolsVersion = "37.0.0"
    ndkVersion = "29.0.14206865"

    defaultConfig {
        applicationId = "app.commonplace"
        minSdk = 31
        targetSdk = 36
        versionCode = 2
        versionName = "0.1.1"
        // scripts/build-android.sh passes -Pabis=… to match the Rust libraries it built.
        ndk { abiFilters += (project.findProperty("abis") as String? ?: "arm64-v8a,x86_64").split(",") }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    androidResources {
        noCompress += listOf("onnx", "onnx_data", "safetensors", "json")
    }
}

// The app must never gain network access. Fails the build if any
// dependency manifest sneaks INTERNET back into the merged manifest.
androidComponents {
    onVariants { variant ->
        val name = variant.name.replaceFirstChar { it.uppercase() }
        val check = tasks.register("check${name}NoInternet") {
            val manifest = layout.buildDirectory.file("intermediates/merged_manifest/${variant.name}/process${name}MainManifest/AndroidManifest.xml")
            inputs.file(manifest)
            doLast {
                val text = manifest.get().asFile.readText()
                if (text.contains("android.permission.INTERNET")) {
                    throw GradleException("INTERNET permission found in the merged ${variant.name} manifest")
                }
            }
        }
        tasks.matching { it.name == "process${name}MainManifest" }.configureEach { finalizedBy(check) }
    }
}

// Moonshine's AAR bundles its own ONNX Runtime 1.23.2 (minimal build) under the file name of our full
// 1.30.0, and its libraries need the symbol version VERS_1.23.2, which 1.30.0 does not define. Give
// Moonshine's copy a private name so both load side by side. Needs patchelf (in the Nix dev shell).
val moonshineAar = configurations.detachedConfiguration(dependencies.create("ai.moonshine:moonshine-voice:0.1.5@aar"))
    .apply { isTransitive = false }
val moonshineDir = layout.buildDirectory.dir("moonshine/aar")
val unpackMoonshine = tasks.register<Sync>("unpackMoonshineAar") {
    from(zipTree(moonshineAar.singleFile))
    into(moonshineDir)
}
val renameMoonshineOrt = tasks.register("renameMoonshineOrt") {
    dependsOn(unpackMoonshine)
    val root = moonshineDir
    doLast {
        root.get().asFile.walkTopDown().filter { it.name == "libonnxruntime.so" }.forEach { ort ->
            val renamed = File(ort.parentFile, "libonnxruntime_moonshine.so")
            ort.renameTo(renamed)
            val patches = listOf(listOf("--set-soname", renamed.name, renamed.path)) +
                listOf("libmoonshine.so", "libmoonshine-jni.so").map {
                    listOf("--replace-needed", "libonnxruntime.so", renamed.name, File(ort.parentFile, it).path)
                }
            for (args in patches) {
                check(ProcessBuilder(listOf("patchelf") + args).inheritIO().start().waitFor() == 0) { "patchelf failed: $args" }
            }
        }
    }
}
val slimMoonshine = tasks.register<Zip>("repackMoonshineAar") {
    dependsOn(renameMoonshineOrt)
    from(moonshineDir)
    archiveFileName = "moonshine-voice-repacked.aar"
    destinationDirectory = layout.buildDirectory.dir("moonshine")
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2026.09.00"))
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended:1.7.8")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.11.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.11.0")
    implementation("androidx.lifecycle:lifecycle-process:2.11.0")
    implementation("androidx.navigation:navigation-compose:2.10.2")
    implementation("net.java.dev.jna:jna:5.19.1@aar")
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.30.0")
    implementation("com.google.ai.edge.litertlm:litertlm-android:0.17.1")
    // Voice input. Models load from files, so the downloader stack (OkHttp, WorkManager) is never used.
    implementation(files(slimMoonshine))
    implementation("androidx.appcompat:appcompat:1.6.1")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
