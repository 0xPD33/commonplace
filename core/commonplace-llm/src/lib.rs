//! llama.cpp backend: minimal bindgen FFI, cached system-prompt state, GBNF grammars, streaming.

#![allow(non_upper_case_globals, non_camel_case_types, non_snake_case, dead_code, unnecessary_transmutes, clippy::all)]
mod sys {
    include!(concat!(env!("OUT_DIR"), "/bindings.rs"));
}

use anyhow::{Context, Result, bail, ensure};
use commonplace_core::llm::{GenParams, GenStats, LlmBackend};
use std::collections::HashMap;
use std::ffi::{CStr, CString};
use std::path::Path;
use std::ptr;
use std::sync::{Mutex, Once};
use std::time::Instant;

static INIT: Once = Once::new();

fn init() {
    INIT.call_once(|| unsafe {
        sys::llama_log_set(Some(log_cb), ptr::null_mut());
        sys::llama_backend_init();
    });
}

unsafe extern "C" fn log_cb(level: sys::ggml_log_level, text: *const std::os::raw::c_char, _: *mut std::os::raw::c_void) {
    if text.is_null() || !(level == sys::GGML_LOG_LEVEL_WARN || level == sys::GGML_LOG_LEVEL_ERROR) {
        return;
    }
    let s = unsafe { CStr::from_ptr(text) }.to_string_lossy();
    log::warn!("llama: {}", s.trim_end());
}

/// llama.cpp CPU feature string, e.g. "CPU : NEON = 1 | ARM_FMA = 1 | DOTPROD = 1 | ...".
pub fn system_info() -> String {
    init();
    unsafe { CStr::from_ptr(sys::llama_print_system_info()) }.to_string_lossy().into_owned()
}

/// On arm64 the fast Q4_0 kernels need DOTPROD and MATMUL_INT8 (PLAN.md §4.1 build trap).
pub fn check_cpu_features() -> Result<()> {
    if cfg!(target_arch = "aarch64") {
        let info = system_info();
        for f in ["DOTPROD = 1", "MATMUL_INT8 = 1"] {
            ensure!(info.contains(f), "llama.cpp built without {f}: {info}");
        }
    }
    Ok(())
}

/// CPUs whose capacity is at least half of the biggest core (X + A7xx on Tensor; skips A5xx).
pub fn big_cores() -> Vec<usize> {
    let caps: Vec<(usize, u32)> = (0..64)
        .filter_map(|i| {
            std::fs::read_to_string(format!("/sys/devices/system/cpu/cpu{i}/cpu_capacity")).ok().and_then(|s| s.trim().parse().ok()).map(|c| (i, c))
        })
        .collect();
    let max = caps.iter().map(|c| c.1).max().unwrap_or(0);
    caps.into_iter().filter(|c| c.1 * 2 >= max).map(|c| c.0).collect()
}

/// Chat-template text that switches reasoning off (`off_*`) or on (`on_*`): appended to the system
/// message and after the assistant header.
#[derive(Clone, Copy)]
struct ThinkStyle {
    off_system: &'static str,
    off_open: &'static str,
    on_system: &'static str,
    on_open: &'static str,
}

struct Cached {
    tokens: usize,
    state: Vec<u8>,
}

struct Inner {
    model: *mut sys::llama_model,
    ctx: *mut sys::llama_context,
    threadpool: *mut sys::ggml_threadpool,
    batch_pool: *mut sys::ggml_threadpool,
    cache: HashMap<String, Cached>,
    /// How the chat template switches reasoning on and off; `None` for models without a thinking mode.
    think: Option<ThinkStyle>,
    /// `</think>` as one special token, when the vocabulary has it.
    think_end: Option<i32>,
    batch_threads: u32,
    n_batch: usize,
}

// SAFETY: all access to the raw pointers goes through the Mutex in LlamaBackend.
unsafe impl Send for Inner {}

impl Drop for Inner {
    fn drop(&mut self) {
        unsafe {
            if !self.ctx.is_null() {
                sys::llama_free(self.ctx);
            }
            for p in [self.threadpool, self.batch_pool] {
                if !p.is_null() {
                    sys::ggml_threadpool_free(p);
                }
            }
            if !self.model.is_null() {
                sys::llama_model_free(self.model);
            }
        }
    }
}

