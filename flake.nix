{
  description = "Commonplace: offline research assistant for Android";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { nixpkgs, rust-overlay, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [ rust-overlay.overlays.default ];
        config = {
          allowUnfree = true;
          android_sdk.accept_license = true;
        };
      };

      rust = pkgs.rust-bin.stable.latest.default.override {
        extensions = [ "rust-src" "rust-analyzer" ];
        targets = [ "aarch64-linux-android" "x86_64-linux-android" ];
      };

      android = pkgs.androidenv.composeAndroidPackages {
        platformVersions = [ "36" "37.0" ];
        buildToolsVersions = [ "37.0.0" ];
        includeNDK = true;
        ndkVersions = [ "29.0.14206865" ];
        cmakeVersions = [ "3.22.1" ];
        # Headless test device: AOSP image without Play Services, like GrapheneOS.
        includeEmulator = true;
        includeSystemImages = true;
        systemImageTypes = [ "default" ];
        abiVersions = [ "x86_64" ];
      };
      sdk = "${android.androidsdk}/libexec/android-sdk";
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [
          rust
          cargo-ndk
          android.androidsdk
          jdk17
          cmake
          ninja
          pkg-config
          rustPlatform.bindgenHook
          uv
          python312
          git-lfs
          gradle
        ];

        ANDROID_HOME = sdk;
        ANDROID_SDK_ROOT = sdk;
        ANDROID_NDK_HOME = "${sdk}/ndk/29.0.14206865";
        JAVA_HOME = "${pkgs.jdk17}";
        ORT_DYLIB_PATH = "${pkgs.onnxruntime}/lib/libonnxruntime.so";
        # Gradle downloads its own aapt2, which cannot run on NixOS.
        GRADLE_OPTS = "-Dorg.gradle.project.android.aapt2FromMavenOverride=${sdk}/build-tools/37.0.0/aapt2";
        # uv-installed wheels (torch, vllm) need libstdc++ and the host CUDA driver.
        LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib ] + ":/run/opengl-driver/lib";
        # Triton (ModernBERT kernels) looks for libcuda with /sbin/ldconfig, which NixOS lacks.
        TRITON_LIBCUDA_PATH = "/run/opengl-driver/lib";
        UV_PYTHON_DOWNLOADS = "never";
      };
    };
}
