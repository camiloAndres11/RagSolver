"""Métricas de IR (docs/PLAN_RAG.md §11.2) sin dependencias: recall@k, nDCG@k (relevancia
binaria, varios relevantes por consulta) y MRR. Reemplazan a `ranx` en las corridas
largas: en dos corridas de evaluación `ranx` devolvió valores absurdos (recall 0.11 con
las mismas listas de hits que, recomputadas desde las trazas, dan 0.76 —
docs/DECISIONS.md #43). `tests/test_metrics.py` verifica la igualdad con ranx.
"""

from __future__ import annotations

import math


def ranked_ids(scored: dict[str, float]) -> list[str]:
    return [d for d, _ in sorted(scored.items(), key=lambda kv: -kv[1])]


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    return len(relevant & set(ranked[:k])) / len(relevant) if relevant else 0.0


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 2) for i, d in enumerate(ranked[:k]) if d in relevant)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / idcg if idcg else 0.0


def mrr(ranked: list[str], relevant: set[str]) -> float:
    return next((1.0 / (i + 1) for i, d in enumerate(ranked) if d in relevant), 0.0)


def evaluate_run(qrels: dict[str, dict[str, int]], run: dict[str, dict[str, float]], ks: tuple[int, ...] = (10,)) -> dict[str, float]:
    """Promedio sobre las consultas de `qrels` (consulta sin hits = 0, como ranx)."""
    out: dict[str, float] = {}
    n = len(qrels) or 1
    for k in ks:
        out[f"recall@{k}"] = sum(recall_at_k(ranked_ids(run.get(q, {})), set(rel), k) for q, rel in qrels.items()) / n
        out[f"ndcg@{k}"] = sum(ndcg_at_k(ranked_ids(run.get(q, {})), set(rel), k) for q, rel in qrels.items()) / n
    out["mrr"] = sum(mrr(ranked_ids(run.get(q, {})), set(rel)) for q, rel in qrels.items()) / n
    return out