pub struct LlamaBackend {
    id: String,
    inner: Mutex<Inner>,
}

pub struct LoadOptions {
    pub n_ctx: u32,
    /// Decode threads. Decode is memory-bound: on Tensor G5, 4 beat 6 (25 vs 17 tok/s).
    pub threads: u32,
    /// Prefill threads. Prefill is compute-bound: 6 beat 4 (120 vs 102 tok/s).
    pub batch_threads: u32,
    /// Pin threads to these CPUs (empty: OS default).
    pub cpus: Vec<usize>,
    /// Read the weights into RAM (fast model). Memory-mapping plus weight repacking keeps the file
    /// mapped next to the repacked copy: 7.0 GB peak instead of 4.9 GB. The deep model streams (mmap).
    pub in_ram: bool,
}

impl LlamaBackend {
    pub fn load(path: &Path, opts: &LoadOptions) -> Result<Self> {
        init();
        let cpath = CString::new(path.to_string_lossy().as_bytes())?;
        let mut mp = unsafe { sys::llama_model_default_params() };
        mp.load_mode = if opts.in_ram { sys::LLAMA_LOAD_MODE_NONE } else { sys::LLAMA_LOAD_MODE_MMAP };
        let model = unsafe { sys::llama_model_load_from_file(cpath.as_ptr(), mp) };
        ensure!(!model.is_null(), "failed to load model {}", path.display());

        let mut cp = unsafe { sys::llama_context_default_params() };
        cp.n_ctx = opts.n_ctx;
        cp.n_batch = 512;
        cp.n_ubatch = 512;
        cp.n_seq_max = 1;
        cp.n_threads = opts.threads as i32;
        cp.n_threads_batch = opts.batch_threads.max(1) as i32;
        let ctx = unsafe { sys::llama_init_from_model(model, cp) };
        if ctx.is_null() {
            unsafe { sys::llama_model_free(model) };
            bail!("failed to create llama context");
        }
        let (mut threadpool, mut batch_pool) = (ptr::null_mut(), ptr::null_mut());
        if !opts.cpus.is_empty() {
            let pool = |n: u32| unsafe {
                let mut tp = std::mem::zeroed::<sys::ggml_threadpool_params>();
                tp.n_threads = n as i32;
                tp.prio = sys::GGML_SCHED_PRIO_NORMAL;
                tp.poll = 50;
                tp.strict_cpu = false;
                for &c in &opts.cpus {
                    if c < tp.cpumask.len() {
                        tp.cpumask[c] = true;
                    }
                }
                sys::ggml_threadpool_new(&mut tp)
            };
            threadpool = pool(opts.threads);
            batch_pool = pool(opts.batch_threads.max(1));
            if !threadpool.is_null() && !batch_pool.is_null() {
                unsafe { sys::llama_attach_threadpool(ctx, threadpool, batch_pool) };
            }
        }
        let mut desc = vec![0u8; 256];
        let n = unsafe { sys::llama_model_desc(model, desc.as_mut_ptr() as *mut _, desc.len()) };
        desc.truncate(n.max(0) as usize);
        let desc = String::from_utf8_lossy(&desc).into_owned();
        let name = path.file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_default();
        // Reasoning models (LFM2.5-8B-A1B, Qwen3.x) skip thinking when handed an empty think block.
        // Ling 3.0 also needs "detailed thinking off" in the system turn; llama.cpp maps its template to Ling 2.0's.
        let tmpl = unsafe { sys::llama_model_chat_template(model, ptr::null()) };
        let tmpl = if tmpl.is_null() { String::new() } else { unsafe { CStr::from_ptr(tmpl) }.to_string_lossy().into_owned() };
        let think = if tmpl.contains("detailed thinking") {
            Some(ThinkStyle { off_system: "\ndetailed thinking off", off_open: "\n<think></think>", on_system: "\ndetailed thinking on", on_open: "\n<think>" })
        } else if tmpl.contains("</think>") {
            Some(ThinkStyle { off_system: "", off_open: "<think>\n\n</think>\n\n", on_system: "", on_open: "<think>\n" })
        } else {
            None
        };
        let mut inner = Inner { model, ctx, threadpool, batch_pool, cache: HashMap::new(), think, think_end: None, batch_threads: opts.batch_threads.max(1), n_batch: 512 };
        inner.think_end = inner.tokenize("</think>", false).ok().filter(|t| t.len() == 1).map(|t| t[0]);
        if opts.in_ram {
            // One tiny decode warms the kernels.
            let _ = inner.decode_tokens(&[inner.bos()], 0);
            unsafe { sys::llama_memory_clear(sys::llama_get_memory(ctx), true) };
        }
        Ok(Self { id: format!("{name} ({desc})"), inner: Mutex::new(inner) })
    }
}

