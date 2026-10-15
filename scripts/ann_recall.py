"""Índice ANN de `mathlib_full`: construye IVF_SQ (si hace falta) y mide cuánto se aparta de la
búsqueda exacta (recall@k del ANN respecto del top-k exacto), latencia p50/p95 de ambas y RSS.

Consultas sintéticas sin cargar el embedder: normalize(e_i + e_j) para pares al azar de
declaraciones (caen entre dos documentos, no encima de uno). Uso:
    python scripts/ann_recall.py [--build] [--n 200] [--out data/eval/results/ann_mathlib_full.json]
"""

from __future__ import annotations

import argparse
import json
import resource
import statistics
import time

import lancedb
import numpy as np

from ragsolver.rag.index import ANN_NPROBES, ANN_REFINE, build_ann_index, sql_quote


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-path", default="data/index/lancedb")
    ap.add_argument("--table", default="mathlib_full")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--k", type=int, default=50)
    ap.add_argument("--out", default="data/eval/results/ann_mathlib_full.json")
    args = ap.parse_args()

    t = lancedb.connect(args.db_path).open_table(args.table)
    n_rows = t.count_rows()
    if args.build:
        t0 = time.time()
        build_ann_index(t)
        print(f"índice construido en {time.time() - t0:.0f} s", flush=True)
    has_index = any("IVF" in str(i.index_type).upper() for i in t.list_indices())

    rng = np.random.default_rng(13)
    all_ids = t.search().select(["chunk_id"]).limit(n_rows).to_arrow().column("chunk_id").to_pylist()
    pick = [all_ids[i] for i in rng.choice(n_rows, size=2 * args.n, replace=False)]
    rows = t.search().where("chunk_id IN (" + ", ".join(sql_quote(c) for c in pick) + ")").select(["chunk_id", "embedding"]).limit(len(pick)).to_list()
    by_id = {r["chunk_id"]: r["embedding"] for r in rows}
    emb = np.array([by_id[c] for c in pick], dtype="float32")
    queries = emb[0::2] + emb[1::2]
    queries /= np.linalg.norm(queries, axis=1, keepdims=True)

    rec10, rec50, lat_exact, lat_ann = [], [], [], []
    for i, q in enumerate(queries):
        t0 = time.time()
        exact = [r["chunk_id"] for r in t.search(q, vector_column_name="embedding").bypass_vector_index().limit(args.k).select(["chunk_id"]).to_list()]
        lat_exact.append(time.time() - t0)
        t0 = time.time()
        ann = [r["chunk_id"] for r in t.search(q, vector_column_name="embedding").nprobes(ANN_NPROBES).refine_factor(ANN_REFINE).limit(args.k).select(["chunk_id"]).to_list()]
        lat_ann.append(time.time() - t0)
        rec10.append(len(set(exact[:10]) & set(ann[:10])) / 10)
        rec50.append(len(set(exact) & set(ann)) / len(exact))
        if (i + 1) % 50 == 0:
            print(f"{i + 1}/{len(queries)} recall@10 {statistics.mean(rec10):.4f} recall@50 {statistics.mean(rec50):.4f}", flush=True)

    p = lambda xs, q: round(sorted(xs)[int(q * (len(xs) - 1))], 3)
    out = {
        "table": args.table, "rows": n_rows, "ann_index": has_index, "index_type": "IVF_SQ", "num_partitions": 256,
        "nprobes": ANN_NPROBES, "refine_factor": ANN_REFINE, "n_queries": len(queries), "k": args.k,
        "ann_recall@10_vs_exact": round(statistics.mean(rec10), 4), "ann_recall@50_vs_exact": round(statistics.mean(rec50), 4),
        "latency_exact_s": {"p50": p(lat_exact, 0.5), "p95": p(lat_exact, 0.95)}, "latency_ann_s": {"p50": p(lat_ann, 0.5), "p95": p(lat_ann, 0.95)},
        "maxrss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
        "note": "consultas sintéticas normalize(e_i + e_j); latencias medidas con la corrida eval_v1 en paralelo (CPU compartida)",
    }
    open(args.out, "w", encoding="utf-8").write(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
