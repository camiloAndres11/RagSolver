"""Objetivo 5 / §8.1 de la propuesta — conjunto de evaluación de 30-60 problemas de
miniF2F-v2s estratificado en 3 áreas (álgebra, teoría de números, combinatoria) × 3
niveles de dificultad (1 = mathd/algebra/numbertheory/induction, 2 = AMC/AIME, 3 = IMO),
con partición estricta desarrollo/prueba (dev = split `valid`, test = split `test`).

Área por heurística sobre nombre + enunciado (revisable a mano en el JSONL resultante).
Uso: python scripts/build_eval_set.py [--seed 13]
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

# (área, nivel) -> (n_dev, n_test); combinatoria nivel 3 sólo tiene 1 problema en miniF2F-v2
QUOTA = {
    ("algebra", 1): (4, 6), ("algebra", 2): (3, 5), ("algebra", 3): (2, 4),
    ("number_theory", 1): (4, 6), ("number_theory", 2): (3, 5), ("number_theory", 3): (2, 4),
    ("combinatorics", 1): (3, 4), ("combinatorics", 2): (3, 2), ("combinatorics", 3): (0, 1),
}


def area(r: dict) -> str:
    n = r["name"]
    s = r["formal_statement"] + " " + (r.get("informal_statement") or r.get("informal statement") or "")
    if "numbertheory" in n or re.search(r"Nat\.Prime|\bdigits\b|\bmod\b|% \d|Nat\.gcd|Nat\.lcm|divisib|\\mid|∣|prime|Nat\.factorial|remainder", s):
        return "number_theory"
    if re.search(r"Finset\.card|choose|Nat\.choose|permutation|combinat|count|how many|number of ways|Finset\.filter|binom", s, re.I):
        return "combinatorics"
    if "algebra" in n or "induction" in n or re.search(r"Real|polynomial|\\sqrt|roots|function|f\(|f :", s):
        return "algebra"
    return "other"


def level(r: dict) -> int:
    n = r["name"]
    if n.startswith(("mathd", "algebra", "numbertheory", "induction")):
        return 1
    if n.startswith("imo"):
        return 3
    return 2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/eval/minif2f_v2/miniF2F_v2s.jsonl")
    ap.add_argument("--out", default="data/eval/problems/eval_v1.jsonl")
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()
    rows = [json.loads(l) for l in open(args.src, encoding="utf-8")]
    rng = random.Random(args.seed)
    pools: dict[tuple, list[dict]] = {}
    for r in rows:
        pools.setdefault((area(r), level(r), "dev" if r["split"] == "valid" else "test"), []).append(r)
    out = []
    for (a, lv), (n_dev, n_test) in QUOTA.items():
        for split, n in (("dev", n_dev), ("test", n_test)):
            pool = sorted(pools.get((a, lv, split), []), key=lambda r: r["name"])
            rng.shuffle(pool)
            for r in pool[:n]:
                stmt = r["formal_statement"].rstrip()
                stmt = stmt + " sorry" if stmt.endswith(":= by") else (stmt if stmt.endswith("sorry") else stmt + " := by sorry")
                out.append({"problem_id": r["name"], "source": "minif2f_v2s", "split": split, "area": a, "difficulty": lv, "informal_statement": r.get("informal_statement") or r.get("informal statement"), "formal_statement": stmt, "header": "open BigOperators Real Nat Topology Rat"})
    Path(args.out).write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in out) + "\n", encoding="utf-8")
    from collections import Counter

    print(json.dumps({"n": len(out), "by_split": Counter(p["split"] for p in out), "by_area": Counter(p["area"] for p in out), "by_level": Counter(p["difficulty"] for p in out), "out": args.out}, indent=2, default=dict))


if __name__ == "__main__":
    main()
