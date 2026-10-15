"""Fase 5, tarea 2 — gold informal: 30 consultas (15 dependientes de figura) sobre
el PDF de ejemplo -> Recall@5 y "cobertura de figura" (la descripción VLM de la
figura contiene las entidades clave). Uso: python scripts/eval_informal_gold.py
"""

from __future__ import annotations

import argparse
import json
import tempfile

from ragsolver.orchestrator.run import wilson
from ragsolver.rag.retrieve import Retriever
from ragsolver.rag.schema import RetrievalRequest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="data/eval/informal_gold.jsonl")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--out", default="data/eval/results/fase5_informal_gold.json")
    args = ap.parse_args()
    gold = [json.loads(l) for l in open(args.gold, encoding="utf-8") if l.strip()]
    r = Retriever()
    r.trace_dir = tempfile.mkdtemp(prefix="ragsolver-eval-")
    figures = {f["figure_id"]: f for f in (json.loads(l) for l in open("data/corpus/figures.jsonl", encoding="utf-8"))}

    rows, hits_at_k, fig_cov = [], 0, []
    for q in gold:
        res = r.search(RetrievalRequest(queries=[q["query"]], mode="exemplar", corpora=["informal"], k=args.k, rerank=True, context={"problem_id": q["query_id"], "agent": "eval"}))
        items = [h.metadata.get("item_number") for h in res.hits]
        hit = any(i in q["relevant_items"] for i in items)
        hits_at_k += hit
        cov = None
        if q["figure_dependent"]:
            descs = " ".join((figures[fid]["description"] + " " + " ".join(figures[fid]["labels"])).lower() for h in res.hits for fid in h.metadata.get("figure_ids", []) if fid in figures and h.metadata.get("item_number") in q["relevant_items"])
            ents = q["figure_entities"]
            cov = sum(1 for e in ents if e.lower() in descs) / len(ents) if ents else None
            if cov is not None:
                fig_cov.append(cov)
        rows.append({"query_id": q["query_id"], "hit@k": hit, "rank": next((i + 1 for i, it in enumerate(items) if it in q["relevant_items"]), None), "top_items": items, "figure_coverage": cov})
        print(f"{q['query_id']}: hit={hit} rank={rows[-1]['rank']} top={items} fig_cov={cov}")

    n = len(gold)
    out = {
        "n_queries": n,
        f"recall@{args.k}": round(hits_at_k / n, 3),
        f"recall@{args.k}_wilson95": wilson(hits_at_k, n),
        "figure_dependent": {"n": sum(q["figure_dependent"] for q in gold), f"recall@{args.k}": round(sum(r["hit@k"] for r, q in zip(rows, gold) if q["figure_dependent"]) / max(1, sum(q["figure_dependent"] for q in gold)), 3), "figure_coverage_mean": round(sum(fig_cov) / len(fig_cov), 3) if fig_cov else None},
        "mrr": round(sum(1 / r["rank"] for r in rows if r["rank"]) / n, 3),
        "rows": rows,
    }
    json.dump(out, open(args.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
