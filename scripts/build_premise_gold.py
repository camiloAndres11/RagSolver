"""Fase 5, tarea 1 — premisas gold por problema de miniF2F a partir de pruebas Lean 4
verificadas publicadas (DeepSeek-Prover-V2, `minif2f-solutions.zip`: enunciados de
miniF2F-v1) cruzadas con los enunciados informales de miniF2F-v2s por nombre.

Una premisa = identificador de la prueba (tras `:= by`) que existe como `decl_name`
en el corpus normalizado de Mathlib (data/corpus/mathlib.jsonl).

Uso: python scripts/build_premise_gold.py
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from pathlib import Path

_IDENT_RE = re.compile(r"(?<![\w.'])([A-Za-z_][\w']*(?:\.[A-Za-z_][\w']*)*)")
_TACTIC_WORDS = {"by", "have", "exact", "apply", "intro", "intros", "rcases", "obtain", "refine", "constructor", "simp", "norm_num", "linarith", "nlinarith", "omega", "ring", "ring_nf", "field_simp", "positivity", "decide", "rfl", "use", "cases", "induction", "rw", "calc", "show", "fun", "at", "with", "this", "h", "hx", "hy", "hz", "Real", "Nat", "Int", "Finset", "Set", "ℝ", "ℕ", "ℤ", "True", "False", "not", "and", "or"}


def premises_of(proof: str, names: set[str]) -> list[str]:
    body = proof.split(":= by", 1)[1] if ":= by" in proof else proof
    found = []
    for tok in _IDENT_RE.findall(body):
        if tok in names and tok not in _TACTIC_WORDS and tok not in found:
            found.append(tok)
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proofs", default="data/eval/minif2f_v2/dsp2")
    ap.add_argument("--v2s", default="data/eval/minif2f_v2/miniF2F_v2s.jsonl")
    ap.add_argument("--corpus", default="data/corpus/mathlib.jsonl")
    ap.add_argument("--out", default="data/eval/premises_gold.jsonl")
    args = ap.parse_args()

    names = set()
    with open(args.corpus, encoding="utf-8") as f:
        for line in f:
            names.add(json.loads(line)["decl_name"])
    v2s = {r["name"]: r for r in (json.loads(l) for l in open(args.v2s, encoding="utf-8"))}

    rows, n_prem = [], 0
    for path in sorted(glob.glob(f"{args.proofs}/**/*.lean", recursive=True)):
        name = Path(path).stem
        prem = premises_of(Path(path).read_text(encoding="utf-8"), names)
        if not prem or name not in v2s:
            continue
        r = v2s[name]
        rows.append({"problem_id": name, "split": r["split"], "informal_statement": r.get("informal_statement") or r.get("informal statement", ""), "formal_statement": r["formal_statement"], "premises": prem, "n_premises": len(prem)})
        n_prem += len(prem)
    Path(args.out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    uniq = {p for r in rows for p in r["premises"]}
    print(json.dumps({"problems_with_premises": len(rows), "premises_total": n_prem, "premises_unique": len(uniq), "mean_per_problem": round(n_prem / len(rows), 2) if rows else 0, "out": args.out}, indent=2))


if __name__ == "__main__":
    main()
