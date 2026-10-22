"""Análisis de eval-v4 / eval-v4b (fuera de miniF2F y los 7 difíciles), 0 LLM. Reejecutable mientras corre eval-v4b."""
import collections, glob, json, math, os, re
from math import comb

def wilson(k, n, z=1.96):
    if not n: return (0, 0)
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round((c - h) / d, 3), round((c + h) / d, 3))

def mcnemar(a, b):
    n = a + b
    return 1.0 if n == 0 else round(min(1.0, 2 * sum(comb(n, i) for i in range(min(a, b) + 1)) / 2 ** n), 4)

def load(run):
    path = f"data/eval/runs/{run}/results.jsonl"
    return {(r["config"], r["problem_id"]): r for r in map(json.loads, open(path))} if os.path.exists(path) else {}

def cost(run, config):  # US$ de las llamadas de pago, desde las trazas
    return round(sum(float(x) for f in glob.glob(f"data/eval/runs/{run}/traces/{run}:{config}:*.jsonl") for x in re.findall(r'"cost_usd": ?([0-9.e-]+)', open(f).read())), 4)

# (nombre, corrida, config); el primero de cada conjunto es el control
SETS = {
    "hard7": [("baseline_prover16", "eval-v4-unsolved", "baseline_prover16"), ("baseline_prover32", "eval-v4-unsolved", "baseline_prover32"), ("dsp", "eval-v4-unsolved", "dsp"),
              ("repair", "eval-v4-unsolved", "repair"), ("sketch (v4)", "eval-v4-unsolved", "sketch"), ("rag_proofs", "eval-v4-unsolved", "rag_proofs"), ("sketch (v4b)", "eval-v4b-unsolved", "sketch")],
    "pdf_set": [("baseline_prover", "eval-v4-pdf", "baseline_prover"), ("baseline_prover16", "eval-v4-pdf", "baseline_prover16"), ("dsp8", "eval-v4-pdf", "dsp8"),
                ("rag_proofs", "eval-v4-pdf", "rag_proofs"), ("sketch (v4)", "eval-v4-pdf", "sketch"), ("sketch (v4b)", "eval-v4b-pdf", "sketch")],
}
pids = {"hard7": [json.loads(l)["problem_id"] for l in open("data/eval/problems/eval_v3_unsolved_provable.jsonl")],
        "pdf_set": [json.loads(l)["problem_id"] for l in open("data/eval/problems/pdf_set.jsonl")] if os.path.exists("data/eval/problems/pdf_set.jsonl") else []}
out = {}
for s, cols in SETS.items():
    res = {run: load(run) for _, run, _ in cols}
    ok = lambda run, c, p: (c, p) in res[run] and res[run][(c, p)]["status"] == "done"
    ctl = {p for p in pids[s] if ok(cols[0][1], cols[0][2], p)}
    out[s] = {}
    for name, run, c in cols:
        done = [p for p in pids[s] if (c, p) in res[run]]
        if not done: continue
        A = {p for p in done if ok(run, c, p)}; B = ctl & set(done)
        rows = [res[run][(c, p)] for p in done]
        out[s][name] = {"k": len(A), "n": len(done), "wilson95": wilson(len(A), len(done)), "solved": sorted(A),
                        "solved_by": dict(collections.Counter(res[run][(c, p)]["solved_by"] for p in A)),
                        "vs_control": {"solo_config": len(A - B), "solo_control": len(B - A), "p_mcnemar": mcnemar(len(A - B), len(B - A))},
                        "cost_usd": cost(run, c), "llm_calls": sum(r["llm_calls"] for r in rows), "wall_h": round(sum(r["wall_seconds"] or 0 for r in rows) / 3600, 2)}
json.dump(out, open("data/eval/results/eval_v4_analysis.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps(out, indent=1, ensure_ascii=False))