impl Inner {
    fn vocab(&self) -> *const sys::llama_vocab {
        unsafe { sys::llama_model_get_vocab(self.model) }
    }

    fn bos(&self) -> i32 {
        unsafe { sys::llama_vocab_bos(self.vocab()) }
    }

    fn tokenize(&self, text: &str, add_special: bool) -> Result<Vec<i32>> {
        let n_max = text.len() as i32 + 16;
        let mut toks = vec![0i32; n_max as usize];
        let n = unsafe {
            sys::llama_tokenize(self.vocab(), text.as_ptr() as *const _, text.len() as i32, toks.as_mut_ptr(), n_max, add_special, true)
        };
        ensure!(n >= 0, "tokenize failed");
        toks.truncate(n as usize);
        Ok(toks)
    }

    fn piece(&self, tok: i32, out: &mut Vec<u8>) {
        let mut buf = [0u8; 256];
        let n = unsafe { sys::llama_token_to_piece(self.vocab(), tok, buf.as_mut_ptr() as *mut _, buf.len() as i32, 0, false) };
        if n > 0 {
            out.extend_from_slice(&buf[..n as usize]);
        }
    }

    fn apply_template(&self, msgs: &[(&str, &str)], add_ass: bool) -> Result<String> {
        let tmpl = unsafe { sys::llama_model_chat_template(self.model, ptr::null()) };
        let roles: Vec<CString> = msgs.iter().map(|m| CString::new(m.0).unwrap()).collect();
        let contents: Vec<CString> = msgs.iter().map(|m| CString::new(m.1.replace('\0', "")).unwrap()).collect();
        let chat: Vec<sys::llama_chat_message> =
            roles.iter().zip(&contents).map(|(r, c)| sys::llama_chat_message { role: r.as_ptr(), content: c.as_ptr() }).collect();
        let mut buf = vec![0u8; msgs.iter().map(|m| m.1.len()).sum::<usize>() * 2 + 512];
        let mut n = unsafe { sys::llama_chat_apply_template(tmpl, chat.as_ptr(), chat.len(), add_ass, buf.as_mut_ptr() as *mut _, buf.len() as i32) };
        if n > buf.len() as i32 {
            buf.resize(n as usize, 0);
            n = unsafe { sys::llama_chat_apply_template(tmpl, chat.as_ptr(), chat.len(), add_ass, buf.as_mut_ptr() as *mut _, buf.len() as i32) };
        }
        if n < 0 {
            // Unknown template: fall back to ChatML, which LFM2 and Qwen use.
            let mut s = String::new();
            for (r, c) in msgs {
                s.push_str(&format!("<|im_start|>{r}\n{c}<|im_end|>\n"));
            }
            if add_ass {
                s.push_str("<|im_start|>assistant\n");
            }
            return Ok(s);
        }
        buf.truncate(n as usize);
        Ok(String::from_utf8_lossy(&buf).into_owned())
    }

    /// Decode tokens starting at position `pos`, in n_batch chunks.
    fn decode_tokens(&mut self, toks: &[i32], _pos: usize) -> Result<()> {
        for chunk in toks.chunks(self.n_batch) {
            let mut c = chunk.to_vec();
            let batch = unsafe { sys::llama_batch_get_one(c.as_mut_ptr(), c.len() as i32) };
            let rc = unsafe { sys::llama_decode(self.ctx, batch) };
            ensure!(rc == 0, "llama_decode failed ({rc})");
        }
        Ok(())
    }

