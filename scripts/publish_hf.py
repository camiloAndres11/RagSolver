"""Fase 6, tarea 1 — publicar el índice y los corpus redistribuibles en HF Hub.

Sube: data/index/lancedb/{mathlib,figures}.lance (y `informal` sólo si TODOS sus
documentos son redistribuibles), data/corpus/mathlib*.jsonl, data/eval/{results,
problems, premises_gold.jsonl, informal_gold.jsonl}, configs/ y la dataset card.
NO sube: PDFs, recortes de figuras ni texto de documentos con `redistributable: false`
(docs/PLAN_RAG.md §12, licencias) — de ellos sólo van los metadatos.

SEC-2 (docs/AUDIT_2026-09-23.md, docs/PLAN_CORRECCIONES.md Fase 2): `results.jsonl`
de cada corrida lleva `Citation.snippet` con hasta 300 caracteres del corpus
informal en las citas `corpus=informal` — si algún documento no es redistribuible,
`eval/runs/` se sube desde una copia temporal saneada (snippet vaciado), nunca
desde el original. Antes de subir, la misma comprobación por contenido de
`scripts/check_no_leaked_content.py` corre sobre la copia; si encuentra algo,
el publish se aborta (ni siquiera en `--dry-run` se ignora el resultado).

Uso: HF_TOKEN=... python scripts/publish_hf.py --repo <usuario>/ragsolver-index [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_no_leaked_content import check as _check_leaks  # noqa: E402

CARD = """---
license: apache-2.0
tags: [lean4, mathlib, retrieval, rag, theorem-proving, olympiad]
---
# RagSolver — índice de recuperación (Mathlib v4.28.0-rc1)

