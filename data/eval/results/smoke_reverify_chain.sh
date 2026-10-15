#!/bin/bash
cd /mnt/c/Users/USER/Desktop/RagSolver
.venv/bin/python data/eval/results/smoke_used_constants.py > data/eval/results/smoke_used_constants.log 2>&1
.venv/bin/python -m pytest -q -m slow tests/test_verify_slow.py tests/test_goal_mode_slow.py > data/eval/results/slow_tests.log 2>&1
echo REVERIFY DONE
