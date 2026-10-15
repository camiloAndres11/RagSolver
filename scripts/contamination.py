"""Fase 5, tarea 3 — control de contaminación: similitud (coseno) entre cada problem
card del corpus informal y cada enunciado informal de miniF2F-v2s (valid+test).
Pares con similitud > umbral se listan para revisión/exclusión.
Uso: python scripts/contamination.py [--threshold 0.9]
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import yaml

from ragsolver.rag.embed import embed_documents


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.9)
    ap.add_argument("--out", default="data/eval/results/fase5_contamination.json")
    args = ap.parse_args()
    model = yaml.safe_load(open("configs/versions.yaml", encoding="utf-8"))["embedding"]["model"]
    cards = [json.loads(l) for l in open("data/corpus/informal_embedded.jsonl", encoding="utf-8") if '"problem_card"' in l]
    tests = [json.loads(l) for l in open("data/eval/minif2f_v2/miniF2F_v2s.jsonl", encoding="utf-8")]
    stmts = [t.get("informal_statement") or t.get("informal statement", "") for t in tests]
    C = np.array([c["embedding"] for c in cards], dtype=np.float32)
    T = np.array(embed_documents(stmts, model_name=model, batch_size=16), dtype=np.float32)
    S = C @ T.T  # embeddings normalizados -> coseno
    pairs = [(cards[i]["item_number"], tests[j]["name"], float(S[i, j])) for i, j in zip(*np.where(S > args.threshold))]
    best = [(cards[i]["item_number"], tests[int(S[i].argmax())]["name"], float(S[i].max())) for i in range(len(cards))]
    best.sort(key=lambda x: -x[2])
    out = {"threshold": args.threshold, "n_cards": len(cards), "n_minif2f": len(tests), "n_pairs_over_threshold": len(pairs), "pairs_over_threshold": pairs, "max_similarity": best[0][2] if best else None, "top10_most_similar": best[:10]}
    json.dump(out, open(args.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in out.items() if k != "top10_most_similar"}, indent=2, ensure_ascii=False))
    print("top10:", best[:10])


if __name__ == "__main__":
    main()
