#!/bin/bash
# docs/PLAN_MEJORA_RAG.md Fases 0, 3 y 4: mediciones 0-LLM, en serie (una carga pesada a la vez).
cd /mnt/c/Users/USER/Desktop/RagSolver
.venv/bin/python scripts/build_premise_gold_goals.py > data/eval/results/build_gold_goals.log 2>&1
export RAGSOLVER_MATHLIB_TABLE=mathlib_full
.venv/bin/python scripts/eval_premises_goals.py > data/eval/results/premises_goals.log 2>&1
.venv/bin/python scripts/probe_close.py > data/eval/results/probe_close/battery.log 2>&1
echo MEASURE DONE
