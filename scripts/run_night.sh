#!/usr/bin/env bash
# Cadena desacoplada (nohup): evaluaciones de recuperación restantes -> Fase 4 -> ablaciones -> informe.
# Uso: nohup bash scripts/run_night.sh > data/eval/results/night.log 2>&1 &
set -u
cd "$(dirname "$0")/.."
export PATH="$HOME/.elan/bin:$PATH" PYTHONUNBUFFERED=1
F() { grep --line-buffered -v "Warning\|Loading\|it/s\|prompt name"; }
step() { echo; echo "=== $1 $(date +%H:%M) mem_avail=$(free -m | awk '/Mem:/{print $7}')MB ==="; }

step "INFORMAL GOLD";   uv run python scripts/eval_informal_gold.py 2>&1 | F
step "CONTAMINATION";   uv run python scripts/contamination.py 2>&1 | F
step "QR sample100 rerank"; uv run python -m ragsolver.rag.evaluate --sample 100 2>&1 | F | tee data/eval/results/fase1_redo_mathlibqr_sample3903_100q.json
step "FASE 4 smoke (5 triviales, full)"
uv run python -m ragsolver.orchestrator.run --problems data/eval/problems/dev_trivial.jsonl --configs full --run-id f4-trivial-full 2>&1 | F
step "FASE 4/5 ablaciones (13 problemas x 5 configs)"
cat data/eval/problems/dev_trivial.jsonl data/eval/problems/dev_minif2f.jsonl > data/eval/problems/dev_all.jsonl
uv run python -m ragsolver.orchestrator.run --problems data/eval/problems/dev_all.jsonl --configs full,no_rag,no_critic,baseline_a,baseline_b --run-id f5-ablations 2>&1 | F
step "REPORT"; uv run python scripts/eval_report.py --run-id f5-ablations 2>&1 | F
echo; echo "NIGHT DONE $(date +%H:%M)"
