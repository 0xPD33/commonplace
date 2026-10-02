//! Builds the pinned llama.cpp submodule as static libraries and generates FFI bindings.

use std::env;
use std::path::PathBuf;

fn main() {
    let root = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("../../third_party/llama.cpp");
    let target = env::var("TARGET").unwrap();
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed={}", root.join("include/llama.h").display());

    let mut cfg = cmake::Config::new(&root);
    cfg.profile("Release")
        .define("BUILD_SHARED_LIBS", "OFF")
        .define("LLAMA_BUILD_COMMON", "OFF")
        .define("LLAMA_BUILD_TESTS", "OFF")
        .define("LLAMA_BUILD_TOOLS", "OFF")
        .define("LLAMA_BUILD_EXAMPLES", "OFF")
        .define("LLAMA_BUILD_SERVER", "OFF")
        .define("LLAMA_BUILD_APP", "OFF")
        .define("LLAMA_OPENSSL", "OFF")
        .define("GGML_OPENMP", "OFF")
        .define("GGML_BACKEND_DL", "OFF");

    if target.contains("android") {
        let ndk = env::var("ANDROID_NDK_HOME")
            .or_else(|_| env::var("ANDROID_NDK_ROOT"))
            .expect("ANDROID_NDK_HOME must point at the NDK");
        let abi = match target.split('-').next().unwrap() {
            "aarch64" => "arm64-v8a",
            "x86_64" => "x86_64",
            a => panic!("unsupported android arch {a}"),
        };
        cfg.define("CMAKE_TOOLCHAIN_FILE", format!("{ndk}/build/cmake/android.toolchain.cmake"))
            .define("ANDROID_ABI", abi)
            .define("ANDROID_PLATFORM", "android-31")
            .define("ANDROID_STL", "c++_static")
            .define("GGML_NATIVE", "OFF");
        if abi == "arm64-v8a" {
            // Build trap: without the explicit arch the dotprod check can silently fail.
            cfg.define("GGML_CPU_ARM_ARCH", "armv8.2-a+dotprod+i8mm").define("GGML_CPU_KLEIDIAI", "ON");
        }
    } else {
        // The Nix cc wrapper strips -march=native, so name the features instead.
        cfg.define("GGML_NATIVE", "OFF");
    }
    if target.starts_with("x86_64") {
        // Desktop and the emulator (KVM passes the host AVX2/FMA/F16C through).
        for f in ["GGML_AVX", "GGML_AVX2", "GGML_FMA", "GGML_F16C", "GGML_BMI2"] {
            cfg.define(f, "ON");
        }
    }
    let dst = cfg.build();

    for dir in ["lib", "lib64"] {
        println!("cargo:rustc-link-search=native={}", dst.join(dir).display());
    }
    for lib in ["llama", "ggml", "ggml-cpu", "ggml-base"] {
        println!("cargo:rustc-link-lib=static={lib}");
    }
    if target.contains("android") {
        println!("cargo:rustc-link-lib=c++_static");
        println!("cargo:rustc-link-lib=c++abi");
        println!("cargo:rustc-link-lib=log");
    } else {
        println!("cargo:rustc-link-lib=dylib=stdc++");
    }
    if target.contains("aarch64") && target.contains("android") {
        // KleidiAI builds as its own static library.
        println!("cargo:rustc-link-lib=static=kleidiai");
    }

    if target.contains("android") {
        // Use the NDK sysroot instead of the host libc headers that the Nix bindgen hook adds.
        let ndk = env::var("ANDROID_NDK_HOME").unwrap();
        let sysroot = format!("--sysroot={ndk}/toolchains/llvm/prebuilt/linux-x86_64/sysroot");
        unsafe { env::set_var(format!("BINDGEN_EXTRA_CLANG_ARGS_{target}"), sysroot) };
    }
    let bindings = bindgen::Builder::default()
        .header(root.join("include/llama.h").to_string_lossy())
        .clang_arg(format!("-I{}", root.join("ggml/include").display()))
        .allowlist_function("llama_.*|ggml_threadpool_.*|ggml_backend_load_all")
        .allowlist_type("llama_.*|ggml_threadpool_params")
        .prepend_enum_name(false)
        .generate()
        .expect("bindgen llama.h");
    bindings.write_to_file(PathBuf::from(env::var("OUT_DIR").unwrap()).join("bindings.rs")).unwrap();
}
