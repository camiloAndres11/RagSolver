#!/usr/bin/env bash
# Experimento principal (propuesta §8): 5 configuraciones × 60 problemas de eval_v1, reanudable.
# Uso: nohup bash scripts/run_eval_v1.sh > data/eval/results/eval_v1.log 2>&1 &
# Si corta por cuota diaria ("quota_exhausted"), volver a lanzar el mismo comando al día siguiente.
set -u
cd "$(dirname "$0")/.."
export PATH="$HOME/.elan/bin:$PATH" PYTHONUNBUFFERED=1
F() { grep --line-buffered -v "Warning\|Loading\|it/s\|prompt name"; }
echo "=== EVAL_V1 $(date '+%F %H:%M') mem_avail=$(free -m | awk '/Mem:/{print $7}')MB ==="
uv run python -m ragsolver.orchestrator.run --problems data/eval/problems/eval_v1.jsonl --configs full,no_rag,no_critic,baseline_a,baseline_b --run-id eval-v1 --workers 6 2>&1 | F
echo "=== REPORT $(date '+%F %H:%M') ==="
uv run python scripts/eval_report.py --run-id eval-v1 --problems data/eval/problems/eval_v1.jsonl 2>&1 | F
echo "EVAL_V1 DONE $(date '+%F %H:%M')"
