"""Fase 6 / Objetivo 7 — publicar la demo como Space de Gradio en HF Hub.

El Space sirve la pestaña **Buscar** sobre el índice publicado por `scripts/publish_hf.py`
(se descarga al arrancar vía `RAGSOLVER_HF_DATASET`); **Resolver** necesita Lean 4 + Mathlib
y una API key, así que en el Space muestra un aviso (sólo local). Sube `app.py`, el paquete
`ragsolver/`, `configs/`, `requirements.txt` y la tarjeta del Space, y con ellos el resto del repositorio que pide
la propuesta (docs, notebooks, tests, scripts, dependencias, LICENSE, CITATION), tras la misma comprobación de
contenido del PDF que `publish_hf.py` (SEC-2).

Desde 2026 los Spaces de Gradio en `cpu-basic` piden suscripción PRO (HTTP 402): con `--model` se sube lo mismo
como repositorio de modelo (gratis) y la demo se corre en local (`bash scripts/demo_up.sh`).

Uso: HF_TOKEN=... python scripts/publish_space.py --repo <usuario>/ragsolver --dataset <usuario>/ragsolver-index [--model] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_no_leaked_content import check as _check_leaks  # noqa: E402

GRADIO = "6.28.0"  # la versión con la que se probó demo/app.py

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

SPACE_YAML = """---
title: RagSolver
emoji: 🧮
colorFrom: indigo
colorTo: green
sdk: gradio
sdk_version: {gradio}
python_version: "3.12"
app_file: app.py
pinned: false
license: apache-2.0
---
"""
MODEL_YAML = """---
license: apache-2.0
tags: [lean4, mathlib, retrieval, rag, theorem-proving, olympiad, multi-agent]
datasets: [{dataset}]
---
"""
CARD = """# RagSolver — búsqueda de lemas de Mathlib (Lean 4, v4.28.0-rc1)

Recuperación híbrida (Qwen3-Embedding-0.6B + BM25, RRF) sobre {n_full} declaraciones de Mathlib
informalizadas por LeanSearch v2, con citas (nombre, módulo, enlace a la documentación).
Índice: [`{dataset}`](https://huggingface.co/datasets/{dataset}). El modo *Resolver* (agentes +
verificación con el kernel de Lean) requiere una instalación local: `docs/install.md`. {demo}

Este repositorio contiene el proyecto completo ([GitHub](https://github.com/camiloAndres11/RagSolver)):
`ragsolver/` (agentes, orquestador, recuperación, Lean), `configs/`, `docs/` (arquitectura, instalación, guía de
uso, interfaces, reproducción de cada ablación, informes de resultados e informe final), `notebooks/`, `tests/`,
`scripts/`, `pyproject.toml` + `uv.lock`, `LICENSE` (Apache-2.0) y `CITATION.cff`.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="<usuario>/ragsolver (Space)")
    ap.add_argument("--dataset", required=True, help="<usuario>/ragsolver-index (publicado con publish_hf.py)")
    ap.add_argument("--model", action="store_true", help="repositorio de modelo en vez de Space (sin PRO)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    n_full = "278 160"
    variables = {"RAGSOLVER_HF_DATASET": args.dataset, "RAGSOLVER_MATHLIB_TABLE": "mathlib_full", "GRADIO_SERVER_NAME": "0.0.0.0"}
    files = {"app.py": "demo/app.py", **{f: f for f in ("LICENSE", "CITATION.cff", "pyproject.toml", "uv.lock")}}
    folders = {"ragsolver": "src/ragsolver", "configs": "configs", **{d: d for d in ("docs", "notebooks", "tests", "scripts")}}
    upload = [Path(f) for f in files.values()] + [f for d in folders.values() for f in Path(d).rglob("*") if f.is_file() and "__pycache__" not in f.parts]
    leaks = _check_leaks(upload)
    if leaks:
        raise SystemExit("SEC-2: texto del corpus informal en lo que se iba a subir — publish abortado:\n" + "\n".join(leaks))
    print(json.dumps({"space": args.repo, "variables": variables, "files": files, "folders": folders}, indent=2, ensure_ascii=False))
    if args.dry_run:
        return
    from huggingface_hub import HfApi

    api = HfApi(token=os.environ.get("HF_TOKEN"))
    repo_type = "model" if args.model else "space"
    api.create_repo(args.repo, repo_type=repo_type, exist_ok=True, **({} if args.model else {"space_sdk": "gradio"}))
    demo = "La demo (`app.py`, Gradio) se corre en local: `bash scripts/demo_up.sh` (`docs/user_guide.md`)." if args.model else ""
    card = (MODEL_YAML if args.model else SPACE_YAML).format(dataset=args.dataset, gradio=GRADIO) + CARD.format(n_full=n_full, dataset=args.dataset, demo=demo)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "README.md").write_text(card, encoding="utf-8")
        (Path(tmp) / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")
        for name in ("README.md", "requirements.txt"):
            api.upload_file(path_or_fileobj=str(Path(tmp) / name), path_in_repo=name, repo_id=args.repo, repo_type=repo_type)
    for dst, src in files.items():
        api.upload_file(path_or_fileobj=src, path_in_repo=dst, repo_id=args.repo, repo_type=repo_type)
    for dst, src in folders.items():
        api.upload_folder(folder_path=src, path_in_repo=dst, repo_id=args.repo, repo_type=repo_type, ignore_patterns=["__pycache__/*", "*.pyc"])
    for k, v in ({} if args.model else variables).items():
        api.add_space_variable(args.repo, k, v)
    print("ok")


if __name__ == "__main__":
    main()
