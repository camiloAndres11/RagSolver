#!/bin/bash
cd /mnt/c/Users/USER/Desktop/RagSolver
export RAGSOLVER_MATHLIB_TABLE=mathlib_full
.venv/bin/python scripts/probe_close.py > data/eval/results/probe_close/battery_rerun.log 2>&1
.venv/bin/python -m pytest -q -m slow tests/test_verify_slow.py > data/eval/results/slow_tests.log 2>&1
echo RERUN DONE
