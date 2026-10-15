"""Las métricas propias coinciden con ranx (misma definición: relevancia binaria, ties por orden de score)."""

from __future__ import annotations

import random

from ranx import Qrels, Run, evaluate

from ragsolver.rag.metrics import evaluate_run


def test_metrics_match_ranx_on_random_runs():
    rng = random.Random(0)
    qrels = {f"q{i}": {f"d{rng.randrange(30)}": 1 for _ in range(rng.randint(1, 3))} for i in range(40)}
    run = {q: {f"d{j}": rng.random() for j in rng.sample(range(30), 20)} for q in qrels}
    ours = evaluate_run(qrels, run, ks=(8, 10, 20))
    theirs = evaluate(Qrels(qrels), Run(run), ["recall@8", "recall@10", "recall@20", "ndcg@8", "ndcg@10", "ndcg@20", "mrr"])
    for m, v in theirs.items():
        assert abs(ours[m] - float(v)) < 1e-9, m


def test_missing_query_counts_as_zero():
    assert evaluate_run({"q1": {"a": 1}, "q2": {"b": 1}}, {"q1": {"a": 1.0}})["recall@10"] == 0.5
