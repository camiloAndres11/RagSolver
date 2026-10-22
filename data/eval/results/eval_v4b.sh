#!/usr/bin/env bash
# eval-v4b (DECISIONS #128): SKETCH otra vez, con sus dos arreglos (tope de tokens propio; pasos "stale" tras un
# reinicio del REPL), primero sobre los 7 difíciles; luego lo que faltó de eval-v4 en pdf_set (WSL se reinició el
# 2026-10-21 a las 18:43) y SKETCH sobre los fallos de pdf_set.                                  ~US$0.25
#   0. comprobación con Lean real del límite de heartbeats en bloques de varias líneas (si falla, no sigue)
#   1. los 7 difíciles × sketch                         (run nuevo: eval-v4b-unsolved; control en eval-v4-unsolved)
#   2. pdf_set × dsp8,baseline_prover16                 (lo que faltaba de eval-v4-pdf)
#   3. pdf_set × sketch                                 (run nuevo: eval-v4b-pdf; sólo cuestan los fallos)
# Lanzar: setsid nohup bash data/eval/results/eval_v4b.sh > data/eval/results/eval_v4b.log 2>&1 < /dev/null &
set -u
cd "$(dirname "$0")/../../.."
export RAGSOLVER_MATHLIB_TABLE=mathlib_full PYTHONUNBUFFERED=1
run() { bash scripts/prover_server.sh start || exit 1; .venv/bin/python -m ragsolver.orchestrator.run --problems "$1" --configs "$2" --run-id "$3" --workers 3 2>&1 | grep --line-buffered -vE "Warning|warn\(|Loading weights|_fork_exec"; }

echo "=== 0/3 Lean real: límite de heartbeats en bloques $(date '+%F %H:%M') ==="
.venv/bin/python -m pytest -q -m slow tests/test_goal_mode_slow.py -k heartbeat_limit || { echo "ABORTO: el envoltorio de heartbeats no funciona en Lean"; exit 1; }
echo "=== 1/3 7 difíciles × sketch $(date '+%F %H:%M') ==="
run data/eval/problems/eval_v3_unsolved_provable.jsonl sketch eval-v4b-unsolved
echo "=== 2/3 pdf_set × dsp8,baseline_prover16 $(date '+%F %H:%M') ==="
run data/eval/problems/pdf_set.jsonl dsp8,baseline_prover16 eval-v4-pdf
echo "=== 3/3 pdf_set × sketch $(date '+%F %H:%M') ==="
run data/eval/problems/pdf_set.jsonl sketch eval-v4b-pdf
echo "=== FIN $(date '+%F %H:%M') ==="
bash scripts/prover_server.sh stop
