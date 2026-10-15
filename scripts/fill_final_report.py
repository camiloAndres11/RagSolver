"""Rellena §6.3 (experimento principal) de docs/final_report.md con los números de una corrida.

Uso: python scripts/fill_final_report.py --run-id eval-v1 [--full-index-run-id eval-v1-full]
                                        [--problems data/eval/problems/eval_v1.jsonl]

Reemplaza el bloque entre "### 6.3 Main experiment" y "## 7. Discussion" (tablas en inglés,
generadas; la prosa interpretativa se escribe a mano después). Idempotente.
"""

from __future__ import annotations

import argparse
import json
import re
from itertools import combinations
from math import comb
from pathlib import Path

from ragsolver.orchestrator.run import wilson

CFG_LABEL = {"full": "full", "no_rag": "no RAG", "no_critic": "no critic", "baseline_a": "baseline A", "baseline_b": "baseline B (unverified)"}


def _rows(run_dir: Path) -> list[dict]:
    return [json.loads(l) for l in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def _ci(x) -> str:
    return f"[{x[0]}, {x[1]}]" if isinstance(x, (list, tuple)) and len(x) == 2 else "–"


def main_table(summary: dict) -> list[str]:
    L = ["| Configuration | n | pass@1 | Wilson 95 % | LLM calls (mean, bootstrap 95 %) | Retrieval calls | Tokens (mean) | Wall s (median) | Lemma-use precision |", "|---|---|---|---|---|---|---|---|---|"]
    for c in ("full", "no_rag", "no_critic", "baseline_a", "baseline_b"):
        s = summary.get(c)
        if not s:
            continue
        pass1 = "n/a (unverified)" if c == "baseline_b" else f"**{s['pass@1']:.3f}**"
        L.append(f"| {CFG_LABEL[c]} | {s['n']} | {pass1} | {'–' if c == 'baseline_b' else _ci(s.get('pass@1_wilson95'))} | {s['llm_calls_mean']} {_ci(s.get('llm_calls_mean_ci95_bootstrap'))} | {s.get('retrieval_calls_mean', '–')} | {s['tokens_mean']} | {s['wall_s_median']} | {s.get('lemma_use_precision') if s.get('lemma_use_precision') is not None else '–'} |")
    return L


def pass_by_attempt(summary: dict) -> list[str]:
    cfgs = [c for c in ("full", "no_rag", "no_critic") if summary.get(c, {}).get("pass_by_attempt")]
    if not cfgs:
        return []
    ks = sorted({int(k) for c in cfgs for k in summary[c]["pass_by_attempt"]})
    L = ["| Configuration | " + " | ".join(f"≤ {k} attempt(s)" for k in ks) + " |", "|---|" + "---|" * len(ks)]
    for c in cfgs:
        pba = summary[c]["pass_by_attempt"]
        L.append(f"| {CFG_LABEL[c]} | " + " | ".join(str(pba.get(str(k), pba.get(k, "–"))) for k in ks) + " |")
    return L


def strata(rows: list[dict], meta: dict) -> list[str]:
    rows = [r for r in rows if r["problem_id"] in meta]
    cfgs = [c for c in ("full", "no_rag", "no_critic", "baseline_a") if any(r["config"] == c for r in rows)]
    L = []
    for key, title in (("split", "Split"), ("area", "Area"), ("difficulty", "Level")):
        values = sorted({meta[r["problem_id"]].get(key) for r in rows}, key=str)
        L += [f"*{title}* — pass@1 (solved/n)", "", f"| {title} | " + " | ".join(CFG_LABEL[c] for c in cfgs) + " |", "|---|" + "---|" * len(cfgs)]
        for v in values:
            cells = []
            for c in cfgs:
                rs = [r for r in rows if r["config"] == c and meta[r["problem_id"]].get(key) == v]
                k = sum(r["status"] == "done" for r in rs)
                cells.append(f"{k}/{len(rs)} ({k / len(rs):.2f})" if rs else "–")
            L.append(f"| {v} | " + " | ".join(cells) + " |")
        L.append("")
    return L


def mcnemar(rows: list[dict]) -> list[str]:
    by = {(r["config"], r["problem_id"]): r for r in rows}
    probs = list(dict.fromkeys(r["problem_id"] for r in rows))
    cfgs = [c for c in ("full", "no_rag", "no_critic", "baseline_a") if any(r["config"] == c for r in rows)]
    ok = lambda c, p: by.get((c, p), {}).get("status") == "done"
    pairs = list(combinations(cfgs, 2))
    alpha = 0.05 / max(len(pairs), 1)
    L = [f"Exact McNemar tests on paired outcomes (α = 0.05, Bonferroni over {len(pairs)} pairs → {alpha:.4f}).", "", "| A | B | only A | only B | p | significant |", "|---|---|---|---|---|---|"]
    for a, b in pairs:
        n01 = sum(1 for p in probs if ok(a, p) and not ok(b, p))
        n10 = sum(1 for p in probs if ok(b, p) and not ok(a, p))
        n = n01 + n10
        pval = min(1.0, 2 * sum(comb(n, k) for k in range(0, min(n01, n10) + 1)) / 2**n) if n else 1.0
        L.append(f"| {CFG_LABEL[a]} | {CFG_LABEL[b]} | {n01} | {n10} | {pval:.3f} | {'yes' if pval < alpha else 'no'} |")
    if any(r["config"] == "baseline_b" for r in rows) and "baseline_a" in cfgs:
        self_ok = [p for p in probs if by.get(("baseline_b", p), {}).get("self_assessed_correct")]
        fp = sum(1 for p in self_ok if not ok("baseline_a", p))
        L += ["", f"**False positives avoided** (proposal §8.3): baseline B self-assessed its proof as correct on {len(self_ok)}/{len(probs)} problems; on {fp} of them the direct formalization (baseline A) is rejected by the kernel."]
    return L


def cost(rows: list[dict], run_dir: Path) -> str:
    """Tokens de los resultados + costo exacto de OpenRouter (`usage.cost`) sumado de las trazas,
    que existe desde el 22-09 (DECISIONS #59); para las llamadas anteriores sólo hay tokens."""
    ti = sum(r["tokens_in"] for r in rows)
    to = sum(r["tokens_out"] for r in rows)
    calls = sum(r["llm_calls"] for r in rows)
    exact, n_exact, n_llm = 0.0, 0, 0
    for f in (run_dir / "traces").glob("*.jsonl"):
        for l in f.read_text(encoding="utf-8").splitlines():
            e = json.loads(l)
            if e.get("agent") in ("planner", "generator", "formalizer", "critic", "baseline_a", "baseline_b") and "tokens_in" in e.get("request", {}):
                n_llm += 1
                if "cost_usd" in e["request"]:
                    exact += e["request"]["cost_usd"]; n_exact += 1
    return f"Total: {len(rows)} (problem, configuration) pairs, {calls} LLM calls, {ti / 1e6:.2f} M input / {to / 1e6:.2f} M output tokens. Exact provider cost recorded for {n_exact} of {n_llm} traced calls: US${exact:.3f} (earlier calls predate per-call cost logging)."


def full_index_table(main_summary: dict, full_summary: dict) -> list[str]:
    L = ["| Configuration | Index | n | pass@1 | Wilson 95 % | Lemma-use precision | Retrieval calls |", "|---|---|---|---|---|---|---|"]
    for c in ("full", "no_critic"):
        for label, s in (("3 903-row sample", main_summary.get(c)), ("full Mathlib (278 160)", full_summary.get(c))):
            if s:
                L.append(f"| {CFG_LABEL[c]} | {label} | {s['n']} | {s['pass@1']:.3f} | {_ci(s.get('pass@1_wilson95'))} | {s.get('lemma_use_precision', '–')} | {s.get('retrieval_calls_mean', '–')} |")
    return L


def build_section(run_dir: Path, problems: Path, full_run_dir: Path | None) -> str:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    rows = _rows(run_dir)
    meta = {json.loads(l)["problem_id"]: json.loads(l) for l in problems.read_text(encoding="utf-8").splitlines() if l.strip()}
    n_probs = len({r["problem_id"] for r in rows})
    L = [f"### 6.3 Main experiment (`{run_dir.name}`, {n_probs} problems × {len(summary)} configurations)", "",
         "Single model for every configuration (`deepseek/deepseek-v4-flash-0731`, reasoning off, temperature 0.2, seed 13); 3 903-row Mathlib table; k_max = 3; budget 20 calls. Tables generated by `scripts/fill_final_report.py` from `summary.json` and `results.jsonl`.", "",
         *main_table(summary), ""]
    pba = pass_by_attempt(summary)
    if pba:
        L += ["**Pass by attempt** (cumulative pass@1 after k attempts; marginal gain of the critic loop, §8.3):", "", *pba, ""]
    L += ["**Stratified results** (`" + problems.name + "`; dev = prompts/thresholds tuned on it, test = held out):", "", *strata(rows, meta)]
    L += ["**Paired comparisons**", "", *mcnemar(rows), ""]
    L += ["**Cost.** " + cost(rows, run_dir), ""]
    if full_run_dir and (full_run_dir / "summary.json").exists():
        fs = json.loads((full_run_dir / "summary.json").read_text(encoding="utf-8"))
        L += [f"**Effect of the index size** (`{full_run_dir.name}`: `full` and `no critic` re-run with the full Mathlib index, everything else identical; `no RAG` and the baselines do not depend on the index):", "", *full_index_table(summary, fs), ""]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="eval-v1")
    ap.add_argument("--full-index-run-id", default="eval-v1-full")
    ap.add_argument("--problems", default="data/eval/problems/eval_v1.jsonl")
    ap.add_argument("--report", default="docs/final_report.md")
    args = ap.parse_args()

    runs = Path("data/eval/runs")
    section = build_section(runs / args.run_id, Path(args.problems), runs / args.full_index_run_id if args.full_index_run_id else None)
    p = Path(args.report)
    text = p.read_text(encoding="utf-8")
    new, n = re.subn(r"### 6\.3 Main experiment.*?(?=\n## 7\. Discussion)", section.rstrip("\n") + "\n", text, count=1, flags=re.S)
    if n != 1:
        raise SystemExit("no se encontró el bloque §6.3 … ## 7 en " + str(p))
    new = new.replace("Main experiment **[TODO eval_v1]**:", "Main experiment:")
    p.write_text(new, encoding="utf-8")
    print(f"§6.3 reescrita en {p} ({len(section.splitlines())} líneas)")


if __name__ == "__main__":
    main()
