//! Builds the pinned llama.cpp submodule as static libraries and generates FFI bindings.
//! The patches in `patches/llama.cpp/` apply to a copy in OUT_DIR, so the submodule stays upstream.

use std::env;
use std::fs;
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::process::Command;

fn main() {
    let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let upstream = manifest.join("../../third_party/llama.cpp");
    let target = env::var("TARGET").unwrap();
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed={}", upstream.join("include/llama.h").display());
    let root = patched(&upstream, &manifest.join("../../patches/llama.cpp"));

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

    let ndk = env::var("ANDROID_NDK_HOME").or_else(|_| env::var("ANDROID_NDK_ROOT"));
    if target.contains("android") {
        let ndk = ndk.as_deref().expect("ANDROID_NDK_HOME must point at the NDK");
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
            // +fp16: native FP16 math for the F16 KV cache. MiniCPM decode at 1,100 tokens of context ran 34% faster on a
            // Pixel 10 (docs/BENCHMARKS.md). Every core with i8mm, which this build already needs, has FP16.
            cfg.define("GGML_CPU_ARM_ARCH", "armv8.2-a+dotprod+i8mm+fp16").define("GGML_CPU_KLEIDIAI", "ON");
        }
    } else {
        // The Nix cc wrapper strips -march=native, so name the features instead.
        cfg.define("GGML_NATIVE", "OFF");
        if target == "aarch64-unknown-linux-gnu" {
            // The phone's arch flags: a clang build of this target under qemu-aarch64 (-cpu max,sme=off) gives the
            // Pixel's output byte for byte, so ARM-only numerics can be evaluated on the desktop.
            cfg.define("GGML_CPU_ARM_ARCH", "armv8.2-a+dotprod+i8mm+fp16").define("GGML_CPU_KLEIDIAI", "ON");
        }
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
    if target.contains("aarch64") {
        // KleidiAI builds as its own static library (Android and the phone-exact Linux build).
        println!("cargo:rustc-link-lib=static=kleidiai");
    }

    if target.contains("android") {
        // Use the NDK sysroot instead of the host libc headers that the Nix bindgen hook adds.
        let ndk = ndk.as_deref().unwrap();
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

/// The llama.cpp source tree to build: `upstream` itself without patches, else a copy in OUT_DIR with every
/// `*.patch` in `patches` applied in name order. The copy is rebuilt when a patch or the upstream commit changes.
fn patched(upstream: &Path, patches: &Path) -> PathBuf {
    println!("cargo:rerun-if-changed={}", patches.display());
    let mut list: Vec<PathBuf> = fs::read_dir(patches)
        .map(|d| d.filter_map(|e| e.ok().map(|e| e.path())).filter(|p| p.extension().is_some_and(|x| x == "patch")).collect())
        .unwrap_or_default();
    if list.is_empty() {
        return upstream.to_path_buf();
    }
    list.sort();
    let head = Command::new("git").arg("-C").arg(upstream).args(["rev-parse", "HEAD"]).output().expect("git rev-parse");
    let mut h = std::collections::hash_map::DefaultHasher::new();
    head.stdout.hash(&mut h);
    for p in &list {
        println!("cargo:rerun-if-changed={}", p.display());
        fs::read(p).unwrap().hash(&mut h);
    }
    let stamp = format!("{:016x}", h.finish());
    let dst = PathBuf::from(env::var("OUT_DIR").unwrap()).join("llama.cpp-patched");
    if fs::read_to_string(dst.join(".patch-stamp")).ok().as_deref() == Some(stamp.as_str()) {
        return dst;
    }
    let _ = fs::remove_dir_all(&dst);
    // The test models (75 MB) and the git data are not needed for the build.
    copy_tree(upstream, &dst, &[".git", "models"]);
    for p in &list {
        // OUT_DIR lies inside our own repository: stop git from applying the paths relative to its root.
        let ok = Command::new("git")
            .current_dir(&dst)
            .env("GIT_CEILING_DIRECTORIES", dst.parent().unwrap())
            .arg("apply")
            .arg(p)
            .status()
            .expect("git apply")
            .success();
        assert!(ok, "patch {} does not apply to llama.cpp {}", p.display(), String::from_utf8_lossy(&head.stdout).trim());
    }
    fs::write(dst.join(".patch-stamp"), stamp).unwrap();
    dst
}

fn copy_tree(src: &Path, dst: &Path, skip: &[&str]) {
    fs::create_dir_all(dst).unwrap();
    for e in fs::read_dir(src).unwrap() {
        let e = e.unwrap();
        let name = e.file_name();
        if skip.iter().any(|s| name == *s) {
            continue;
        }
        let (from, to) = (e.path(), dst.join(&name));
        let ty = e.file_type().unwrap();
        if ty.is_symlink() {
            std::os::unix::fs::symlink(fs::read_link(&from).unwrap(), &to).unwrap();
        } else if ty.is_dir() {
            copy_tree(&from, &to, &[]);
        } else {
            fs::copy(&from, &to).unwrap();
        }
    }
}
