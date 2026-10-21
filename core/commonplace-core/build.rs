fn main() {
    let hash = std::process::Command::new("git")
        .args(["describe", "--always", "--dirty", "--exclude", "*"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "dev".into());
    println!("cargo:rustc-env=CP_BUILD={hash}");
    // .git/HEAD changes only on a branch switch; the reflog changes on every commit.
    println!("cargo:rerun-if-changed=../../.git/logs/HEAD");
}
