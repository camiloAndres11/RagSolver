#!/bin/bash
cd /mnt/c/Users/USER/Desktop/RagSolver
.venv/bin/python -m pytest -q -m slow tests/test_goal_mode_slow.py > data/eval/results/slow_goal_mode.log 2>&1 || { echo SLOW FAILED; exit 1; }
RAGSOLVER_MATHLIB_TABLE=mathlib_full .venv/bin/python -m ragsolver.orchestrator.run --problems data/eval/problems/smoke_goal_mode.jsonl --configs full --run-id smoke-goal-mode-v3 --workers 2 > data/eval/results/smoke_goal_mode_v3.log 2>&1
echo SMOKE DONE