    fn sampler(&self, p: &GenParams) -> Result<*mut sys::llama_sampler> {
        unsafe {
            let chain = sys::llama_sampler_chain_init(sys::llama_sampler_chain_default_params());
            if let Some(g) = &p.grammar {
                let gs = CString::new(g.as_str())?;
                let root = CString::new("root")?;
                let s = sys::llama_sampler_init_grammar(self.vocab(), gs.as_ptr(), root.as_ptr());
                if s.is_null() {
                    sys::llama_sampler_free(chain);
                    bail!("invalid grammar");
                }
                sys::llama_sampler_chain_add(chain, s);
            }
            if p.repeat_penalty != 1.0 {
                sys::llama_sampler_chain_add(chain, sys::llama_sampler_init_penalties(sys::llama_vocab_n_tokens(self.vocab()), 64, p.repeat_penalty, 0.0, 0.0));
            }
            if p.temperature <= 0.0 {
                sys::llama_sampler_chain_add(chain, sys::llama_sampler_init_greedy());
            } else {
                sys::llama_sampler_chain_add(chain, sys::llama_sampler_init_top_k(40));
                sys::llama_sampler_chain_add(chain, sys::llama_sampler_init_top_p(p.top_p, 1));
                sys::llama_sampler_chain_add(chain, sys::llama_sampler_init_temp(p.temperature));
                sys::llama_sampler_chain_add(chain, sys::llama_sampler_init_dist(0x5eed));
            }
            Ok(chain)
        }
    }

