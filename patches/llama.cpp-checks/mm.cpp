// Scratch check: Q5_0 x F32 mul_mat through ggml-cpu against a double reference built from the same quantized
// inputs (weights dequantized from Q5_0, activations quantized to Q8_0 and dequantized, as the CPU path does).
#include "ggml.h"
#include "ggml-cpu.h"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <random>
#include <vector>

int main(int argc, char ** argv) {
    const int K = 6144, N = 512, M = argc > 1 ? atoi(argv[1]) : 13;  // like MiniCPM5 ffn_down, M tokens
    std::mt19937 rng(42);
    std::normal_distribution<float> nd(0.0f, 1.0f);
    std::vector<float> w(size_t(K) * N), x(size_t(K) * M);
    for (auto & v : w) v = nd(rng) * 0.02f;
    for (auto & v : x) v = nd(rng);

    ggml_init_params ip = { size_t(1) << 30, nullptr, false };
    ggml_context * ctx = ggml_init(ip);
    ggml_tensor * W = ggml_new_tensor_2d(ctx, GGML_TYPE_Q5_0, K, N);
    ggml_tensor * X = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, K, M);
    ggml_quantize_chunk(GGML_TYPE_Q5_0, w.data(), W->data, 0, N, K, nullptr);
    memcpy(X->data, x.data(), x.size() * sizeof(float));
    ggml_tensor * Y = ggml_mul_mat(ctx, W, X);
    ggml_cgraph * gf = ggml_new_graph(ctx);
    ggml_build_forward_expand(gf, Y);
    ggml_graph_compute_with_ctx(ctx, gf, 4);

    // reference: dequantized weights, activations through Q8_0
    const auto * tq5 = ggml_get_type_traits(GGML_TYPE_Q5_0);
    const auto * tq8 = ggml_get_type_traits(GGML_TYPE_Q8_0);
    std::vector<float> wd(size_t(K) * N), xd(size_t(K) * M);
    for (int n = 0; n < N; n++) tq5->to_float((const char *) W->data + n * W->nb[1], wd.data() + size_t(n) * K, K);
    std::vector<char> q8(ggml_row_size(GGML_TYPE_Q8_0, K));
    for (int m = 0; m < M; m++) {
        ggml_quantize_chunk(GGML_TYPE_Q8_0, x.data() + size_t(m) * K, q8.data(), 0, 1, K, nullptr);
        tq8->to_float(q8.data(), xd.data() + size_t(m) * K, K);
    }
    double max_rel = 0, sum_rel = 0, max_abs = 0;
    for (int m = 0; m < M; m++) {
        for (int n = 0; n < N; n++) {
            double ref = 0, mag = 0;
            for (int k = 0; k < K; k++) {
                ref += double(wd[size_t(n) * K + k]) * xd[size_t(m) * K + k];
                mag += std::fabs(double(wd[size_t(n) * K + k]) * xd[size_t(m) * K + k]);
            }
            const double got = ((float *) Y->data)[size_t(m) * N + n];
            const double rel = std::fabs(got - ref) / mag;  // error relative to the sum of magnitudes
            max_rel = std::max(max_rel, rel);
            sum_rel += rel;
            max_abs = std::max(max_abs, std::fabs(got - ref));
        }
    }
    printf("M=%d  max |err| %.3e  max rel %.3e  mean rel %.3e\n", M, max_abs, max_rel, sum_rel / (double(M) * N));
    ggml_free(ctx);
    return 0;
}
