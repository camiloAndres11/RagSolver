"""Análisis de eval-v2 (0 LLM): resultados por problema y configuración, costo por par, atribución de
cada goal cerrado (bloque del LLM / reutilizado / batería / FILL) y premisas usadas por el kernel."""
import collections, glob, json, sys
from pathlib import Path

RUN = Path("data/eval/runs/eval-v2")
rows = [json.loads(l) for l in open(RUN / "results.jsonl")]
v1 = {(r["config"], r["problem_id"]): r for r in map(json.loads, open("data/eval/runs/eval-v1/results.jsonl"))}
pids = [json.loads(l)["problem_id"] for l in open("data/eval/problems/eval_v1.jsonl")]
meta = {json.loads(l)["problem_id"]: json.loads(l) for l in open("data/eval/problems/eval_v1.jsonl")}
by = {(r["config"], r["problem_id"]): r for r in rows}
cfgs = ["full", "no_rag", "baseline_auto"]
done_pids = [p for p in pids if all((c, p) in by for c in ("full", "no_rag"))]

# costo y tokens por par (trazas de agentes)
cost = collections.defaultdict(float); tin = collections.Counter(); tout = collections.Counter(); close = collections.defaultdict(list); fills = collections.Counter()
for f in glob.glob(str(RUN / "traces" / "*.jsonl")):
    for l in open(f):
        e = json.loads(l); req = e.get("request") or {}
        key = tuple(e["run_id"].split(":")[1:3]) if e["run_id"].count(":") >= 2 else None
        if not key:
            continue
        if req.get("cost_usd") is not None:
            cost[key] += req["cost_usd"]; tin[key] += req.get("tokens_in", 0); tout[key] += req.get("tokens_out", 0)
        if e["agent"] == "close":
            close[key].append(req.get("decision") or {})
        if e["agent"] == "fill":
            fills[key] += 1

out = {"n_problems_with_full_and_no_rag": len(done_pids), "per_config": {}, "goal_attribution": {}, "per_problem": []}
for c in cfgs:
    rs = [by[(c, p)] for p in done_pids if (c, p) in by]
    keys = [(c, p) for p in done_pids if (c, p) in by]
    out["per_config"][c] = {
        "n": len(rs), "solved": sum(r["status"] == "done" for r in rs), "errors": sum(r["status"] == "error" for r in rs),
        "llm_calls_mean": round(sum(r["llm_calls"] for r in rs) / max(len(rs), 1), 2),
        "cost_usd_traces": round(sum(cost[k] for k in keys), 4), "cost_per_pair": round(sum(cost[k] for k in keys) / max(len(rs), 1), 5),
        "tokens_in_mean": round(sum(tin[k] for k in keys) / max(len(rs), 1)), "tokens_out_mean": round(sum(tout[k] for k in keys) / max(len(rs), 1)),
        "kernel_used_retrieved_premises": sum(r["n_used_in_proof"] for r in rs if r["status"] == "done"),
        "problems_with_kernel_used_premise": sum(1 for r in rs if r["status"] == "done" and r["n_used_in_proof"] > 0),
    }
    att = collections.Counter()
    for k in keys:
        for d in close[k]:
            if "goal" not in d:
                att["skeleton_error"] += 1
                continue
            tried = d.get("tried") or []
            win = next((t["tactic"] for t in tried if t.get("closed")), None)
            att["open_after_close" if not d.get("closed") else ("llm_block" if win == "llm" else "reused" if win == "reused" else (win or "?").split(" ")[0] + (" +premisas" if "[" in (win or "") or "+suggestions" in (win or "") else ""))] += 1
    out["goal_attribution"][c] = dict(att)
    out["per_config"][c]["fill_calls"] = sum(fills[k] for k in keys)
for p in done_pids:
    out["per_problem"].append({"problem_id": p, "area": meta[p]["area"], "difficulty": meta[p]["difficulty"],
                               **{c: by[(c, p)]["status"] if (c, p) in by else "-" for c in cfgs},
                               "v1_full": v1.get(("full", p), {}).get("status", "-"), "v1_no_rag": v1.get(("no_rag", p), {}).get("status", "-")})
json.dump(out, open("data/eval/results/eval_v2_analysis.json", "w"), indent=1)
print(json.dumps({k: v for k, v in out.items() if k != "per_problem"}, indent=1))
for r in out["per_problem"]:
    print(r)