    fn generate(&mut self, system: &str, user: &str, p: &GenParams, on_token: &mut dyn FnMut(&str) -> bool) -> Result<GenStats> {
        let mut st = GenStats::default();
        // Thinking runs only when asked for and the model has a thinking mode.
        let budget = p.think_budget.filter(|_| self.think.is_some());
        let (sys_suffix, open) = match (self.think, budget) {
            (Some(t), Some(_)) => (t.on_system, t.on_open),
            (Some(t), None) => (t.off_system, t.off_open),
            (None, _) => ("", ""),
        };
        let system = format!("{system}{sys_suffix}");
        let prefix = self.apply_template(&[("system", &system)], false)?;
        let mut full = self.apply_template(&[("system", &system), ("user", user)], true)?;
        full.push_str(open);
        let mem = unsafe { sys::llama_get_memory(self.ctx) };
        unsafe { sys::llama_memory_clear(mem, true) };

        let t = Instant::now();
        let suffix_toks = if let Some(rest) = full.strip_prefix(prefix.as_str()) {
            let restored = match self.cache.get(system.as_str()) {
                Some(c) => {
                    let ok = unsafe { sys::llama_state_seq_set_data(self.ctx, c.state.as_ptr(), c.state.len(), 0) } > 0;
                    if ok { Some(c.tokens) } else { None }
                }
                None => None,
            };
            match restored {
                Some(n) => st.cached_tokens = n as u32,
                None => {
                    unsafe { sys::llama_memory_clear(mem, true) };
                    let ptoks = self.tokenize(&prefix, true)?;
                    self.decode_tokens(&ptoks, 0)?;
                    let size = unsafe { sys::llama_state_seq_get_size(self.ctx, 0) };
                    let mut state = vec![0u8; size];
                    let got = unsafe { sys::llama_state_seq_get_data(self.ctx, state.as_mut_ptr(), size, 0) };
                    state.truncate(got);
                    if self.cache.len() >= 6 {
                        self.cache.clear();
                    }
                    self.cache.insert(system.clone(), Cached { tokens: ptoks.len(), state });
                    st.prompt_tokens += ptoks.len() as u32;
                }
            }
            st.prompt_tokens += st.cached_tokens;
            self.tokenize(rest, false)?
        } else {
            self.tokenize(&full, true)?
        };
        let n_ctx = unsafe { sys::llama_n_ctx(self.ctx) } as usize;
        ensure!(st.prompt_tokens as usize + suffix_toks.len() + ((p.max_tokens + budget.unwrap_or(0)) as usize) < n_ctx, "prompt too long for n_ctx {n_ctx}");
        self.decode_tokens(&suffix_toks, 0)?;
        st.prompt_tokens += suffix_toks.len() as u32;
        st.prefill_ms = t.elapsed().as_secs_f64() * 1000.0;

        let smpl = self.sampler(p)?;
        let t = Instant::now();
        let mut pending: Vec<u8> = Vec::new();
        // Reasoning is streamed between "<think>" and "</think>" so the caller can show it apart.
        let mut thinking = budget.is_some();
        let mut tail = String::new();
        if thinking {
            on_token("<think>");
        }
        // Answer tokens since the reasoning last closed. A model that keeps reasoning after a forced close and
        // then writes its own "</think>" gets its full answer budget after that (the caller drops the text before it).
        let mut answer_tokens = 0u32;
        let result = (|| -> Result<()> {
            for _ in 0..p.max_tokens + 2 * budget.unwrap_or(0) {
                let tok = unsafe { sys::llama_sampler_sample(smpl, self.ctx, -1) };
                if unsafe { sys::llama_vocab_is_eog(self.vocab(), tok) } {
                    return Ok(());
                }
                st.gen_tokens += 1;
                if !thinking {
                    if budget.is_some() && (Some(tok) == self.think_end || tail.contains("</think>")) {
                        answer_tokens = 0;
                        tail.clear();
                    }
                    answer_tokens += 1;
                    if answer_tokens > p.max_tokens {
                        st.truncated = true;
                        return Ok(());
                    }
                }
                if thinking {
                    st.think_tokens += 1;
                    if Some(tok) == self.think_end || tail.contains("</think>") {
                        thinking = false;
                        if Some(tok) == self.think_end {
                            // Announce the end once, whether or not the token renders as text.
                            if !on_token("</think>") {
                                return Ok(()); // cancelled
                            }
                            self.decode_tokens(&[tok], 0)?;
                            continue;
                        }
                    } else if st.think_tokens >= budget.unwrap_or(0) {
                        // Over budget: close the reasoning ourselves and let the model answer. A bare "</think>" is
                        // often ignored (the model keeps reasoning in the answer); a closing sentence is followed.
                        thinking = false;
                        let close = self.tokenize("\n\nI have to give the answer now, based on the reasoning so far.\n</think>\n\n", false)?;
                        self.decode_tokens(&close, 0)?;
                        if !on_token("</think>") {
                            return Ok(()); // cancelled
                        }
                        continue;
                    }
                }
                self.piece(tok, &mut pending);
                let valid = match std::str::from_utf8(&pending) {
                    Ok(_) => pending.len(),
                    Err(e) if e.error_len().is_none() => e.valid_up_to(),
                    Err(_) => pending.len(),
                };
                if valid > 0 {
                    let s = String::from_utf8_lossy(&pending[..valid]).into_owned();
                    pending.drain(..valid);
                    if budget.is_some() {
                        // Enough text to spot a "</think>" written as several ordinary tokens.
                        tail.push_str(&s);
                        let cut = tail.len().saturating_sub(16);
                        tail = tail[tail.ceil_char_boundary(cut)..].to_string();
                    }
                    if !on_token(&s) {
                        return Ok(()); // cancelled
                    }
                }
                let mut one = [tok];
                let batch = unsafe { sys::llama_batch_get_one(one.as_mut_ptr(), 1) };
                let rc = unsafe { sys::llama_decode(self.ctx, batch) };
                ensure!(rc == 0, "llama_decode failed ({rc})");
            }
            st.truncated = true; // ran out of tokens before the end token
            Ok(())
        })();
        unsafe { sys::llama_sampler_free(smpl) };
        result?;
        st.decode_ms = t.elapsed().as_secs_f64() * 1000.0;
        Ok(st)
    }
}

impl LlmBackend for LlamaBackend {
    fn id(&self) -> String {
        self.id.clone()
    }

    fn generate(&self, system: &str, user: &str, params: &GenParams, on_token: &mut dyn FnMut(&str) -> bool) -> Result<GenStats> {
        self.inner.lock().unwrap().generate(system, user, params, on_token).context("llama generate")
    }

    /// Decode threads only: prefill keeps its own (larger) count from `LoadOptions::batch_threads`.
    fn set_threads(&self, n: u32) {
        let g = self.inner.lock().unwrap();
        unsafe { sys::llama_set_n_threads(g.ctx, n as i32, g.batch_threads as i32) };
    }
}
