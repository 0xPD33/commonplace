"""Distil a large cross-encoder into the phone reranker (Ettin-17m) on our own retrieval pools.

Pools come from `commonplace pool` (one JSON line per query: the fused hits with the reranker's input text).

  score   teacher logits for every (query, hit) of a pool file; resumable, appends to --out
  hitk    answer-hit@k (DPR style: a normalized answer string occurs in the passage) for fusion order,
          the pool's own rerank scores, and any score files
  train   listwise distillation (KL between teacher and student softmax over each pool)
  export  onnx/model.onnx (transformer only, as the Rust core expects) + int8 variants + head weights

The student is the Rust reranker layout: ModernBERT → CLS → Dense 256 (GELU, no bias) → LayerNorm → Dense 1.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import time
import unicodedata
from pathlib import Path

import numpy as np

STUDENT = "cross-encoder/ettin-reranker-17m-v1"
STUDENT_REV = "9e4aa35321a6dd1a43ca313f500c4b4f7cfb5cc6"
MAX_LEN = 256  # RERANK_MAX_TOKENS in commonplace-core


def read_jsonl(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).lower()
    return " " + " ".join(re.sub(r"[^\w\s]", " ", s).split()) + " "


# ---------------------------------------------------------------- score


def cmd_score(a) -> None:
    import torch
    from sentence_transformers import CrossEncoder

    out = Path(a.out)
    done = {json.loads(l)["id"] for l in out.open()} if out.exists() else set()
    pools = [p for p in read_jsonl(a.pools) if p["id"] not in done]
    ce = CrossEncoder(a.model, max_length=a.max_len, activation_fn=torch.nn.Identity(), model_kwargs={"dtype": torch.float16})
    t0, n = time.time(), 0
    with out.open("a") as f:
        for i in range(0, len(pools), a.chunk):
            chunk = pools[i : i + a.chunk]
            pairs = [(p["query"], h["text"]) for p in chunk for h in p["hits"]]
            s = ce.predict(pairs, batch_size=a.batch, show_progress_bar=False).tolist() if pairs else []
            k = 0
            for p in chunk:
                f.write(json.dumps({"id": p["id"], "scores": s[k : k + len(p["hits"])]}) + "\n")
                k += len(p["hits"])
            f.flush()
            n += len(pairs)
            print(f"{i + len(chunk)}/{len(pools)} queries, {n / (time.time() - t0):.0f} pairs/s", flush=True)


# ---------------------------------------------------------------- hitk


def hit_table(pools: list[dict], answers: dict, orders: dict[str, list[list[int]]], ks=(1, 3, 5, 10, 40)) -> None:
    print(f"{'order':32s} " + " ".join(f"hit@{k:<3d}" for k in ks) + f"   (n={len(pools)})")
    for name, order in orders.items():
        hits = np.zeros(len(ks))
        for p, o in zip(pools, order):
            ans = [norm(x) for x in answers[p["id"]]]
            ok = [any(x in norm(p["hits"][j]["text"]) for x in ans) for j in o]
            hits += [any(ok[:k]) for k in ks]
        print(f"{name:32s} " + " ".join(f"{h / len(pools):7.3f}" for h in hits))


def argsort_desc(v: list[float]) -> list[int]:
    return sorted(range(len(v)), key=lambda j: -v[j])


def cmd_hitk(a) -> None:
    pools = read_jsonl(a.pools)[a.offset : a.offset + a.limit if a.limit else None]
    answers = {r["id"]: r["answers"] for r in read_jsonl(a.answers)}
    orders = {
        "fusion": [list(range(len(p["hits"]))) for p in pools],
        "pool rerank (installed)": [argsort_desc([h["rerank"] or 0 for h in p["hits"]]) for p in pools],
    }
    for sf in a.scores or []:
        s = {r["id"]: r["scores"] for r in read_jsonl(sf)}
        keep = [p for p in pools if p["id"] in s]
        if len(keep) < len(pools):
            print(f"{sf}: scores for {len(keep)} of {len(pools)} queries; table uses all queries with fusion order for the rest")
        orders[Path(sf).stem] = [argsort_desc(s[p["id"]]) if p["id"] in s else list(range(len(p["hits"]))) for p in pools]
    hit_table(pools, answers, orders)


# ---------------------------------------------------------------- student


class Student:
    def __init__(self, src: str, device: str = "cuda"):
        import torch
        from safetensors.torch import load_file
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.src = src
        self.tok = AutoTokenizer.from_pretrained(src)
        self.backbone = AutoModel.from_pretrained(src).to(device)
        d = self.backbone.config.hidden_size
        self.dense1 = torch.nn.Linear(d, d, bias=False)
        self.norm = torch.nn.LayerNorm(d, eps=1e-5)
        self.dense2 = torch.nn.Linear(d, 1)
        self.dense1.load_state_dict({"weight": load_file(f"{src}/2_Dense/model.safetensors")["linear.weight"]})
        self.norm.load_state_dict({k.split(".")[-1]: v for k, v in load_file(f"{src}/3_LayerNorm/model.safetensors").items()})
        self.dense2.load_state_dict({k.split(".")[-1]: v for k, v in load_file(f"{src}/4_Dense/model.safetensors").items()})
        self.head = torch.nn.Sequential(self.dense1, torch.nn.GELU(), self.norm, self.dense2).to(device)
        self.device = device

    def params(self):
        return list(self.backbone.parameters()) + list(self.head.parameters())

    def logits(self, query_docs: list[tuple[str, str]]):
        enc = self.tok([q for q, _ in query_docs], [d for _, d in query_docs], truncation="only_second",
                       max_length=MAX_LEN, padding=True, return_tensors="pt").to(self.device)
        h = self.backbone(input_ids=enc["input_ids"], attention_mask=enc["attention_mask"]).last_hidden_state[:, 0]
        return self.head(h.float()).squeeze(-1)

    def score_pools(self, pools: list[dict], batch: int = 64) -> list[list[float]]:
        torch = self.torch
        self.backbone.eval()
        self.head.eval()
        pairs = [(p["query"], h["text"]) for p in pools for h in p["hits"]]
        out: list[float] = []
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            for i in range(0, len(pairs), batch):
                out += self.logits(pairs[i : i + batch]).float().cpu().tolist()
        res, k = [], 0
        for p in pools:
            res.append(out[k : k + len(p["hits"])])
            k += len(p["hits"])
        return res

    def save(self, out: Path) -> None:
        from safetensors.torch import save_file

        out.mkdir(parents=True, exist_ok=True)
        self.backbone.save_pretrained(out)
        self.tok.save_pretrained(out)
        for d in ["1_Pooling", "2_Dense", "3_LayerNorm", "4_Dense"]:
            (out / d).mkdir(exist_ok=True)
            shutil.copy(f"{self.src}/{d}/config.json", out / d / "config.json")
        # Without the two sentence-transformers configs, CrossEncoder() treats the directory as a plain
        # HF model and adds a fresh, random classifier head.
        for f in ["modules.json", "config_sentence_transformers.json", "sentence_bert_config.json"]:
            shutil.copy(f"{self.src}/{f}", out / f)
        c = lambda t: t.detach().cpu().contiguous()
        save_file({"linear.weight": c(self.dense1.weight)}, out / "2_Dense/model.safetensors")
        save_file({"norm.weight": c(self.norm.weight), "norm.bias": c(self.norm.bias)}, out / "3_LayerNorm/model.safetensors")
        save_file({"linear.weight": c(self.dense2.weight), "linear.bias": c(self.dense2.bias)}, out / "4_Dense/model.safetensors")


def student_src(init: str) -> str:
    if Path(init).is_dir():
        return init
    from huggingface_hub import snapshot_download

    return snapshot_download(init, revision=STUDENT_REV if init == STUDENT else None)


def dev_hit1(st: Student, dev: list[dict], answers: dict) -> float:
    ok = 0
    for p, s in zip(dev, st.score_pools(dev)):
        best = p["hits"][max(range(len(s)), key=s.__getitem__)]["text"] if s else ""
        ok += any(norm(x) in norm(best) for x in answers[p["id"]])
    return ok / len(dev)


def cmd_train(a) -> None:
    import torch

    random.seed(0)
    torch.manual_seed(0)
    teacher = {}
    for sf in a.teacher:
        teacher |= {r["id"]: r["scores"] for r in read_jsonl(sf)}
    train = [p for p in read_jsonl(a.pools) if p["id"] in teacher and len(p["hits"]) >= 4]
    dev = read_jsonl(a.dev_pools)[: a.dev_n]
    answers = {r["id"]: r["answers"] for r in read_jsonl(a.dev_answers)}
    st = Student(student_src(a.init))
    opt = torch.optim.AdamW(st.params(), lr=a.lr, weight_decay=0.01)
    steps = a.epochs * len(train) // a.queries_per_step
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps, pct_start=0.05, anneal_strategy="linear")
    best = dev_hit1(st, dev, answers)
    print(f"train {len(train)} queries, {steps} steps; dev hit@1 before: {best:.4f}", flush=True)
    st.save(Path(a.out))
    step, t0 = 0, time.time()
    for ep in range(a.epochs):
        random.shuffle(train)
        for i in range(0, len(train) - a.queries_per_step + 1, a.queries_per_step):
            st.backbone.train()
            st.head.train()
            pairs, tl = [], []
            for p in train[i : i + a.queries_per_step]:
                t = teacher[p["id"]]
                idx = argsort_desc(t)
                pick = idx[:2] + random.sample(idx[2:], min(a.group - 2, len(idx) - 2))
                pick += [pick[-1]] * (a.group - len(pick))  # pad short pools by repeating the last doc
                pairs += [(p["query"], p["hits"][j]["text"]) for j in pick]
                tl.append([t[j] for j in pick])
            with torch.autocast("cuda", dtype=torch.bfloat16):
                s = st.logits(pairs).float().view(len(tl), a.group)
            t = torch.tensor(tl, device=s.device) / a.temperature
            loss = torch.nn.functional.kl_div(torch.log_softmax(s / a.temperature, -1), torch.log_softmax(t, -1),
                                              log_target=True, reduction="batchmean")
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(st.params(), 1.0)
            opt.step()
            sched.step()
            step += 1
            if step % 100 == 0:
                print(f"step {step}/{steps} loss {loss.item():.4f} ({time.time() - t0:.0f}s)", flush=True)
            if step % a.eval_every == 0 or step == steps:
                h = dev_hit1(st, dev, answers)
                print(f"step {step}: dev hit@1 {h:.4f} (best {best:.4f})", flush=True)
                if h > best:
                    best = h
                    st.save(Path(a.out))
    print(f"best dev hit@1 {best:.4f}; saved {a.out}")


# ---------------------------------------------------------------- export


def nb8(src: Path, dst: Path) -> None:
    """8-bit weight-only MatMulNBits copy of an fp32 ONNX graph (activations stay fp32)."""
    import onnx
    from onnxruntime.quantization import matmul_nbits_quantizer as mq

    q = mq.MatMulNBitsQuantizer(onnx.load(src), algo_config=mq.DefaultWeightOnlyQuantConfig(
        block_size=32, is_symmetric=True, accuracy_level=4, bits=8))
    q.process()
    q.model.save_model_to_file(str(dst))


def cmd_export(a) -> None:
    import torch
    import onnx
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from onnxruntime.quantization import matmul_nbits_quantizer as mq
    from transformers import AutoModel

    src = Path(a.model)
    m = AutoModel.from_pretrained(src).eval()

    class Body(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask):
            return self.m(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state

    onnx_dir = src / "onnx"
    onnx_dir.mkdir(exist_ok=True)
    ids = torch.ones((2, 16), dtype=torch.long)
    torch.onnx.export(Body(m), (ids, torch.ones_like(ids)), onnx_dir / "model.onnx", input_names=["input_ids", "attention_mask"],
                      output_names=["last_hidden_state"], dynamic_axes={"input_ids": {0: "b", 1: "n"}, "attention_mask": {0: "b", 1: "n"},
                                                                         "last_hidden_state": {0: "b", 1: "n"}},
                      opset_version=17, dynamo=False)
    # The app still loads the per-tensor dynamic int8 files, but they cost ~6 points of NQ hit@1 (ModernBERT's
    # activation outliers). model_nb8 quantizes weights only (8-bit blocks of 32) and matches fp32 (Spearman 0.9999).
    quantize_dynamic(onnx_dir / "model.onnx", onnx_dir / "model_qint8_arm64.onnx", weight_type=QuantType.QInt8, per_channel=True)
    quantize_dynamic(onnx_dir / "model.onnx", onnx_dir / "model_quint8_avx2.onnx", weight_type=QuantType.QUInt8)
    nb8(onnx_dir / "model.onnx", onnx_dir / "model_nb8.onnx")

    # Parity: ONNX (fp32 and int8) vs PyTorch on the last hidden state's CLS row.
    import onnxruntime as ort
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(src)
    enc = tok(["why is the sky blue"] * 2, ["Rayleigh scattering makes the sky blue.", "Sky blue is a colour."], return_tensors="np", padding=True)
    with torch.inference_mode():
        ref = m(input_ids=torch.tensor(enc["input_ids"]), attention_mask=torch.tensor(enc["attention_mask"])).last_hidden_state[:, 0].numpy()
    for f in ["model.onnx", "model_nb8.onnx", "model_qint8_arm64.onnx", "model_quint8_avx2.onnx"]:
        s = ort.InferenceSession(str(onnx_dir / f), providers=["CPUExecutionProvider"])
        got = s.run(None, {"input_ids": enc["input_ids"].astype(np.int64), "attention_mask": enc["attention_mask"].astype(np.int64)})[0][:, 0]
        print(f"{f}: max |Δ CLS| {np.abs(got - ref).max():.4f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("--pools", required=True)
    s.add_argument("--model", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--max-len", type=int, default=512)
    s.add_argument("--batch", type=int, default=64)
    s.add_argument("--chunk", type=int, default=64, help="queries per predict call")
    h = sub.add_parser("hitk")
    h.add_argument("--pools", required=True)
    h.add_argument("--answers", required=True)
    h.add_argument("--scores", nargs="*")
    h.add_argument("--limit", type=int)
    h.add_argument("--offset", type=int, default=0, help="skip the first N pools (train uses the first 1,000 NQ dev queries for model selection)")
    t = sub.add_parser("train")
    t.add_argument("--pools", required=True)
    t.add_argument("--teacher", required=True, nargs="+")
    t.add_argument("--dev-pools", required=True)
    t.add_argument("--dev-answers", required=True)
    t.add_argument("--dev-n", type=int, default=1000)
    t.add_argument("--init", default=STUDENT)
    t.add_argument("--out", required=True)
    t.add_argument("--epochs", type=int, default=2)
    t.add_argument("--lr", type=float, default=3e-5)
    t.add_argument("--queries-per-step", type=int, default=16)
    t.add_argument("--group", type=int, default=16)
    t.add_argument("--temperature", type=float, default=1.0)
    t.add_argument("--eval-every", type=int, default=500)
    e = sub.add_parser("export")
    e.add_argument("--model", required=True)
    n = sub.add_parser("nb8", help="write the 8-bit weight-only copy of an fp32 ONNX reranker (the phone's file)")
    n.add_argument("--src", required=True)
    n.add_argument("--dst", required=True)
    a = ap.parse_args()
    if a.cmd == "nb8":
        return nb8(Path(a.src), Path(a.dst))
    {"score": cmd_score, "hitk": cmd_hitk, "train": cmd_train, "export": cmd_export}[a.cmd](a)


if __name__ == "__main__":
    main()
