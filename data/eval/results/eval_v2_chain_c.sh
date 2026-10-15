#!/bin/bash
# eval-v2 en 4 rondas de 15 problemas (docs/PLAN_MEJORA_RAG.md Fase 6) SIN `rag_nl` (aislada a pedido
# del usuario, DECISIONS #106): full, no_rag, baseline_auto. Tope de precio con 8 proveedores y 4 hilos (#107). Tras cada ronda, round_check.py; cualquier
# alerta (costo, presupuesto RNF-3, errores, proyección > saldo) detiene la cadena.
cd /mnt/c/Users/USER/Desktop/RagSolver
export RAGSOLVER_MATHLIB_TABLE=mathlib_full
for k in 1 2 3 4; do
  .venv/bin/python -m ragsolver.orchestrator.run --problems data/eval/problems/eval_v1_round$k.jsonl --configs full,no_rag,baseline_auto --run-id eval-v2 --workers 4 >> data/eval/results/eval_v2_round$k.log 2>&1
  .venv/bin/python data/eval/results/round_check.py $k > data/eval/results/eval_v2_round${k}_check.json 2>&1
  if ! python3 -c "import json,sys; d=json.load(open('data/eval/results/eval_v2_round${k}_check.json')); sys.exit(1 if d['alerts'] else 0)"; then
    echo "ROUND $k ALERT -> STOP"; exit 1
  fi
  echo "ROUND $k OK"
done
echo "EVAL-V2 DONE"