Índice LanceDB (vector + BM25) sobre las declaraciones de Mathlib informalizadas por
[LeanSearch v2](https://huggingface.co/datasets/FrenzyMath/lsv2-mathlib-v4.28.0-rc1-jsonl)
(Apache 2.0), embebidas con Qwen3-Embedding-0.6B, más los conjuntos de evaluación del proyecto.
Construido con https://github.com/camiloAndres11/RagSolver (`docs/reproduce.md`); código, documentación y demo en [`{space}`](https://huggingface.co/{space}).

- `index/lancedb/mathlib.lance`: {n_mathlib} declaraciones ({scope}).
- `index/lancedb/mathlib_full.lance`: el corpus completo ({n_full} declaraciones) con índice ANN IVF_SQ
  (recall@10 0.989 respecto de la búsqueda exacta; `RAGSOLVER_MATHLIB_TABLE=mathlib_full` para usarlo).
- `index/lancedb/proofs.lance`: {n_proofs} pruebas Lean 4 verificadas de
  [Lean-workbook-proofs](https://huggingface.co/datasets/Goedel-LM/Lean-workbook-proofs) (MIT), embebidas por enunciado, sin los
  enunciados de miniF2F-v2 (config `rag_proofs`).
- `eval/runs/`: resultados por problema × configuración, resúmenes y trazas JSONL por agente de las
  corridas del informe ({runs}).
- `eval/prover_cache.jsonl`: las muestras de Goedel-Prover-V2-8B por (mensajes, semilla, parámetros), para repetir
  las corridas con el demostrador exactamente.
- `corpus/`: JSONL normalizado (`MathlibChunk`, ver `docs/interfaces.md`).
- `eval/`: MathlibQR/MPR, premisas gold de miniF2F (desde pruebas verificadas de DeepSeek-Prover-V2), gold informal, resultados.
- Mathlib: Apache 2.0. miniF2F-v2: Apache 2.0. El corpus informal de ejemplo (PDF de olimpiadas, Univ. de Sonora 2002) **no se redistribuye**: sólo metadatos;
  por eso faltan `pdf_set.jsonl` (sus 32 enunciados formalizados a mano) y la corrida `eval-v4-pdf`, y las citas a ese
  corpus van con el `snippet` vacío.
"""


# Corridas del informe; `eval-v4-pdf` no: sus problemas y trazas salen del PDF (SEC-2).
RUNS = ("eval-v1", "eval-v1-full", "f5-ablations", "eval-v2", "eval-v3", "eval-v4-unsolved", "eval-v4b-unsolved")
NOT_REDISTRIBUTABLE = ("pdf_set.jsonl",)


def _sanitize_run_dir(src: Path, dst: Path) -> None:
    """Copia `src` (una corrida en `data/eval/runs/<run_id>/`) a `dst` con el
    `snippet` de las citas `corpus=informal` vaciado en `results.jsonl` — el
    único vector de fuga conocido (verificado: las trazas no incluyen texto de
    fuente, sólo ids/scores/consultas). `src` nunca se modifica."""
    shutil.copytree(src, dst)
    for path in (dst / "results.jsonl", dst / "errors.jsonl"):
        if not path.exists():
            continue
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        for r in rows:
            for c in r.get("citations", []):
                if c.get("corpus") == "informal":
                    c["snippet"] = ""
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--space", default="", help="<usuario>/ragsolver, el repositorio del código (sólo para el enlace de la tarjeta)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    docs = [json.loads(p.read_text(encoding="utf-8")) for p in Path("data/parsed").glob("*/document.json")]
    informal_ok = docs and all(d.get("redistributable") for d in docs)
    n_mathlib = sum(1 for _ in open("data/corpus/mathlib_sample.jsonl", encoding="utf-8")) if Path("data/corpus/mathlib_sample.jsonl").exists() else 0
    scope = "muestra de evaluación" if n_mathlib < 100000 else "corpus completo"

    uploads = [("data/index/lancedb/mathlib.lance", "index/lancedb/mathlib.lance"), ("data/index/lancedb/mathlib_full.lance", "index/lancedb/mathlib_full.lance"), ("data/index/lancedb/proofs.lance", "index/lancedb/proofs.lance")]
    n_full = n_proofs = 0
    if Path("data/index/lancedb/mathlib_full.lance").exists():
        import lancedb

        n_full = lancedb.connect("data/index/lancedb").open_table("mathlib_full").count_rows()
        n_proofs = lancedb.connect("data/index/lancedb").open_table("proofs").count_rows()
    if informal_ok:  # texto y figuras del corpus informal sólo si su licencia lo permite
        uploads += [("data/index/lancedb/informal.lance", "index/lancedb/informal.lance"), ("data/index/lancedb/figures.lance", "index/lancedb/figures.lance")]
    files = [("data/corpus/mathlib_sample.jsonl", "corpus/mathlib_sample.jsonl"), ("data/eval/MathlibQR.json", "eval/MathlibQR.json"), ("data/eval/MathlibMPR.json", "eval/MathlibMPR.json"), ("data/eval/premises_gold.jsonl", "eval/premises_gold.jsonl"), ("data/eval/informal_gold.jsonl", "eval/informal_gold.jsonl"), ("data/eval/prover_cache.jsonl", "eval/prover_cache.jsonl")]
    dirs = [("data/eval/results", "eval/results"), ("data/eval/problems", "eval/problems"), ("configs", "configs"), ("docs/interfaces", "docs/interfaces")]

    tmp = Path(tempfile.mkdtemp(prefix="ragsolver-publish-")) if not informal_ok else None
    runs = [r for r in RUNS if (Path("data/eval/runs") / r).exists()]
    for r in runs:
        src = Path("data/eval/runs") / r
        if not src.exists():
            continue
        if informal_ok:  # el documento es redistribuible: no hace falta sanear
            dirs.append((str(src), f"eval/runs/{r}"))
        else:
            sanitized = tmp / r
            _sanitize_run_dir(src, sanitized)
            dirs.append((str(sanitized), f"eval/runs/{r}"))
    card = CARD.format(n_mathlib=n_mathlib, scope=scope, n_full=n_full, n_proofs=n_proofs, runs=", ".join(f"`{r}`" for r in runs), space=args.space)

    # SEC-2: gate de contenido antes de subir nada, `--dry-run` incluido — sobre
    # lo que de verdad se va a subir (copias saneadas cuando corresponda).
    upload_files = [Path(src) for src, _ in files if Path(src).exists()]
    upload_files += [f for src, _ in uploads + dirs if Path(src).exists() and Path(src).is_dir() for f in Path(src).rglob("*") if f.is_file() and f.name not in NOT_REDISTRIBUTABLE]
    leaks = _check_leaks(upload_files)
    if leaks:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        raise SystemExit(f"SEC-2: {len(leaks)} archivo(s) con texto del corpus informal en lo que se iba a subir — publish abortado:\n" + "\n".join(f"  {f}: {len(h)} coincidencia(s)" for f, h in leaks.items()))

    print(json.dumps({"repo": args.repo, "informal_redistributable": informal_ok, "folders": [d for _, d in uploads + dirs], "files": [f for _, f in files], "content_check": "OK"}, indent=2))
    if args.dry_run:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        return
    from huggingface_hub import HfApi

    api = HfApi(token=os.environ.get("HF_TOKEN"))
    api.create_repo(args.repo, repo_type="dataset", exist_ok=True)
    api.upload_file(path_or_fileobj=card.encode(), path_in_repo="README.md", repo_id=args.repo, repo_type="dataset")
    for src, dst in uploads + dirs:
        if Path(src).exists():
            api.upload_folder(folder_path=src, path_in_repo=dst, repo_id=args.repo, repo_type="dataset", ignore_patterns=[f"*{n}" for n in NOT_REDISTRIBUTABLE])
    for src, dst in files:
        if Path(src).exists():
            api.upload_file(path_or_fileobj=src, path_in_repo=dst, repo_id=args.repo, repo_type="dataset")
    if tmp is not None:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ok")


if __name__ == "__main__":
    main()
