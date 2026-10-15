#!/bin/bash
# eval-v3 (docs/PLAN_REDISENO_PROVER.md): rediseño con demostrador local (Goedel-Prover-V2-8B, GPU) sobre las
# rondas 1-2 de eval-v1 (30 problemas, pareados con eval-v2). baseline_prover (0 API) primero; full después
# (reutiliza sus muestras vía caché; API sólo para lo que el demostrador no resuelve). Guardia tras cada ronda.
cd /mnt/c/Users/USER/Desktop/RagSolver
export RAGSOLVER_MATHLIB_TABLE=mathlib_full RS_RUN=eval-v3 RS_LOG=eval_v3 RS_BUDGET=data/eval/results/eval_v3_budget.json
for k in 1 2; do
  .venv/bin/python -m ragsolver.orchestrator.run --problems data/eval/problems/eval_v1_round$k.jsonl --configs baseline_prover,full --run-id eval-v3 --workers 2 >> data/eval/results/eval_v3_round$k.log 2>&1
  .venv/bin/python data/eval/results/round_check.py $k 2 > data/eval/results/eval_v3_round${k}_check.json 2>&1
  if ! python3 -c "import json,sys; d=json.load(open('data/eval/results/eval_v3_round${k}_check.json')); sys.exit(1 if d['alerts'] else 0)"; then
    echo "ROUND $k ALERT -> STOP"; exit 1
  fi
  echo "ROUND $k OK"
done
echo "EVAL-V3 DONE"
