"""Dev-only: arma un subconjunto acotado de data/corpus/mathlib.jsonl para poder
embeber e indexar rápido en una máquina sin GPU (prueba de concepto de Fase 1).

Incluye TODAS las declaraciones gold referenciadas por MathlibQR/MathlibMPR (si no,
la evaluación de recall sería 0 por construcción, no por calidad del retriever) más
N distractores aleatorios. No reemplaza la corrida completa sobre las 278k
declaraciones filtradas — ver docs/DECISIONS.md, entrada E2 (2026-09-18).

Uso: python scripts/build_eval_sample.py --n-distractors 8000
"""

from __future__ import annotations

import argparse
import json
import random


def gold_decl_names(qr_path: str, mpr_path: str) -> set[str]:
    qr = json.load(open(qr_path, encoding="utf-8"))
    mpr = json.load(open(mpr_path, encoding="utf-8"))
    names = {r["full_name"] for r in qr}
    for r in mpr:
        for g in r.get("premise_group", []):
            names.update(g.get("docs", []))
    return names


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="data/corpus/mathlib.jsonl")
    ap.add_argument("--qr", default="data/eval/MathlibQR.json")
    ap.add_argument("--mpr", default="data/eval/MathlibMPR.json")
    ap.add_argument("--out", default="data/corpus/mathlib_sample.jsonl")
    ap.add_argument("--n-distractors", type=int, default=8000)
    ap.add_argument("--extra-gold", default=None, help="JSONL con campo `premises` (Fase 5: premisas gold de miniF2F)")
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    gold = gold_decl_names(args.qr, args.mpr)
    if args.extra_gold:
        for line in open(args.extra_gold, encoding="utf-8"):
            gold.update(json.loads(line).get("premises", []))
    rng = random.Random(args.seed)

    gold_rows, other_rows = [], []
    with open(args.corpus, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            (gold_rows if row["decl_name"] in gold else other_rows).append(line)

    rng.shuffle(other_rows)
    sample = gold_rows + other_rows[: args.n_distractors]
    rng.shuffle(sample)

    with open(args.out, "w", encoding="utf-8") as f:
        f.writelines(sample)

    found_gold_names = {json.loads(r)["decl_name"] for r in gold_rows}
    print(
        json.dumps(
            {
                "gold_wanted": len(gold),
                "gold_found_in_corpus": len(found_gold_names),
                "gold_missing": sorted(gold - found_gold_names)[:20],
                "distractors": len(sample) - len(gold_rows),
                "total": len(sample),
                "out": args.out,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
