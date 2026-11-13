//! A DSpark drafter: a small model that reads the target's hidden features and proposes the next tokens in one pass.
//! The same steps as llama.cpp's `common/speculative.cpp`, without the `common` library.

use crate::{LoadOptions, sys};
use anyhow::{Result, ensure};
use std::ffi::{CString, c_char};
use std::path::Path;
use std::ptr;

/// Tokens per draft. On the Pixel 10, 3 beat 2 and 5 (docs/BENCHMARKS.md, 2026-11-06).
pub const DRAFT_LEN: u32 = 3;

pub struct Drafter {
    model: *mut sys::llama_model,
    pub ctx: *mut sys::llama_context,
    /// Target layers whose input the drafter reads.
    layers: Vec<u32>,
    n_embd: usize,
    n_vocab: usize,
    mask: i32,
    block: *mut sys::llama_batch_ext,
    inject: *mut sys::llama_batch_ext,
    /// Holds [last token, draft...] for the target.
    pub verify: *mut sys::llama_batch_ext,
    features: Vec<f32>,
    feeding: bool,
    /// File name and size: a saved state only fits the weights that made it.
    pub key: String,
}

impl Drop for Drafter {
    fn drop(&mut self) {
        unsafe {
            sys::llama_batch_ext_free(self.block);
            sys::llama_batch_ext_free(self.inject);
            sys::llama_batch_ext_free(self.verify);
            sys::llama_free(self.ctx);
            sys::llama_model_free(self.model);
        }
    }
}

impl Drafter {
    pub fn load(path: &Path, target_model: *const sys::llama_model, target: *mut sys::llama_context, opts: &LoadOptions) -> Result<Self> {
        let cpath = CString::new(path.to_string_lossy().as_bytes())?;
        let mut mp = unsafe { sys::llama_model_default_params() };
        mp.load_mode = if opts.in_ram { sys::LLAMA_LOAD_MODE_NONE } else { sys::LLAMA_LOAD_MODE_MMAP };
        let model = unsafe { sys::llama_model_load_from_file(cpath.as_ptr(), mp) };
        ensure!(!model.is_null(), "failed to load drafter {}", path.display());

        let mut cp = unsafe { sys::llama_context_default_params() };
        cp.n_ctx = opts.n_ctx;
        cp.n_batch = 512;
        cp.n_ubatch = 512;
        cp.n_seq_max = 1;
        cp.n_outputs_max = DRAFT_LEN;
        cp.n_outputs_max_per_seq = DRAFT_LEN;
        cp.n_threads = opts.threads as i32;
        cp.n_threads_batch = opts.batch_threads.max(1) as i32;
        cp.ctx_other = target;
        let ctx = unsafe { sys::llama_init_from_model(model, cp) };
        if ctx.is_null() {
            unsafe { sys::llama_model_free(model) };
            anyhow::bail!("failed to create the drafter's context");
        }
        let block = unsafe { sys::llama_batch_ext_init(ctx) };
        let inject = unsafe { sys::llama_batch_ext_init(ctx) };
        let verify = unsafe { sys::llama_batch_ext_init(target) };
        let vocab = unsafe { sys::llama_model_get_vocab(model) };
        let mut ids = ptr::null();
        let n = unsafe { sys::llama_draft_target_layers(model, &mut ids) } as usize;
        let layers = if ids.is_null() { vec![] } else { unsafe { std::slice::from_raw_parts(ids, n) }.iter().map(|&l| l as u32).collect() };
        let name = path.file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_default();
        let d = Self {
            model,
            ctx,
            layers,
            n_embd: unsafe { sys::llama_model_n_embd(target_model) } as usize,
            n_vocab: unsafe { sys::llama_vocab_n_tokens(vocab) } as usize,
            mask: unsafe { sys::llama_vocab_mask(vocab) },
            block,
            inject,
            verify,
            features: Vec::new(),
            feeding: false,
            key: format!("{name} {}", std::fs::metadata(path).map(|m| m.len()).unwrap_or(0)),
        };
        ensure!(!d.layers.is_empty() && d.mask >= 0, "{name} is not a target-feature drafter");
        ensure!(d.meta(c"dflash.sample_from_anchor").as_deref() != Some("false"), "{name} does not draft from its first row");
        let causal = d.meta(c"dflash.attention.causal").as_deref() == Some("true");
        unsafe {
            sys::llama_draft_set_nextn(ctx, true, true);
            sys::llama_set_causal_attn(ctx, causal);
        }
        Ok(d)
    }

