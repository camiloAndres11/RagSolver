"""Fase 5, tarea 1 — recuperación de premisas gold (data/eval/premises_gold.jsonl):
consulta = enunciado informal (mode nl) o formal (mode formal) de miniF2F;
relevantes = lemas de Mathlib usados en la prueba verificada publicada.
Métricas: Recall@8, Recall@20, nDCG@10 (ranx), con cobertura de la tabla.
Uso: python scripts/eval_premises.py [--limit 100]
"""

from __future__ import annotations

import argparse
import json
import tempfile

from ragsolver.rag.metrics import evaluate_run
from ragsolver.rag.retrieve import Retriever
from ragsolver.rag.schema import RetrievalRequest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="data/eval/premises_gold.jsonl")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rerank", action="store_true")
    ap.add_argument("--out", default="data/eval/results/fase5_premises.json")
    args = ap.parse_args()
    gold = [json.loads(l) for l in open(args.gold, encoding="utf-8") if l.strip()][: args.limit]
    r = Retriever()
    r.trace_dir = tempfile.mkdtemp(prefix="ragsolver-eval-")
    table = r._table("mathlib")
    ids = {x["chunk_id"] for x in table.search().select(["chunk_id"]).limit(table.count_rows()).to_list()}

    qrels, covered = {}, 0
    for g in gold:
        rel = {f"mathlib:{p}:0": 1 for p in g["premises"] if f"mathlib:{p}:0" in ids}
        if rel:
            qrels[g["problem_id"]] = rel
            covered += len(rel)
    total_prem = sum(len(g["premises"]) for g in gold)
    out = {"n_problems": len(gold), "n_problems_with_covered_premises": len(qrels), "premise_coverage": round(covered / total_prem, 3) if total_prem else None, "rerank": args.rerank}
    for mode, field in (("nl", "informal_statement"), ("formal", "formal_statement")):
        run = {}
        for g in gold:
            if g["problem_id"] not in qrels:
                continue
            res = r.search(RetrievalRequest(queries=[g[field][:2000]], mode=mode, corpora=["mathlib"], k=20, n_candidates=50, rerank=args.rerank, include_figures=False, context={"problem_id": g["problem_id"], "agent": "eval"}))
            run[g["problem_id"]] = {h.chunk_id: (h.citation.scores.rerank if args.rerank else h.citation.scores.fused) or 0.0 for h in res.hits}
        m = evaluate_run(qrels, run, ks=(8, 10, 20))
        out[f"query_{mode}"] = {"recall@8": m["recall@8"], "recall@20": m["recall@20"], "ndcg@10": m["ndcg@10"], "mrr": m["mrr"]}
        out[f"_run_{mode}"] = run
        print(mode, out[f"query_{mode}"])
    json.dump(out, open(args.out, "w", encoding="utf-8"), indent=2, default=float)
    print(json.dumps(out, indent=2, default=float))


if __name__ == "__main__":
    main()
