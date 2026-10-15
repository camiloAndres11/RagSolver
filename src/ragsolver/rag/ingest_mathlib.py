"""Fase 1 — Corpus A: normaliza el JSONL de LeanSearch v2 al esquema MathlibChunk.

docs/PLAN_RAG.md §6.1. Entrada: `lsv2-mathlib-v4.28.0-rc1.jsonl` (310 579 registros,
FrenzyMath/lsv2-mathlib-v4.28.0-rc1-jsonl, Apache-2.0). Salida: `data/corpus/mathlib.jsonl`
con un `MathlibChunk` por línea (sin `embedding` todavía — eso lo hace `embed.py`).

Uso: python -m ragsolver.rag.ingest_mathlib [--config configs/ingest.yaml] [--limit N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml

from ragsolver.rag.schema import MathlibChunk


def _load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["mathlib"]


def _should_exclude(record: dict, cfg: dict) -> bool:
    if record.get("kind") in cfg["exclude_kinds"]:
        return True
    module_path = ".".join(record.get("module_name") or [])
    if any(module_path.startswith(p) for p in cfg["exclude_module_prefixes"]):
        return True
    decl_name = ".".join(record.get("name") or [])
    if any(sub in decl_name for sub in cfg["exclude_name_substrings"]):
        return True
    # lemas de ecuación autogenerados (`foo.eq_1`, `foo.eq_def`), sin borrar lemas reales como
    # `Nat.Prime.eq_one_or_self_of_dvd` (docs/PLAN_MEJORA_RAG.md §2.4, Fase 3 tarea 4b)
    if cfg.get("exclude_name_regex") and re.search(cfg["exclude_name_regex"], decl_name):
        return True
    return False


def _docs_url(module_path: str, decl_name: str, base: str) -> str:
    return f"{base}/{module_path.replace('.', '/')}.html#{decl_name}"


def record_to_chunk(record: dict, cfg: dict, mathlib_tag: str, lean_version: str) -> MathlibChunk | None:
    names = record.get("name") or []
    if not names:
        return None
    # `name` es la lista de COMPONENTES del nombre (["GCDMonoid", "dvd_gcd"]), no
    # alias: el nombre completo es su unión con "." (docs/DECISIONS.md #31).
    decl_name = ".".join(names)
    module_path = ".".join(record.get("module_name") or [])
    informal_name = record.get("informal_name", "") or ""
    informal_description = record.get("informal_description", "") or ""
    signature = record.get("signature", "") or record.get("type", "") or ""

    text = "\n".join(part for part in [informal_name, informal_description, "", signature] if part is not None).strip()
    content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    return MathlibChunk(
        chunk_id=f"mathlib:{decl_name}:0",
        doc_id=cfg["doc_id"],
        text=text,
        content_sha=content_sha,
        decl_name=decl_name,
        decl_names_alt=[],
        module_path=module_path,
        kind=record.get("kind", ""),
        signature=signature,
        type=record.get("type", "") or "",
        value=record.get("value", "") or "",
        informal_name=informal_name,
        informal_description=informal_description,
        mathlib_tag=mathlib_tag,
        lean_version=lean_version,
        docs_url=_docs_url(module_path, decl_name, cfg["docs_url_base"]),
    )


def run(config_path: str = "configs/ingest.yaml", versions_path: str = "configs/versions.yaml", limit: int | None = None) -> dict:
    cfg = _load_config(config_path)
    with open(versions_path, encoding="utf-8") as f:
        versions = yaml.safe_load(f)
    mathlib_tag = versions["lean"]["mathlib_tag"]
    lean_version = versions["lean"]["toolchain"]

    raw_path = Path(cfg["raw_jsonl"])
    out_path = Path(cfg["out_jsonl"])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_in = n_out = n_excluded = 0
    with raw_path.open(encoding="utf-8") as fin, out_path.open("w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip():
                continue
            n_in += 1
            if limit is not None and n_in > limit:
                break
            record = json.loads(line)
            if _should_exclude(record, cfg):
                n_excluded += 1
                continue
            chunk = record_to_chunk(record, cfg, mathlib_tag, lean_version)
            if chunk is None:
                n_excluded += 1
                continue
            fout.write(chunk.model_dump_json(exclude={"embedding"}) + "\n")
            n_out += 1

    return {"n_in": n_in, "n_out": n_out, "n_excluded": n_excluded, "out_path": str(out_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/ingest.yaml")
    parser.add_argument("--versions", default="configs/versions.yaml")
    parser.add_argument("--limit", type=int, default=None, help="sólo las primeras N líneas del raw (debug)")
    args = parser.parse_args()
    stats = run(args.config, args.versions, args.limit)
    print(json.dumps(stats, indent=2))
