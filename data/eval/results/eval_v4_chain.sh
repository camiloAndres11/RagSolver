#!/usr/bin/env bash
# eval-v4 (2026-10-21, tercera versión: lo que más informa primero; DECISIONS #123-#127).
#   hecho: pdf_set × baseline_prover (21/32); 6 de los 9 difíciles × baseline_prover32 (0/6); 4 × repair (0/4)
#   1. los 7 difíciles demostrables × {sketch (REPAIR + esqueleto Lean), dsp, baseline_prover16}   ~US$0.08, ~3 h
#   2. comprobación con Lean de los 2 enunciados falsos (un solo REPL cabe en la RAM: va entre pasos)
#   3. pdf_set × {sketch, dsp8 (sin ejemplares del PDF), baseline_prover16}: sólo cuestan los 11 fallos  ~US$0.08, ~5 h
# `sketch` incluye REPAIR: `solved_by` (prover_repair / prover_sketch) dice qué etapa resolvió, así que no hace
# falta una corrida aparte de `repair`. Las 8 primeras muestras de cada problema están en caché.
# Cada paso es reanudable (mismo --run-id). Al final apaga el demostrador (libera la GPU).
# Lanzar: setsid nohup bash data/eval/results/eval_v4_chain.sh > data/eval/results/eval_v4_chain.log 2>&1 < /dev/null &
set -u
cd "$(dirname "$0")/../../.."
export RAGSOLVER_MATHLIB_TABLE=mathlib_full PYTHONUNBUFFERED=1
# antes de cada paso: el demostrador arriba (lo relanza si murió; eval-v4 perdió una noche por eso)
run() { bash scripts/prover_server.sh start || exit 1; .venv/bin/python -m ragsolver.orchestrator.run --problems "$1" --configs "$2" --run-id "$3" --workers 3 2>&1 | grep --line-buffered -vE "Warning|warn\(|Loading weights|_fork_exec"; }

echo "=== 1/3 7 difíciles × sketch,dsp,baseline_prover16 $(date '+%F %H:%M') ==="
run data/eval/problems/eval_v3_unsolved_provable.jsonl sketch,dsp,baseline_prover16 eval-v4-unsolved
echo "=== 2/3 enunciados falsos $(date '+%F %H:%M') ==="
(cd lean_project && timeout 1200 lake env lean ../data/eval/results/false_statements/FalseStatements.lean && echo "OK: las dos negaciones verifican")
echo "=== 3/3 pdf_set × sketch,dsp8,baseline_prover16 $(date '+%F %H:%M') ==="
run data/eval/problems/pdf_set.jsonl sketch,dsp8,baseline_prover16 eval-v4-pdf
echo "=== FIN $(date '+%F %H:%M') ==="
bash scripts/prover_server.sh stop
