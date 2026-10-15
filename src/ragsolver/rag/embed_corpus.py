"""Embebe un corpus normalizado (data/corpus/*.jsonl) in-place: agrega
`embedding`, `embedding_model`, `embedding_dim`, `index_version` a cada línea.

Genérico para ambos corpus (mathlib/informal) — sólo depende de que cada línea
tenga un campo `text` (docs/PLAN_RAG.md §9.1: un solo modelo para ambos).

Uso: python -m ragsolver.rag.embed_corpus --in data/corpus/mathlib.jsonl --out data/corpus/mathlib_embedded.jsonl
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import yaml

from ragsolver.rag.embed import embed_documents


def run(in_path: str, out_path: str, versions_path: str = "configs/versions.yaml", batch_size: int = 32, chunk: int = 2048) -> dict:
    """Streaming: lee/embebe/escribe por bloques de `chunk` filas (278k filas × 1024 floats no caben en RAM)."""
    with open(versions_path, encoding="utf-8") as f:
        versions = yaml.safe_load(f)
    model_name = versions["embedding"]["model"]
    index_version = versions["index"]["version"]

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    n, dim, t0 = 0, 0, time.time()
    with open(in_path, encoding="utf-8") as fin, out.open("w", encoding="utf-8") as fout:
        rows: list[dict] = []

        def flush():
            nonlocal n, dim
            if not rows:
                return
            vectors = embed_documents([r["text"] for r in rows], model_name=model_name, batch_size=batch_size)
            for row, vec in zip(rows, vectors):
                row["embedding"] = vec
                row["embedding_model"] = model_name
                row["embedding_dim"] = len(vec)
                row["index_version"] = index_version
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += len(rows)
            dim = len(vectors[0]) if vectors else dim
            rows.clear()
            print(f"  {n} textos, {n / (time.time() - t0):.1f}/s", flush=True)

        for line in fin:
            if line.strip():
                rows.append(json.loads(line))
            if len(rows) >= chunk:
                flush()
        flush()
    dt = time.time() - t0
    return {"n": n, "seconds": round(dt, 1), "texts_per_sec": round(n / dt, 2) if dt > 0 else None, "model": model_name, "dim": dim, "backend": __import__("os").environ.get("RAGSOLVER_EMBED_BACKEND", "torch"), "out": str(out)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", dest="out_path", required=True)
    ap.add_argument("--versions", default="configs/versions.yaml")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--backend", default=None, choices=["torch", "llama"], help="por defecto RAGSOLVER_EMBED_BACKEND o torch")
    args = ap.parse_args()
    if args.backend:
        import os

        os.environ["RAGSOLVER_EMBED_BACKEND"] = args.backend
    stats = run(args.in_path, args.out_path, args.versions, args.batch_size)
    print(json.dumps(stats, indent=2))
