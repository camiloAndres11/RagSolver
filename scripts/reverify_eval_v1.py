"""Fase 1, tarea 8 de docs/PLAN_CORRECCIONES.md — re-verificación offline de
`eval-v1` con el verificador endurecido (SEC-1, VER-1, BUG-2). 0 llamadas LLM:
sólo el REPL de Lean (~1 min de import + ~0.3 s por fila).

  1. Validación de tácticas (`find_forbidden_tactic`) sobre el `lean_code`
     final de las 300 filas: mide falsos positivos del denylist nuevo contra
     tácticas reales ya producidas por el LLM.
  2. Re-verificación completa (kernel + auditoría de axiomas) de las 27 filas
     `done`, reconstruyendo el archivo con el `header` del problema (BUG-2),
     que el `lean_code` original no incluía.

No modifica `results.jsonl`. Escribe `data/eval/runs/eval-v1/reverify.json`.

Uso: uv run python scripts/reverify_eval_v1.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ragsolver.agents.agents import load_agents_config
from ragsolver.agents.schemas import Problem
from ragsolver.lean.verify import LeanChecker, find_forbidden_tactic

RUN_DIR = Path("data/eval/runs/eval-v1")
PROBLEMS_PATH = Path("data/eval/problems/eval_v1.jsonl")

_TACTICS_SPLIT_RE = re.compile(r":=\s*by\n")


def extract_tactics(lean_code: str) -> str | None:
    """Tácticas ya indentadas a 2 espacios que dejó el `build_file` original
    (formato antiguo, sin `#print axioms`): todo lo que sigue al primer
    `:= by\\n` del archivo, de-indentado. None si no se encuentra el patrón."""
    m = _TACTICS_SPLIT_RE.search(lean_code)
    if not m:
        return None
    tail = lean_code[m.end() :]
    lines = tail.split("\n")
    while lines and lines[-1].strip() == "":
        lines.pop()
    return "\n".join(line[2:] if line.startswith("  ") else line for line in lines)


def main() -> None:
    rows = [json.loads(l) for l in (RUN_DIR / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    problems = {p.problem_id: p for p in (Problem.model_validate_json(l) for l in PROBLEMS_PATH.read_text(encoding="utf-8").splitlines() if l.strip())}
    lean_cfg = load_agents_config()["lean"]

    # 1. Falsos positivos del denylist sobre las filas con lean_code -------------
    false_positives = []
    n_checked = 0
    for r in rows:
        code = r.get("lean_code") or ""
        if not code:
            continue
        tactics = extract_tactics(code)
        if tactics is None:
            continue
        n_checked += 1
        hit = find_forbidden_tactic(tactics)
        if hit:
            false_positives.append({"config": r["config"], "problem_id": r["problem_id"], "status": r["status"], "denied_token": hit, "tactics": tactics[:300]})

    # 2. Re-verificación completa de las filas `done` -----------------------------
    done = [r for r in rows if r["status"] == "done" and r.get("lean_code")]
    checker = LeanChecker(imports=lean_cfg["imports"], timeout_s=lean_cfg["timeout_s"])
    checker.check("theorem _warmup : True := by sorry", "trivial")  # precalienta fuera del cronómetro por fila
    reverified = []
    for r in done:
        problem = problems.get(r["problem_id"])
        if problem is None:
            reverified.append({"config": r["config"], "problem_id": r["problem_id"], "skipped": "problema no encontrado en eval_v1.jsonl"})
            continue
        tactics = extract_tactics(r["lean_code"])
        if tactics is None:
            reverified.append({"config": r["config"], "problem_id": r["problem_id"], "skipped": "no se pudo extraer las tácticas del lean_code original"})
            continue
        v = checker.check(problem.formal_statement, tactics, problem.header, lean_cfg["max_heartbeats"])
        reverified.append(
            {
                "config": r["config"],
                "problem_id": r["problem_id"],
                "ok_before": True,
                "ok_now": v.ok,
                "axioms": v.axioms,
                "non_standard_axioms": [a for a in v.axioms if a not in ("propext", "Classical.choice", "Quot.sound")],
                "errors": v.errors[:2] if not v.ok else [],
            }
        )
    checker.close()

    regressions = [x for x in reverified if x.get("ok_before") and not x.get("ok_now")]
    out = {
        "n_rows": len(rows),
        "n_lean_code_checked_for_false_positives": n_checked,
        "n_false_positives": len(false_positives),
        "false_positives": false_positives,
        "n_done_reverified": len(reverified),
        "n_regressions": len(regressions),
        "regressions": regressions,
        "reverified": reverified,
    }
    (RUN_DIR / "reverify.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "reverified"}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
