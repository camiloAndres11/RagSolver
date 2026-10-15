"""Fase 6 / Objetivo 7 — publicar la demo como Space de Gradio en HF Hub.

El Space sirve la pestaña **Buscar** sobre el índice publicado por `scripts/publish_hf.py`
(se descarga al arrancar vía `RAGSOLVER_HF_DATASET`); **Resolver** necesita Lean 4 + Mathlib
y una API key, así que en el Space muestra un aviso (sólo local). Sube `app.py`, el paquete
`ragsolver/`, `configs/`, `requirements.txt` y la tarjeta del Space.

Uso: HF_TOKEN=... python scripts/publish_space.py --repo <usuario>/ragsolver --dataset <usuario>/ragsolver-index [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

REQUIREMENTS = """pydantic>=2.9
pyyaml>=6.0
lancedb>=0.17
pyarrow>=17.0
pandas>=2.2
sentence-transformers>=3.3
transformers>=4.51
torch>=2.4
accelerate>=1.0
huggingface_hub>=0.26
langgraph>=0.2
httpx
tenacity
"""

CARD = """---
title: RagSolver
emoji: 🧮
colorFrom: indigo
colorTo: green
sdk: gradio
app_file: app.py
pinned: false
license: apache-2.0
---
# RagSolver — búsqueda de lemas de Mathlib (Lean 4, v4.28.0-rc1)

Recuperación híbrida (Qwen3-Embedding-0.6B + BM25, RRF) sobre {n_full} declaraciones de Mathlib
informalizadas por LeanSearch v2, con citas (nombre, módulo, enlace a la documentación).
Índice: [`{dataset}`](https://huggingface.co/datasets/{dataset}). El modo *Resolver* (agentes +
verificación con el kernel de Lean) requiere una instalación local: ver el repositorio del proyecto.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="<usuario>/ragsolver (Space)")
    ap.add_argument("--dataset", required=True, help="<usuario>/ragsolver-index (publicado con publish_hf.py)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    n_full = "278 160"
    variables = {"RAGSOLVER_HF_DATASET": args.dataset, "RAGSOLVER_MATHLIB_TABLE": "mathlib_full", "GRADIO_SERVER_NAME": "0.0.0.0"}
    files = {"app.py": "demo/app.py"}
    folders = {"ragsolver": "src/ragsolver", "configs": "configs"}
    print(json.dumps({"space": args.repo, "variables": variables, "files": files, "folders": folders}, indent=2, ensure_ascii=False))
    if args.dry_run:
        return
    from huggingface_hub import HfApi

    api = HfApi(token=os.environ.get("HF_TOKEN"))
    api.create_repo(args.repo, repo_type="space", space_sdk="gradio", exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "README.md").write_text(CARD.format(n_full=n_full, dataset=args.dataset), encoding="utf-8")
        (Path(tmp) / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")
        for name in ("README.md", "requirements.txt"):
            api.upload_file(path_or_fileobj=str(Path(tmp) / name), path_in_repo=name, repo_id=args.repo, repo_type="space")
    for dst, src in files.items():
        api.upload_file(path_or_fileobj=src, path_in_repo=dst, repo_id=args.repo, repo_type="space")
    for dst, src in folders.items():
        api.upload_folder(folder_path=src, path_in_repo=dst, repo_id=args.repo, repo_type="space", ignore_patterns=["__pycache__/*", "*.pyc"])
    for k, v in variables.items():
        api.add_space_variable(args.repo, k, v)
    print("ok")


if __name__ == "__main__":
    main()
