#!/bin/bash
# eval-v2, opción B del usuario (2026-10-15): sólo rondas 2 y 3 con el saldo actual. full y no_rag
# (baseline_auto ya está completa; el runner salta lo hecho). Tope {0.13, 0.30}, 4 hilos. Guardia tras cada ronda.
cd /mnt/c/Users/USER/Desktop/RagSolver
export RAGSOLVER_MATHLIB_TABLE=mathlib_full
for k in 2 3; do
  .venv/bin/python -m ragsolver.orchestrator.run --problems data/eval/problems/eval_v1_round$k.jsonl --configs full,no_rag,baseline_auto --run-id eval-v2 --workers 4 >> data/eval/results/eval_v2_round$k.log 2>&1
  .venv/bin/python data/eval/results/round_check.py $k 3 > data/eval/results/eval_v2_round${k}_check.json 2>&1
  if ! python3 -c "import json,sys; d=json.load(open('data/eval/results/eval_v2_round${k}_check.json')); sys.exit(1 if d['alerts'] else 0)"; then
    echo "ROUND $k ALERT -> STOP"; exit 1
  fi
  echo "ROUND $k OK"
done
echo "EVAL-V2 (B) DONE"
