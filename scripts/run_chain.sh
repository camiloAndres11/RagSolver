#!/usr/bin/env bash
# Cadena completa y reanudable del experimento principal (reemplaza run_eval_v1 -> run_full_index -> run_morning):
#   1. eval-v1       60 problemas × 5 configs, tabla de muestra de Mathlib
#   2. eval-v1-full  `full` y `no_critic` con el índice completo (278 160, ANN IVF_SQ); `no_rag` y las líneas
#                    base no dependen del índice y se copian de eval-v1
#   3. cierre        informes, §6.3 del informe final, resumen en data/eval/results/chain_summary.md, métricas QR con el índice ANN
# Cada etapa se reanuda sola hasta 4 veces (caída del proceso, red) y se corta limpio si la key no tiene crédito;
# volver a lanzar el mismo comando continúa donde quedó.
# Uso: setsid nohup bash scripts/run_chain.sh > data/eval/results/chain.log 2>&1 < /dev/null &
set -u
cd "$(dirname "$0")/.."
export PATH="$HOME/.elan/bin:$PATH" PYTHONUNBUFFERED=1
F() { grep --line-buffered -v "Warning\|Loading\|it/s\|prompt name\|_fork_exec\|WARN lance"; }
step() { echo "=== $1 $(date '+%F %H:%M') mem_avail=$(free -m | awk '/Mem:/{print $7}')MB ==="; }
PROBLEMS=data/eval/problems/eval_v1.jsonl
CONFIGS=full,no_rag,no_critic,baseline_a,baseline_b

key_left() {
  uv run python - <<'PY' 2>/dev/null || echo 0
import httpx
key = [l.split("=", 1)[1].strip() for l in open(".env") if l.startswith("OPENROUTER_API_KEY=")][0]
d = httpx.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": "Bearer " + key}, timeout=30).json()["data"]
print(round(d.get("limit_remaining") or 0, 3))
PY
}

pending() {  # pares (config, problema) sin resultado válido en data/eval/runs/$1
  uv run python - "$1" "$PROBLEMS" "$CONFIGS" <<'PY'
import json, sys
from pathlib import Path
run, problems, configs = sys.argv[1:4]
probs = [json.loads(l)["problem_id"] for l in open(problems, encoding="utf-8") if l.strip()]
p = Path("data/eval/runs") / run / "results.jsonl"
rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []
done = {(r["config"], r["problem_id"]) for r in rows if r["status"] != "error"}
print(sum((c, q) not in done for c in configs.split(",") for q in probs))
PY
}

run_until_done() {  # $1 run-id
  for i in 1 2 3 4; do
    n=$(pending "$1"); [ "$n" = "0" ] && return 0
    left=$(key_left)
    awk "BEGIN{exit !($left > 0.02)}" || { echo "[chain] sin crédito en la key (US\$$left): $n pares pendientes en $1"; return 1; }
    step "$1 intento $i: $n pares pendientes, key US\$$left"
    uv run python -m ragsolver.orchestrator.run --problems "$PROBLEMS" --configs "$CONFIGS" --run-id "$1" --workers 6 2>&1 | F
  done
  [ "$(pending "$1")" = "0" ]
}

# 1. eval-v1 -------------------------------------------------------------------------------------------
run_until_done eval-v1; ok1=$?
step "REPORT eval-v1"
uv run python scripts/eval_report.py --run-id eval-v1 --problems "$PROBLEMS" 2>&1 | F

# 2. eval-v1-full ----------------------------------------------------------------------------------------
ok2=1
if [ $ok1 = 0 ]; then
  step "eval-v1-full: índice ANN y copia de configs independientes del índice"
  uv run python - <<'PY' 2>&1 | F
import json, lancedb
from pathlib import Path
from ragsolver.rag.index import build_ann_index
t = lancedb.connect("data/index/lancedb").open_table("mathlib_full")
if not any("IVF" in str(i.index_type).upper() for i in t.list_indices()):
    build_ann_index(t); print("índice ANN construido")
src = [json.loads(l) for l in open("data/eval/runs/eval-v1/results.jsonl", encoding="utf-8") if l.strip()]
dst_p = Path("data/eval/runs/eval-v1-full/results.jsonl"); dst_p.parent.mkdir(parents=True, exist_ok=True)
dst = [json.loads(l) for l in dst_p.read_text(encoding="utf-8").splitlines() if l.strip()] if dst_p.exists() else []
have = {(r["config"], r["problem_id"]) for r in dst}
new = [r for r in src if r["config"] in ("no_rag", "baseline_a", "baseline_b") and (r["config"], r["problem_id"]) not in have]
with dst_p.open("a", encoding="utf-8") as f:
    f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in new)
print(f"copiadas de eval-v1: {len(new)} (ya había {len(dst)})")
PY
  RAGSOLVER_MATHLIB_TABLE=mathlib_full run_until_done eval-v1-full; ok2=$?
  step "REPORT eval-v1-full"
  RAGSOLVER_MATHLIB_TABLE=mathlib_full uv run python scripts/eval_report.py --run-id eval-v1-full --problems "$PROBLEMS" --out docs/eval_report_full_index.md 2>&1 | F
fi

# 3. cierre -------------------------------------------------------------------------------------------------
step "cierre: §6.3 del informe final y PROGRESS"
uv run python scripts/fill_final_report.py --run-id eval-v1 --full-index-run-id eval-v1-full --problems "$PROBLEMS" 2>&1 | F
uv run python - "$ok1" "$ok2" <<'PY'
import json, sys, time
from pathlib import Path
ok1, ok2 = sys.argv[1:3]
L = [f"\n## Resultado automático de la cadena ({time.strftime('%Y-%m-%d %H:%M')})\n", f"eval-v1 completo: {'sí' if ok1 == '0' else 'NO'} · eval-v1-full completo: {'sí' if ok2 == '0' else 'NO'}\n", "| corrida | config | n | pass@1 | Wilson 95 % | llamadas LLM | tokens |", "|---|---|---|---|---|---|---|"]
for run in ("eval-v1", "eval-v1-full"):
    p = Path("data/eval/runs") / run / "summary.json"
    if p.exists():
        for c, s in json.loads(p.read_text(encoding="utf-8")).items():
            L.append(f"| {run} | {c} | {s['n']} | {s['pass@1']} | {s['pass@1_wilson95']} | {s['llm_calls_mean']} | {s['tokens_mean']} |")
L += ["", "Detalle: `docs/eval_report.md`, `docs/eval_report_full_index.md`, `docs/final_report.md` §6.3. Log: `data/eval/results/chain.log`.", ""]
# docs/PLAN_CORRECCIONES.md Fase 3, tarea 5: el resumen automático ya no se agrega a
# docs/PROGRESS.md (documentación editada a mano; auto-escribirla la desincroniza).
Path("data/eval/results/chain_summary.md").open("a", encoding="utf-8").write("\n".join(L))
print("data/eval/results/chain_summary.md actualizado")
PY
if [ ! -f data/eval/results/fase1_full_ann_mathlibqr_100q.json ]; then
  step "MathlibQR 100q sobre el índice completo con ANN (sin costo de API)"
  uv run python -m ragsolver.rag.evaluate --table mathlib_full --sample 100 --out data/eval/results/fase1_full_ann_mathlibqr_100q.json 2>&1 | F | tail -2
  uv run python scripts/eval_report.py --run-id eval-v1 --problems "$PROBLEMS" 2>&1 | F
fi
echo "CHAIN DONE $(date '+%F %H:%M') eval-v1=$ok1 eval-v1-full=$ok2"
