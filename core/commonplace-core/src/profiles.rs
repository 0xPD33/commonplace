//! Per-model defaults. A model pack names its profile in `manifest.model.profile`; a pack without one gets the
//! profile that its file name implies. The user's settings override the numbers.

use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct ModelProfile {
    pub name: &'static str,
    /// The evidence budget when the user has not set one.
    pub evidence_tokens: usize,
    /// Shrink the evidence on a warm phone: a hot CPU reads the prompt slowly, the Tensor NPU does not.
    pub warm_evidence_cut: bool,
}

/// Ling and MiniCPM on the CPU (llama.cpp).
pub const DEFAULT: ModelProfile = ModelProfile { name: "default", evidence_tokens: 600, warm_evidence_cut: true };
/// Gemma 4 through llama.cpp (the desktop stand-in for the NPU file) or LiteRT's CPU and GPU backends.
pub const GEMMA4: ModelProfile = ModelProfile { name: "gemma4", evidence_tokens: 600, warm_evidence_cut: true };
/// Gemma 4 on the Tensor NPU, which reads 800–2,000 tok/s on a warm phone too (docs/BENCHMARKS.md).
pub const GEMMA4_NPU: ModelProfile = ModelProfile { name: "gemma4-npu", evidence_tokens: 600, warm_evidence_cut: false };

pub fn by_name(name: &str) -> Option<&'static ModelProfile> {
    let all: [&'static ModelProfile; 3] = [&DEFAULT, &GEMMA4, &GEMMA4_NPU];
    all.into_iter().find(|p| p.name == name)
}

/// The declared profile when it is known, else the one the model file's name implies.
pub fn for_model(declared: Option<&str>, file_name: &str) -> &'static ModelProfile {
    if let Some(p) = declared.and_then(by_name) {
        return p;
    }
    let f = file_name.to_ascii_lowercase();
    if f.contains("_google_tensor_") {
        &GEMMA4_NPU
    } else if f.contains("gemma-4") {
        &GEMMA4
    } else {
        &DEFAULT
    }
}