    fn meta(&self, key: &std::ffi::CStr) -> Option<String> {
        let mut buf = [0u8; 32];
        let n = unsafe { sys::llama_model_meta_val_str(self.model, key.as_ptr(), buf.as_mut_ptr() as *mut c_char, buf.len()) };
        (n >= 0).then(|| String::from_utf8_lossy(&buf[..(n as usize).min(buf.len())]).into_owned())
    }

    /// Makes the target keep the features that `inject` reads. They cost time, so only drafted answers turn them on.
    pub fn set_feeding(&mut self, target: *mut sys::llama_context, on: bool) {
        if self.feeding != on {
            for &l in &self.layers {
                unsafe { sys::llama_draft_set_features(target, l, on) };
            }
            self.feeding = on;
        }
    }

    pub fn feeding(&self) -> bool {
        self.feeding
    }

    /// Writes the features of the target's last decode (`n` rows, the first at position `pos`) into the drafter's cache.
    pub fn inject(&mut self, target: *mut sys::llama_context, pos: i32, n: usize) -> Result<()> {
        let n_enc = self.layers.len() * self.n_embd;
        self.features.resize(n * n_enc, 0.0);
        for (k, &l) in self.layers.iter().enumerate() {
            let src = unsafe { sys::llama_draft_get_features(target, l) };
            ensure!(!src.is_null(), "the target kept no features for layer {l}");
            let src = unsafe { std::slice::from_raw_parts(src, n * self.n_embd) };
            for (i, row) in src.chunks_exact(self.n_embd).enumerate() {
                self.features[i * n_enc + k * self.n_embd..][..self.n_embd].copy_from_slice(row);
            }
        }
        unsafe {
            sys::llama_batch_ext_clear(self.inject);
            for i in 0..n {
                let e = sys::llama_embd { data: self.features[i * n_enc..].as_ptr(), n_rows: 1, n_embd: n_enc };
                let idx = sys::llama_batch_ext_add_embd(self.inject, 0, e);
                ensure!(idx >= 0, "cannot add features to the batch ({idx})");
                let p = pos + i as i32;
                sys::llama_batch_ext_set_pos(self.inject, idx, &p);
            }
            let rc = sys::llama_process(self.ctx, sys::LLAMA_PROCESS_TYPE_DECODE, self.inject);
            ensure!(rc == 0, "the drafter could not read the features ({rc})");
        }
        Ok(())
    }

    /// Proposes `n` tokens after `last`, which sits at position `pos`. The proposal leaves the drafter's cache unchanged.
    pub fn draft(&mut self, last: i32, pos: i32, n: u32) -> Result<Vec<i32>> {
        if n == 0 {
            return Ok(vec![]);
        }
        unsafe {
            sys::llama_batch_ext_clear(self.block);
            for i in 0..n as i32 {
                let idx = sys::llama_batch_ext_add_token(self.block, 0, if i == 0 { last } else { self.mask });
                ensure!(idx >= 0, "cannot add a token to the draft batch ({idx})");
                let p = pos + i;
                sys::llama_batch_ext_set_pos(self.block, idx, &p);
                sys::llama_batch_ext_set_output_logits(self.block, idx, true);
            }
            let rc = sys::llama_process(self.ctx, sys::LLAMA_PROCESS_TYPE_DECODE, self.block);
            ensure!(rc == 0, "the drafter failed ({rc})");
            let mut draft = Vec::with_capacity(n as usize);
            for i in 0..n as i32 {
                let logits = std::slice::from_raw_parts(sys::llama_get_logits_ith(self.ctx, i), self.n_vocab);
                let tok = logits.iter().enumerate().fold((0, f32::MIN), |b, (t, &l)| if l > b.1 { (t as i32, l) } else { b }).0;
                draft.push(tok);
            }
            self.remove_from(pos)?;
            Ok(draft)
        }
    }

    /// Drops the drafter's cache from `pos` on.
    pub fn remove_from(&self, pos: i32) -> Result<()> {
        let ok = unsafe { sys::llama_memory_seq_rm(sys::llama_get_memory(self.ctx), 0, pos, -1) };
        ensure!(ok, "the drafter cannot drop its cache from {pos}");
        Ok(())
    }
}
