"""Fases 0 (tarea 2) y 4 de docs/PLAN_MEJORA_RAG.md — la batería CLOSE sobre el goal raíz de cada
problema, 0 llamadas LLM: misma función (`close_goal`) y misma selección de premisas
(`select_premises`) que usa el grafo. Reanudable; una fila por (problema, variante).

Variantes:
  battery_plain       sin premisas (lo que corren `no_rag` y `baseline_auto`)
  battery_rag         premisas por goal: prior + índice denso (fusión RRF, canal C encendido) + hints + `grind +suggestions`
  battery_rag_prior   igual, sólo con el canal A (la configuración por defecto, `close.dense_channel: false`)
Una prueba cuenta si, además de cerrar el goal en el REPL, el archivo final pasa `LeanChecker.check`
(validador + kernel + auditoría de axiomas).

Uso: RAGSOLVER_MATHLIB_TABLE=mathlib_full python scripts/probe_close.py [--out data/eval/results/probe_close/battery.jsonl]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--problems", default="data/eval/problems/eval_v1.jsonl")
    ap.add_argument("--out", default="data/eval/results/probe_close/battery.jsonl")
    ap.add_argument("--variants", default="battery_plain,battery_rag,battery_rag_prior")
    args = ap.parse_args()

    from ragsolver.agents.agents import load_agents_config
    from ragsolver.lean.verify import LeanChecker
    from ragsolver.orchestrator.graph import close_goal, select_premises
    from ragsolver.rag import premises as P
    from ragsolver.rag.retrieve import Retriever

    cfg = load_agents_config()
    ccfg, lean = cfg["close"], cfg["lean"]
    problems = [json.loads(l) for l in open(args.problems, encoding="utf-8") if l.strip()]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {(r["problem_id"], r["variant"]) for r in map(json.loads, out.open())} if out.exists() else set()
    variants = args.variants.split(",")

    retriever = Retriever() if "battery_rag" in variants else None
    if retriever is not None:
        retriever.trace_dir = str(out.parent / "traces")
    checker = LeanChecker(imports=lean["imports"], timeout_s=600)
    with out.open("a", encoding="utf-8") as f:
        for p in problems:
            todo = [v for v in variants if (p["problem_id"], v) not in done]
            if not todo:
                continue
            goals, errors = checker.goals(p["formal_statement"], "sorry", p.get("header", ""), lean["max_heartbeats"])
            for v in todo:
                t0 = time.time()
                block, log, names = None, [], []
                if len(goals) == 1 and not errors:
                    g = goals[0]
                    if v == "battery_plain":
                        block, log = close_goal(checker, g, ccfg["tactics"], False, [], [], ccfg["timeout_s"])
                    else:
                        names = P.prior_channel(g["goal"], k=ccfg["k_premises"]) if v == "battery_rag_prior" else select_premises(retriever, g["goal"], ccfg["k_premises"], ctx={"problem_id": p["problem_id"], "agent": "probe"}, dense_channel=True)[0]
                        block, log = close_goal(checker, g, ccfg["tactics"], True, names, P.nlinarith_hints(g["goal"]), ccfg["timeout_s"])
                ok = bool(block) and checker.check(p["formal_statement"], block, p.get("header", ""), lean["max_heartbeats"]).ok
                f.write(json.dumps({"problem_id": p["problem_id"], "variant": v, "ok": ok, "block": block, "premises": names, "tried": log, "s": round(time.time() - t0, 1)}, ensure_ascii=False) + "\n")
                f.flush()
                print(p["problem_id"], v, ok, flush=True)
    checker.close()
    rows = [json.loads(l) for l in out.open()]
    print(json.dumps({v: sum(r["ok"] for r in rows if r["variant"] == v) for v in variants}), flush=True)


if __name__ == "__main__":
    main()
