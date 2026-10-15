#!/usr/bin/env bash
# Embedding del corpus completo de Mathlib en la GPU (llama-server Vulkan del lado de Windows) y tabla `mathlib_full`.
# Requiere el servidor: tools/llama-vulkan/llama-server.exe -m <gguf> --embeddings --pooling last -ngl 99 --host 0.0.0.0 --port 8081 -c 16384 -b 8192 -ub 8192 --parallel 8
# SEC-3 (docs/AUDIT_2026-09-23.md): `--host 0.0.0.0` sin autenticación expone el servidor a toda la
# LAN mientras corre. Preferible `--host <IP de WSL>` (o el default 127.0.0.1 si WSL alcanza a Windows
# por loopback) y apagarlo (Ctrl+C) al terminar el embebido, no dejarlo corriendo de fondo.
set -u
cd "$(dirname "$0")/.."
export RAGSOLVER_EMBED_BACKEND=llama PYTHONUNBUFFERED=1
echo "=== EMBED FULL $(date '+%F %H:%M') ==="
uv run python -m ragsolver.rag.embed_corpus --in data/corpus/mathlib.jsonl --out data/corpus/mathlib_full_embedded.jsonl --backend llama 2>&1 | grep -v "Warning"
echo "=== TABLE mathlib_full $(date '+%F %H:%M') ==="
uv run python -m ragsolver.rag.index --in data/corpus/mathlib_full_embedded.jsonl --table mathlib_full 2>&1 | tail -2
echo "EMBED FULL DONE $(date '+%F %H:%M')"
