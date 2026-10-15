"""Fases 0 (tarea 1) y 3 de docs/PLAN_MEJORA_RAG.md — recall de premisas con el gold contra Lean.

1. Por GOAL (`premises_gold_goals.jsonl`, unidad de RF-2): canal A (prior estático y filtrado por el
   goal), canal C (índice denso, la meta del goal como consulta) y la fusión que usa CLOSE.
2. Por PROBLEMA (`premises_gold_lean.jsonl`): la consulta de eval-v1 (enunciado NL / formal, índice
   denso) vs. el prior — la tabla 2.2 del plan, ahora con el gold que incluye el núcleo de Lean.
Recall@k = fracción de las premisas gold del goal/problema que aparecen en el top-k (media).
Uso: RAGSOLVER_MATHLIB_TABLE=mathlib_full python scripts/eval_premises_goals.py [--sample 600]
"""

from __future__ import annotations

import argparse
import json
import random
import tempfile
from pathlib import Path

KS = (8, 16, 32)


def recall(rows: list[tuple[set, list[str]]], k: int) -> float:
    return round(sum(len(g & set(r[:k])) / len(g) for g, r in rows) / len(rows), 3) if rows else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--goals", default="data/eval/premises_gold_goals.jsonl")
    ap.add_argument("--problems", default="data/eval/premises_gold_lean.jsonl")
    ap.add_argument("--sample", type=int, default=600, help="goals con canal C (embedder en CPU); el canal A corre sobre todos")
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--out", default="data/eval/results/premises_goals.json")
    args = ap.parse_args()

    from ragsolver.orchestrator.graph import select_premises
    from ragsolver.rag import premises as P
    from ragsolver.rag.retrieve import Retriever
    from ragsolver.rag.schema import RetrievalRequest

    goals = [json.loads(l) for l in open(args.goals, encoding="utf-8") if l.strip()]
    probs = [json.loads(l) for l in open(args.problems, encoding="utf-8") if l.strip()]
    out: dict = {"n_goals": len(goals), "n_problems": len(probs)}

    # 1a. canal A sobre todos los goals
    for name, fn in (("A_static", lambda g: P.prior_channel(g, k=max(KS), filtered=False)), ("A_filtered", lambda g: P.prior_channel(g, k=max(KS)))):
        rows = [(set(x["premises"]), fn(x["goal"])) for x in goals]
        out[f"goal_{name}"] = {f"recall@{k}": recall(rows, k) for k in KS}
        print(name, out[f"goal_{name}"], flush=True)

    # 1b. canal C y fusión sobre una muestra fija
    r = Retriever()
    r.trace_dir = tempfile.mkdtemp(prefix="ragsolver-eval-")
    sample = random.Random(args.seed).sample(goals, min(args.sample, len(goals)))
    rows_a, rows_c, rows_f = [], [], []
    for x in sample:
        gold = set(x["premises"])
        names, dense = select_premises(r, x["goal"], k=max(KS), ctx={"problem_id": x["problem_id"], "agent": "eval"}, dense_channel=True)
        rows_c.append((gold, list(dense)))
        rows_f.append((gold, names))
        rows_a.append((gold, P.prior_channel(x["goal"], k=max(KS))))
    out["goal_sample"] = len(sample)
    for name, rows in (("A_filtered_sample", rows_a), ("C_dense_goal", rows_c), ("fused_A_C", rows_f)):
        out[f"goal_{name}"] = {f"recall@{k}": recall(rows, k) for k in KS}
        print(name, out[f"goal_{name}"], flush=True)

    # 2. por problema: consulta de eval-v1 vs prior, gold contra Lean
    for mode, field in (("nl", "informal_statement"), ("formal", "formal_statement")):
        rows = []
        for p in probs:
            res = r.search(RetrievalRequest(queries=[p[field][:2000]], mode=mode, corpora=["mathlib"], k=max(KS), n_candidates=50, rerank=False, include_figures=False, context={"problem_id": p["problem_id"], "agent": "eval"}))
            rows.append((set(p["premises"]), [h.metadata.get("decl_name") for h in res.hits]))
        out[f"problem_dense_{mode}"] = {f"recall@{k}": recall(rows, k) for k in KS}
        print("problem", mode, out[f"problem_dense_{mode}"], flush=True)
    for name, filtered in (("problem_prior_static", False), ("problem_prior_filtered", True)):
        rows = [(set(p["premises"]), P.prior_channel(p["formal_statement"], k=max(KS), filtered=filtered)) for p in probs]
        out[name] = {f"recall@{k}": recall(rows, k) for k in KS}
        print(name, out[name], flush=True)
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
