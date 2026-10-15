"""Análisis de eval-v3 (rediseño con demostrador local) pareado con eval-v2 en los mismos problemas, 0 LLM."""
import collections, glob, json, math
from math import comb

def wilson(k, n, z=1.96):
    if not n: return (0, 0)
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round((c - h) / d, 3), round((c + h) / d, 3))

def mcnemar(a, b):
    n = a + b
    return 1.0 if n == 0 else round(min(1.0, 2 * sum(comb(n, i) for i in range(min(a, b) + 1)) / 2 ** n), 4)

meta = {json.loads(l)["problem_id"]: json.loads(l) for l in open("data/eval/problems/eval_v1.jsonl")}
v2 = {(r["config"], r["problem_id"]): r for r in map(json.loads, open("data/eval/runs/eval-v2/results.jsonl"))}
v3 = {(r["config"], r["problem_id"]): r for r in map(json.loads, open("data/eval/runs/eval-v3/results.jsonl"))}
pids = [p for p in meta if ("full", p) in v3 and ("baseline_prover", p) in v3]
ok = lambda src, c, p: bool(src.get((c, p))) and src[(c, p)]["status"] == "done"
cols = [("v3 full", v3, "full"), ("v3 baseline_prover", v3, "baseline_prover"), ("v2 full", v2, "full"), ("v2 no_rag", v2, "no_rag"), ("v2 baseline_auto", v2, "baseline_auto")]
out = {"n": len(pids), "rates": {}, "by_difficulty": {}, "mcnemar": {}, "solved_by": {}, "prover": {}, "per_problem": []}
for name, src, c in cols:
    k = sum(ok(src, c, p) for p in pids); out["rates"][name] = {"k": k, "n": len(pids), "wilson95": wilson(k, len(pids))}
    out["by_difficulty"][name] = {d: f"{sum(ok(src, c, p) for p in pids if meta[p]['difficulty'] == d)}/{sum(meta[p]['difficulty'] == d for p in pids)}" for d in (1, 2, 3)}
for (an, asrc, ac), (bn, bsrc, bc) in [(cols[0], cols[2]), (cols[0], cols[3]), (cols[1], cols[4]), (cols[0], cols[1])]:
    A = {p for p in pids if ok(asrc, ac, p)}; B = {p for p in pids if ok(bsrc, bc, p)}
    out["mcnemar"][f"{an} vs {bn}"] = {"solo_A": len(A - B), "solo_B": len(B - A), "p": mcnemar(len(A - B), len(B - A))}
out["solved_by"] = dict(collections.Counter(v3[("full", p)]["solved_by"] for p in pids if ok(v3, "full", p)))
# trazas del demostrador: dónde resolvió, correcciones con RAG, sugerencias usadas por el kernel
stages = collections.Counter(); corr_rag = 0; suggested_used = 0; suggested_total = 0
for f in glob.glob("data/eval/runs/eval-v3/traces/eval-v3:*.jsonl"):
    for l in open(f):
        e = json.loads(l); d = (e.get("request") or {}).get("decision") or {}
        if e["agent"] == "prover":
            stages[(f.split(":")[1], d.get("stage"), d.get("solved_in", "unsolved"))] += 1
            corr_rag += d.get("corrections_with_rag", 0)
for p in pids:
    r = v3[("full", p)]
    for c in r["citations"]:
        if c["corpus"] == "mathlib" and c["retrieved_for"].get("agent") == "prover":
            suggested_total += 1; suggested_used += bool(c.get("used_in_proof"))
out["prover"] = {"stages": {"|".join(map(str, k)): v for k, v in stages.items()}, "corrections_with_rag": corr_rag, "rag_name_suggestions_cited": suggested_total, "rag_name_suggestions_used_by_kernel": suggested_used,
                 "samples_full_mean": round(sum(v3[("full", p)]["prover_samples"] for p in pids) / len(pids), 1), "llm_calls_full_mean": round(sum(v3[("full", p)]["llm_calls"] for p in pids) / len(pids), 2)}
for p in pids:
    out["per_problem"].append({"problem_id": p, "difficulty": meta[p]["difficulty"], "area": meta[p]["area"], **{n: ok(s, c, p) for n, s, c in cols}, "v3_solved_by": v3[("full", p)]["solved_by"]})
json.dump(out, open("data/eval/results/eval_v3_analysis.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps({k: v for k, v in out.items() if k != "per_problem"}, indent=1, ensure_ascii=False))
