#!/usr/bin/env bash
# Download pinned model files into data/models. Dev-time only: the app never uses the network.
# Usage: scripts/fetch-models.sh [all|encoders|reranker-32m|fastlane|llm-small|llm-fast|llm-lfm|llm-gemma|llm-maple|llm-deep|mxbai|stt]  (all skips the 16 GB deep model)
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=data/models
HF=https://huggingface.co

fetch() { # repo rev path dest
  local url="$HF/$1/resolve/$2/$3" dest="$OUT/$4"
  mkdir -p "$(dirname "$dest")"
  if [[ -s "$dest" ]]; then echo "have $dest"; return; fi
  echo "get  $dest"
  curl -fL --retry 5 -C - -o "$dest.part" "$url"
  mv "$dest.part" "$dest"
}

LEAF=MongoDB/mdbr-leaf-mt;               LEAF_REV=1ed41b22ce166d66c24f88ebfc340e1f03adb20f
ETTIN=cross-encoder/ettin-reranker-17m-v1; ETTIN_REV=9e4aa35321a6dd1a43ca313f500c4b4f7cfb5cc6
ETTIN32=cross-encoder/ettin-reranker-32m-v1; ETTIN32_REV=b33e5ceb5110773ea9cf5e00c9bedc83a8c2afdd
MXBAI=mixedbread-ai/mxbai-embed-large-v1; MXBAI_REV=b33106f585b9ce46904ad7443a3b52b7a63e231c
LFM_S=LiquidAI/LFM2.5-1.2B-Instruct-GGUF; LFM_S_REV=8ed288026e23958ad9dfa92d53ed773a8eee7125
LFM_F=LiquidAI/LFM2.5-8B-A1B-GGUF;        LFM_F_REV=49c14831707011e64d70b2ebd8462ba08d608434
QWEN=bartowski/Qwen_Qwen3.6-35B-A3B-GGUF;  QWEN_REV=5c2410d71524f4f72b023ce8daf7a80528226d5f
LING=bartowski/Ling-3.0-tiny-GGUF;         LING_REV=ea072726af0d2e8ba325b2f90fc0efa762105a91
GEMMA=litert-community/gemma-4-E2B-it-litert-lm; GEMMA_REV=b3ca0d2f076785a8f4b2219ddbd2bdb99954eae1
MAPLE=deepgrove/maple-preview-GGUF;       MAPLE_REV=f5466f918e0c50cdb9d4d47a6f35813509a42a30
STT_URL=https://download.moonshine.ai/model/medium-streaming-en/quantized_26_08_21
READER=tomasmcm/nlpconnect-deberta-v3-xsmall-squad2-onnx; READER_REV=96a6338a770f28b36ace1915a3c22685534ffe1b

encoders() {
  for f in onnx/model.onnx onnx/model.onnx_data onnx/model_quantized.onnx onnx/model_quantized.onnx_data tokenizer.json; do
    fetch $LEAF $LEAF_REV $f leaf-mt/$f
  done
  for f in onnx/model.onnx onnx/model_qint8_arm64.onnx onnx/model_quint8_avx2.onnx tokenizer.json config.json modules.json 1_Pooling/config.json 2_Dense/config.json 2_Dense/model.safetensors 3_LayerNorm/config.json 3_LayerNorm/model.safetensors 4_Dense/config.json 4_Dense/model.safetensors; do
    fetch $ETTIN $ETTIN_REV $f ettin-17m/$f
  done
  # The phone loads an 8-bit weight-only copy: the int8 file above loses ~6 points of NQ hit@1 (docs/BENCHMARKS.md).
  [ -s "$OUT/ettin-17m/onnx/model_nb8.onnx" ] || uv run --project pipeline --extra gpu python -m commonplace_pipeline.rerank nb8 \
    --src "$OUT/ettin-17m/onnx/model.onnx" --dst "$OUT/ettin-17m/onnx/model_nb8.onnx"
}
reranker32() {
  for f in onnx/model.onnx onnx/model_qint8_arm64.onnx onnx/model_quint8_avx2.onnx tokenizer.json config.json modules.json 2_Dense/config.json 2_Dense/model.safetensors 3_LayerNorm/config.json 3_LayerNorm/model.safetensors 4_Dense/config.json 4_Dense/model.safetensors; do
    fetch $ETTIN32 $ETTIN32_REV $f ettin-32m/$f
  done
}
mxbai() {
  for f in onnx/model.onnx tokenizer.json; do fetch $MXBAI $MXBAI_REV $f mxbai/$f; done
}
llm_fast()  { fetch $LING $LING_REV Ling-3.0-tiny-Q4_0.gguf llm/Ling-3.0-tiny-Q4_0.gguf; }
llm_small() { fetch $LFM_S $LFM_S_REV LFM2.5-1.2B-Instruct-Q4_0.gguf llm/LFM2.5-1.2B-Instruct-Q4_0.gguf; }
llm_lfm()   { fetch $LFM_F $LFM_F_REV LFM2.5-8B-A1B-Q4_0.gguf llm/LFM2.5-8B-A1B-Q4_0.gguf; }
llm_deep()  { fetch $QWEN $QWEN_REV Qwen_Qwen3.6-35B-A3B-IQ3_XXS.gguf llm/Qwen_Qwen3.6-35B-A3B-IQ3_XXS.gguf; }
llm_gemma() { # LiteRT-LM: generic CPU/GPU build and the Tensor G5 NPU build (blocked, see docs/BENCHMARKS.md)
  fetch $GEMMA $GEMMA_REV gemma-4-E2B-it.litertlm llm/gemma-4-E2B-it.litertlm
  fetch $GEMMA $GEMMA_REV gemma-4-E2B-it_Google_Tensor_G5.litertlm llm/gemma-4-E2B-it_Google_Tensor_G5.litertlm
}
llm_maple() { fetch $MAPLE $MAPLE_REV maple-preview-TQ1_0-head-Q4_K.gguf llm/maple-preview-TQ1_0-head-Q4_K.gguf; }
fastlane() { # pretrained fast-lane encoder (no fine-tuning): the extractive reader
  for f in onnx/model_int8.onnx onnx/model.onnx tokenizer.json config.json; do fetch $READER $READER_REV $f reader/$f; done
}
stt() { # Moonshine Medium Streaming EN (MIT), the voice-input model
  local f dest
  for f in adapter.ort cross_kv.ort decoder_kv.ort encoder.ort frontend.model.ort frontend.weights.ort streaming_config.json tokenizer.bin; do
    dest="$OUT/stt/moonshine-medium-streaming-en/$f"
    mkdir -p "$(dirname "$dest")"
    if [[ -s "$dest" ]]; then echo "have $dest"; continue; fi
    echo "get  $dest"
    curl -fL --retry 5 -C - -o "$dest.part" "$STT_URL/$f"
    mv "$dest.part" "$dest"
  done
}
for target in "${@:-all}"; do
  case "$target" in
    encoders) encoders ;;
    mxbai) mxbai ;;
    reranker-32m) reranker32 ;;
    llm-small) llm_small ;;
    llm-fast) llm_fast ;;
    llm-deep) llm_deep ;;
    llm-lfm) llm_lfm ;;
    llm-gemma) llm_gemma ;;
    llm-maple) llm_maple ;;
    fastlane) fastlane ;;
    stt) stt ;;
    all) encoders; llm_small; mxbai; llm_fast ;;
    *) echo "unknown target $target" >&2; exit 2 ;;
  esac
done
(cd "$OUT" && find . -type f ! -name '*.part' ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
