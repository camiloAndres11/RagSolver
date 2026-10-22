#!/usr/bin/env bash
# eval-v4, segunda parte (2026-10-21; DECISIONS #127): toma el relevo de eval_v4_chain.sh cuando termina su paso 1
# (los 7 difíciles × sketch, dsp, control) para medir antes el RAG de pruebas:
#   1. corta la cadena al empezar su paso 2 (la comprobación con Lean se hace aquí; no se pierde nada)
#   2. comprobación con Lean de los 2 enunciados falsos
#   3. índice `proofs` (Lean Workbook, embeddings en la GPU con el demostrador apagado) + vista previa de lo que
#      recupera para cada problema, para revisar a mano si trae el mismo problema (va en runs/: gitignored, SEC-2)
#   4. los 7 difíciles × rag_proofs                                                             0 API
#   5. pdf_set × {rag_proofs, sketch, dsp8, baseline_prover16}: sólo cuestan los 11 fallos      ~US$0.08
# Lanzar: setsid nohup bash data/eval/results/eval_v4_part2.sh > data/eval/results/eval_v4_part2.log 2>&1 < /dev/null &
set -u
cd "$(dirname "$0")/../../.."
export RAGSOLVER_MATHLIB_TABLE=mathlib_full PYTHONUNBUFFERED=1
until grep -q '^=== 2/3' data/eval/results/eval_v4_chain.log 2>/dev/null; do sleep 30; done
pkill -f 'bash data/eval/results/eval_v4_chain[.]sh'
pkill -f 'FalseStatements[.]lean'
sleep 5
run() { bash scripts/prover_server.sh start || exit 1; .venv/bin/python -m ragsolver.orchestrator.run --problems "$1" --configs "$2" --run-id "$3" --workers 3 2>&1 | grep --line-buffered -vE "Warning|warn\(|Loading weights|_fork_exec"; }

echo "=== 1/4 enunciados falsos $(date '+%F %H:%M') ==="
(cd lean_project && timeout 1200 lake env lean ../data/eval/results/false_statements/FalseStatements.lean && echo "OK: las dos negaciones verifican")

echo "=== 2/4 índice de pruebas $(date '+%F %H:%M') ==="
bash scripts/prover_server.sh stop
GW=$(ip route show default | awk '{print $3}')
setsid nohup ./tools/llama-vulkan/llama-server.exe -m tools/models/Qwen3-Embedding-0.6B-f16.gguf --embeddings --pooling last -ngl 99 \
  --host "$GW" --port 8081 -c 16384 -b 8192 -ub 8192 --parallel 8 > tools/logs/embed_server.log 2>&1 < /dev/null &
for _ in $(seq 120); do curl -sf -m 3 "http://$GW:8081/health" > /dev/null && break; sleep 5; done
export RAGSOLVER_EMBED_BACKEND=llama
.venv/bin/python -m ragsolver.rag.proofs build || { pgrep -f 'llama-server[.]exe.*--port 8081' | xargs -r kill; exit 1; }
.venv/bin/python -m ragsolver.rag.proofs preview --problems data/eval/problems/eval_v3_unsolved_provable.jsonl > data/eval/runs/eval-v4-unsolved/proofs_preview.txt
.venv/bin/python -m ragsolver.rag.proofs preview --problems data/eval/problems/pdf_set.jsonl > data/eval/runs/eval-v4-pdf/proofs_preview.txt
unset RAGSOLVER_EMBED_BACKEND
pgrep -f 'llama-server[.]exe.*--port 8081' | xargs -r kill
sleep 5

echo "=== 3/4 7 difíciles × rag_proofs $(date '+%F %H:%M') ==="
run data/eval/problems/eval_v3_unsolved_provable.jsonl rag_proofs eval-v4-unsolved
echo "=== 4/4 pdf_set × rag_proofs,sketch,dsp8,baseline_prover16 $(date '+%F %H:%M') ==="
run data/eval/problems/pdf_set.jsonl rag_proofs,sketch,dsp8,baseline_prover16 eval-v4-pdf
echo "=== FIN $(date '+%F %H:%M') ==="
bash scripts/prover_server.sh stop
