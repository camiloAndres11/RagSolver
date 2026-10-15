"""Re-verifica (0 LLM) las pruebas finales de smoke-goal-mode-v3 y cuenta qué citas usa de verdad el término."""
import json, textwrap
from ragsolver.lean.verify import LeanChecker
probs = {json.loads(l)["problem_id"]: json.loads(l) for l in open("data/eval/problems/smoke_goal_mode.jsonl")}
ck = LeanChecker(imports=["Mathlib", "Aesop"])
out = []
for r in map(json.loads, open("data/eval/runs/smoke-goal-mode-v3/results.jsonl")):
    p = probs[r["problem_id"]]
    body = r["lean_code"].split(":= by\n", 1)[1].rsplit("\n#print axioms", 1)[0]
    v = ck.check(p["formal_statement"], textwrap.dedent(body), p.get("header", ""))
    cited = [c["locator"]["decl_name"] for c in r["citations"] if c["corpus"] == "mathlib"]
    used = [n for n in cited if n in set(v.used_constants)]
    out.append({"problem_id": r["problem_id"], "ok": v.ok, "cited": len(cited), "used_text": r["n_used_in_proof"], "used_kernel": used})
    print(out[-1], flush=True)
ck.close()
json.dump(out, open("data/eval/results/smoke_used_constants.json", "w"), indent=1)
