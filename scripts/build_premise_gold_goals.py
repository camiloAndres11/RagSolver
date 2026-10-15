"""Fase 0 (tareas 3-4) de docs/PLAN_MEJORA_RAG.md — premisas gold por GOAL, contra el entorno de Lean.

Elabora en el REPL cada prueba verificada de DeepSeek-Prover-V2 (`data/eval/minif2f_v2/dsp2`) con
`all_tactics=True` y toma, por táctica hoja, el goal ANTES de la táctica (la consulta) y, como
premisas, las constantes que usa (`used_constants`) que además **nombra el texto de la táctica**
(`nlinarith [sq_nonneg (a - b)]` -> `sq_nonneg`), resolviendo los `open` (`succ_le_iff` ->
`Nat.succ_le_iff`). Sin esa intersección el gold se llena de plomería interna de `norm_num`/`simp`/
`decide` (`congrFun'`, `Nat.decLt`, `of_decide_eq_false`), que nadie recupera ni necesita recuperar. Es la unidad que exige RF-2
(subobjetivo -> lemas) y no depende del corpus de LeanSearch (que no tiene el núcleo de Lean, §2.4).

Salidas:
  data/eval/premises_gold_goals.jsonl   {problem_id, split, goal, tactic, premises}
  data/eval/premises_gold_lean.jsonl    por problema, mismo esquema que premises_gold.jsonl
                                        (unión de las premisas de sus goals)
Uso: python scripts/build_premise_gold_goals.py
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proofs", default="data/eval/minif2f_v2/dsp2")
    ap.add_argument("--v2s", default="data/eval/minif2f_v2/miniF2F_v2s.jsonl")
    ap.add_argument("--out-goals", default="data/eval/premises_gold_goals.jsonl")
    ap.add_argument("--out-problems", default="data/eval/premises_gold_lean.jsonl")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    from lean_interact import Command

    from ragsolver.lean.verify import LeanChecker
    from ragsolver.rag.premises import IDENT_RE, is_lemma_type, is_premise_constant, strip_field

    v2s = {r["name"]: r for r in (json.loads(l) for l in open(args.v2s, encoding="utf-8"))}
    files = [p for p in sorted(glob.glob(f"{args.proofs}/**/*.lean", recursive=True)) if Path(p).stem in v2s][: args.limit]
    checker = LeanChecker(imports=["Mathlib", "Aesop"], timeout_s=1800)
    raw: list[dict] = []
    n_err = 0
    for i, path in enumerate(files):
        src = Path(path).read_text(encoding="utf-8")
        src = "\n".join(l for l in src.splitlines() if not l.startswith("import "))
        src = re.sub(r"set_option maxHeartbeats \d+", "set_option maxHeartbeats 400000", src)
        with checker._lock:
            r = checker._run(Command(cmd=src, env=checker._env, all_tactics=True), timeout=300)
        if r is None or not hasattr(r, "tactics"):
            n_err += 1
            continue
        # pruebas escritas para un Mathlib anterior: Lean recupera el error y sigue; se conservan las
        # tácticas que no contienen la posición de ningún error (sus premisas son reales)
        err_pos = [(m.start_pos.line, m.start_pos.column) for m in r.messages if m.severity == "error" and m.start_pos]
        n_err += bool(err_pos)
        for t in r.tactics:
            # sólo tácticas sin bloque anidado: un `have … := by …` o un `·` arrastra las premisas de su bloque.
            # Un salto de línea NO basta para descartar: DeepSeek parte `nlinarith [sq_nonneg (a - b), …]` en varias líneas
            if re.search(r"(?<![\w'])by(?![\w'])", t.tactic) or t.tactic.lstrip().startswith(("·", "case ", "next ")):
                continue
            if any((t.start_pos.line, t.start_pos.column) <= e <= (t.end_pos.line, t.end_pos.column) for e in err_pos):
                continue
            toks = {strip_field(x) for x in IDENT_RE.findall(t.tactic)}
            named = [c for c in dict.fromkeys(t.used_constants) if is_premise_constant(c) and any(c == x or c.endswith("." + x) for x in toks)]
            if named:
                raw.append({"problem_id": Path(path).stem, "goal": t.goals, "tactic": t.tactic[:300], "consts": named, "used_constants": list(t.used_constants)})
        if i % 50 == 0:
            print(f"{i}/{len(files)} pruebas, {len(raw)} tácticas con constantes, {n_err} con algún error", flush=True)

    Path(args.out_goals).with_suffix(".raw.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in raw), encoding="utf-8")
    sigs = checker.signatures(sorted({c for x in raw for c in x["consts"]}))
    checker.close()
    lemmas = {c for c, t in sigs.items() if is_lemma_type(t)}

    goals: dict[tuple[str, str], dict] = {}
    for x in raw:
        prem = [c for c in x["consts"] if c in lemmas]
        if not prem or not x["goal"].strip():
            continue
        key = (x["problem_id"], x["goal"])
        g = goals.setdefault(key, {"problem_id": x["problem_id"], "split": v2s[x["problem_id"]]["split"], "goal": x["goal"], "tactic": x["tactic"], "premises": []})
        g["premises"] = list(dict.fromkeys(g["premises"] + prem))
    Path(args.out_goals).write_text("".join(json.dumps(g, ensure_ascii=False) + "\n" for g in goals.values()), encoding="utf-8")

    per_problem: dict[str, list[str]] = {}
    for g in goals.values():
        per_problem[g["problem_id"]] = list(dict.fromkeys(per_problem.get(g["problem_id"], []) + g["premises"]))
    rows = [{"problem_id": pid, "split": v2s[pid]["split"], "informal_statement": v2s[pid].get("informal_statement") or v2s[pid].get("informal statement", ""), "formal_statement": v2s[pid]["formal_statement"], "premises": prem, "n_premises": len(prem)} for pid, prem in per_problem.items()]
    Path(args.out_problems).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(json.dumps({"proofs": len(files), "proofs_with_some_error": n_err, "goals": len(goals), "problems": len(rows), "distinct_premises": len({p for r in rows for p in r["premises"]})}, indent=2), flush=True)


if __name__ == "__main__":
    main()
