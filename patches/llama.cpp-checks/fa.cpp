// Scratch check: ggml_flash_attn_ext (CPU) against a double reference built from the same F16 K/V and F32 Q.
// Usage: fa <n_kv> <n_queries> <causal 0|1>
#include "ggml.h"
#include "ggml-cpu.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <vector>

int main(int argc, char ** argv) {
    const int NKV = argc > 1 ? atoi(argv[1]) : 100, NQ = argc > 2 ? atoi(argv[2]) : 64, causal = argc > 3 ? atoi(argv[3]) : 0;
    const int D = 128, H = 4, HKV = 2;
    std::mt19937 rng(7);
    std::normal_distribution<float> nd(0.0f, 1.0f);
    ggml_init_params ip = { size_t(1) << 30, nullptr, false };
    ggml_context * ctx = ggml_init(ip);
    ggml_tensor * q = ggml_new_tensor_4d(ctx, GGML_TYPE_F32, D, NQ, H, 1);
    ggml_tensor * k = ggml_new_tensor_4d(ctx, GGML_TYPE_F16, D, NKV, HKV, 1);
    ggml_tensor * v = ggml_new_tensor_4d(ctx, GGML_TYPE_F16, D, NKV, HKV, 1);
    ggml_tensor * m = ggml_new_tensor_4d(ctx, GGML_TYPE_F16, NKV, NQ, 1, 1);
    std::vector<float> qf(size_t(D) * NQ * H), kf(size_t(D) * NKV * HKV), vf(size_t(D) * NKV * HKV), mf(size_t(NKV) * NQ);
    for (auto & x : qf) x = nd(rng);
    for (auto & x : kf) x = ggml_fp16_to_fp32(ggml_fp32_to_fp16(nd(rng)));
    for (auto & x : vf) x = ggml_fp16_to_fp32(ggml_fp32_to_fp16(nd(rng)));
    for (int i = 0; i < NQ; i++) for (int j = 0; j < NKV; j++)  // causal: query i sees keys up to NKV-NQ+i
        mf[size_t(i) * NKV + j] = (causal && j > NKV - NQ + i) ? -INFINITY : 0.0f;
    memcpy(q->data, qf.data(), qf.size() * 4);
    for (size_t i = 0; i < kf.size(); i++) ((ggml_fp16_t *) k->data)[i] = ggml_fp32_to_fp16(kf[i]);
    for (size_t i = 0; i < vf.size(); i++) ((ggml_fp16_t *) v->data)[i] = ggml_fp32_to_fp16(vf[i]);
    for (size_t i = 0; i < mf.size(); i++) ((ggml_fp16_t *) m->data)[i] = ggml_fp32_to_fp16(mf[i]);
    const float scale = 1.0f / sqrtf(float(D));
    ggml_tensor * out = ggml_flash_attn_ext(ctx, q, k, v, m, scale, 0.0f, 0.0f);
    ggml_flash_attn_ext_set_prec(out, GGML_PREC_F32);
    ggml_cgraph * gf = ggml_new_graph(ctx);
    ggml_build_forward_expand(gf, out);
    ggml_graph_compute_with_ctx(ctx, gf, 4);
    // out: [D, H, NQ, 1]
    double max_err = 0, max_ref = 0;
    for (int h = 0; h < H; h++) {
        const int hk = h / (H / HKV);
        for (int i = 0; i < NQ; i++) {
            std::vector<double> s(NKV);
            double mx = -INFINITY;
            for (int j = 0; j < NKV; j++) {
                double d = 0;
                for (int c = 0; c < D; c++) d += double(qf[(size_t(h) * NQ + i) * D + c]) * kf[(size_t(hk) * NKV + j) * D + c];
                s[j] = d * scale + mf[size_t(i) * NKV + j];
                mx = std::max(mx, s[j]);
            }
            double sum = 0;
            for (int j = 0; j < NKV; j++) { s[j] = std::isinf(s[j]) ? 0 : std::exp(s[j] - mx); sum += s[j]; }
            for (int c = 0; c < D; c++) {
                double r = 0;
                for (int j = 0; j < NKV; j++) r += s[j] * vf[(size_t(hk) * NKV + j) * D + c];
                r /= sum;
                const double got = ((float *) out->data)[(size_t(i) * H + h) * D + c];
                max_err = std::max(max_err, std::fabs(got - r));
                max_ref = std::max(max_ref, std::fabs(r));
            }
        }
    }
    printf("n_kv %5d n_q %3d causal %d  max |err| %.3e  (max |ref| %.3f)\n", NKV, NQ, causal, max_err, max_ref);
    ggml_free(ctx);
    return 0;
}
