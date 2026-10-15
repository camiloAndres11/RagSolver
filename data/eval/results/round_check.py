"""Chequeo entre rondas de eval-v2 (docs/PLAN_MEJORA_RAG.md Fase 6): costo real de la ronda (key de
OpenRouter y línea [cost] del runner), llamadas por par (presupuesto RNF-3 = 20), errores, reintentos 429
y proyección del costo de las rondas restantes. Uso: python round_check.py <n_ronda> [última_ronda=4]"""
import json, os, re, sys
import httpx

RUN = os.environ.get("RS_RUN", "eval-v2")        # eval-v3: rediseño con demostrador local
LOGP = os.environ.get("RS_LOG", "eval_v2")
BUDGET = os.environ.get("RS_BUDGET", "data/eval/results/eval_v2_budget.json")
FREE = {"baseline_auto", "baseline_prover"}       # configs sin API

k = int(sys.argv[1])
last = int(sys.argv[2]) if len(sys.argv) > 2 else 4  # opción B del usuario (2026-10-15): sólo hasta la ronda 3
pids = {json.loads(l)["problem_id"] for l in open(f"data/eval/problems/eval_v1_round{k}.jsonl")}
rows = [json.loads(l) for l in open(f"data/eval/runs/{RUN}/results.jsonl")]
rnd = [r for r in rows if r["problem_id"] in pids]
log = open(f"data/eval/results/{LOGP}_round{k}.log", encoding="utf-8").read()
cost_line = re.findall(r"\[cost\] (\d+) llamadas, (\d+) tokens de entrada, (\d+) de salida, US\$([\d.]+)", log)
env = dict(l.strip().split("=", 1) for l in open(".env", encoding="utf-8") if "=" in l and not l.lstrip().startswith("#"))
key = httpx.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": f"Bearer {env['OPENROUTER_API_KEY'].strip().strip(chr(34))}"}, timeout=30).json()["data"]
budget = json.load(open(BUDGET))
prev = budget.get(f"after_round{k - 1}_usage", budget["before_round1_usage"])
budget[f"after_round{k}_usage"] = key["usage"]
json.dump(budget, open(BUDGET, "w"), indent=1)
spent = key["usage"] - prev
out = {"round": k, "pairs": len(rnd), "key_spent_round": round(spent, 4), "runner_cost": cost_line[-1] if cost_line else None,
       "key_remaining": round(key["limit_remaining"], 4), "retries_429": log.count("429 Too Many Requests"), "status": {}, "llm_calls_max": {}, "llm_calls_mean": {}, "solved": {}}
for cfg in sorted({r["config"] for r in rnd}):
    rs = [r for r in rnd if r["config"] == cfg]
    out["status"][cfg] = {s: sum(r["status"] == s for r in rs) for s in ("done", "fail", "error")}
    out["llm_calls_max"][cfg] = max(r["llm_calls"] for r in rs)
    out["llm_calls_mean"][cfg] = round(sum(r["llm_calls"] for r in rs) / len(rs), 2)
    out["solved"][cfg] = sum(r["status"] == "done" for r in rs)
paid = [r for r in rnd if r["config"] not in FREE]
out["cost_per_paid_pair"] = round(spent / len(paid), 5) if paid else None
out["projection_remaining_rounds"] = round(spent * (last - k), 4)
out["alerts"] = [a for a, bad in (
    ("presupuesto RNF-3 superado (>20 llamadas)", any(v > 20 for v in out["llm_calls_max"].values())),
    ("costo por par > US$0.006 (2x lo esperado)", (out["cost_per_paid_pair"] or 0) > 0.006),
    ("pares con status=error > 10 %", sum(r["status"] == "error" for r in rnd) > 0.1 * len(rnd)),
    ("una línea base sin API gastó LLM", any(r["llm_calls"] for r in rnd if r["config"] in FREE)),
    ("proyección supera el saldo", out["projection_remaining_rounds"] > key["limit_remaining"]),
) if bad]
print(json.dumps(out, indent=1, ensure_ascii=False))
