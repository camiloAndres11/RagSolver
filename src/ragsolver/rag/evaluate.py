"""Fase 1, tarea 6 / Fase 3 aceptación — evaluación de retrieval sobre MathlibQR
(docs/PLAN_RAG.md §10, §11).

Compara 4 variantes (denso, BM25, híbrido, híbrido+rerank) con Recall@k/nDCG@10/MRR
vía `rag/metrics.py` (no `ranx`: dos corridas largas del CLI devolvieron valores
absurdos, docs/DECISIONS.md #43; `metrics.py` se verifica igual a `ranx` en
`tests/test_metrics.py`), usando `full_name` de cada fila de MathlibQR como
documento gold. Las cuatro variantes pasan por `Retriever.search` (la misma
función que usan los agentes) — sólo cambian `channels` y `rerank`, no hay una
segunda implementación de la búsqueda.

IMPORTANTE: la cobertura (`n_gold_covered_in_table`) depende de la tabla que se
evalúe. Con la muestra corregida (docs/DECISIONS.md #31-32, `mathlib_sample.jsonl`)
la cobertura es 83.8 % (793/946); con el corpus completo (`mathlib_full`) el
recall real baja a ~0.41-0.45 (#56, #68) — el número de la muestra sobreestima
por el tamaño acotado del pool de candidatos, no sólo por cobertura.

Uso: python -m ragsolver.rag.evaluate --qr data/eval/MathlibQR.json [--limit 20]
"""

from __future__ import annotations

import argparse
import json
import random
import tempfile

from ragsolver.rag.metrics import evaluate_run
from ragsolver.rag.retrieve import Retriever
from ragsolver.rag.schema import RetrievalRequest

QUERY_FIELDS = ["q1a_lean", "q1b_latex", "q1c_natural", "q2_slogan", "q3_nickname", "q4_special_case"]

VARIANTS = {  # nombre -> (canales, rerank)
    "denso": (("dense",), False),
    "bm25": (("sparse",), False),
    "hibrido": (("dense", "sparse"), False),
    "hibrido_rerank": (("dense", "sparse"), True),
}


def _build_queries(qr_path: str, limit: int | None = None, sample: int | None = None, seed: int = 13) -> list[dict]:
    rows = json.load(open(qr_path, encoding="utf-8"))
    queries = []
    for row in rows:
        gold_id = f"mathlib:{row['full_name']}:0"
        for field in QUERY_FIELDS:
            text = row.get(field, "")
            if text:
                queries.append({"query_id": f"{row['id']}:{field}", "text": text, "gold_id": gold_id})
    if sample:  # subconjunto aleatorio reproducible (el rerank en CPU no alcanza para las 946)
        queries = random.Random(seed).sample(queries, min(sample, len(queries)))
    return queries[:limit] if limit else queries


def evaluate_variants(retriever: Retriever, qr_path: str, table_name: str = "mathlib", n_candidates: int = 50, k: int = 10, limit: int | None = None, sample: int | None = None, variants: list[str] | None = None) -> dict:
    queries = _build_queries(qr_path, limit, sample)
    chosen = {name: VARIANTS[name] for name in (variants or VARIANTS)}
    retriever.tables["mathlib"] = table_name  # la tabla evaluada sirve el corpus "mathlib"
    table = retriever._table("mathlib")
    all_ids = {r["chunk_id"] for r in table.search().select(["chunk_id"]).limit(table.count_rows()).to_list()}
    n_covered = sum(1 for q in queries if q["gold_id"] in all_ids)
    qrels = {q["query_id"]: {q["gold_id"]: 1} for q in queries}

    retriever.trace_dir = tempfile.mkdtemp(prefix="ragsolver-eval-")  # no ensuciar data/eval/traces
    runs: dict[str, dict] = {name: {} for name in chosen}
    for q in queries:
        for name, (channels, rerank) in chosen.items():
            retriever.channels = channels
            req = RetrievalRequest(queries=[q["text"]], corpora=["mathlib"], k=n_candidates, n_candidates=n_candidates, rerank=rerank, include_figures=False, context={"problem_id": "eval", "agent": "evaluate"})
            hits = retriever.search(req).hits
            runs[name][q["query_id"]] = {h.chunk_id: (h.citation.scores.rerank if rerank else h.citation.scores.fused) for h in hits}
    retriever.channels = ("dense", "sparse")

    return {
        "n_queries": len(queries),
        "n_gold_covered_in_table": n_covered,
        "coverage_ratio": round(n_covered / len(queries), 3) if queries else 0.0,
        **{name: evaluate_run(qrels, run, ks=(k,)) for name, run in runs.items()},
        "_runs": runs,  # hits crudos por consulta: permiten recomputar las métricas sin repetir búsquedas
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-path", default="data/index/lancedb")
    ap.add_argument("--table", default="mathlib")
    ap.add_argument("--qr", default="data/eval/MathlibQR.json")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--limit", type=int, default=None, help="evaluar sólo las primeras N consultas")
    ap.add_argument("--sample", type=int, default=None, help="muestra aleatoria de N consultas (semilla fija)")
    ap.add_argument("--variants", default=None, help="p.ej. denso,bm25,hibrido (por defecto las 4)")
    ap.add_argument("--out", default=None, help="JSON con métricas y hits crudos (_runs)")
    args = ap.parse_args()
    out = evaluate_variants(Retriever(db_path=args.db_path), args.qr, args.table, k=args.k, limit=args.limit, sample=args.sample, variants=args.variants.split(",") if args.variants else None)
    if args.out:
        json.dump(out, open(args.out, "w", encoding="utf-8"), default=float)
    print(json.dumps({k: v for k, v in out.items() if k != "_runs"}, indent=2, default=float))
