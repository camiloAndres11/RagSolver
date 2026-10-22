#!/usr/bin/env bash
# Todo lo de la demo con un comando: demostrador en la GPU + demo Gradio en http://127.0.0.1:7860.
# La demo precalienta Lean (`import Mathlib`, ~5 min) y el embedder en segundo plano al arrancar:
# lánzala unos minutos antes de presentar. Ctrl+C cierra la demo; el demostrador sigue hasta
# `bash scripts/prover_server.sh stop`.
set -eu
cd "$(dirname "$0")/.."
bash scripts/prover_server.sh start
export RAGSOLVER_MATHLIB_TABLE="${RAGSOLVER_MATHLIB_TABLE:-mathlib_full}" PYTHONUNBUFFERED=1
exec .venv/bin/python demo/app.py
