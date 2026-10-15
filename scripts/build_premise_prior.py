"""Fase 1 de docs/PLAN_MEJORA_RAG.md — canal A: prior de competición.

Frecuencia de cada lema en las pruebas de Goedel-LM/Lean-workbook-proofs (29 750 pruebas generadas
por Goedel-Prover sobre Lean Workbook; licencia MIT), validado contra el entorno de Lean del
verificador (`#check`, no el JSONL de LeanSearch: así entran los lemas de núcleo como `Nat.mul_mod`,
que el corpus no tiene — sección 2.4 del plan). Se excluyen las pruebas cuyo enunciado coincide con
uno de miniF2F-v2 (contaminación).

Salida: data/corpus/premise_prior.jsonl, una fila {decl_name, freq, type} por lema, de mayor a menor.
Uso: python scripts/build_premise_prior.py   (carga el REPL de Lean: ~5 min + ~0.1 s por nombre)
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

HEADER = "open BigOperators Real Nat Topology Rat"  # el de las pruebas de Lean Workbook y de miniF2F


def _norm_statement(s: str) -> str:
    s = re.sub(r"^\s*theorem\s+\S+", "", s.split(":= by", 1)[0])
    return re.sub(r"\s+", " ", s).strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="Goedel-LM/Lean-workbook-proofs")
    ap.add_argument("--revision", default=None, help="sha del dataset en HF (se registra en la salida)")
    ap.add_argument("--minif2f", default="data/eval/minif2f_v2/miniF2F_v2s.jsonl")
    ap.add_argument("--min-freq", type=int, default=2)
    ap.add_argument("--out", default="data/corpus/premise_prior.jsonl")
    args = ap.parse_args()

    import pyarrow.parquet as pq
    from huggingface_hub import HfApi, hf_hub_download

    from ragsolver.lean.verify import LeanChecker
    from ragsolver.rag.premises import is_lemma_type, proof_tokens

    revision = args.revision or HfApi().dataset_info(args.repo).sha
    path = hf_hub_download(args.repo, "data/train-00000-of-00001.parquet", repo_type="dataset", revision=revision, local_dir="data/raw/lean_workbook_proofs")
    minif2f = {_norm_statement(json.loads(l)["formal_statement"]) for l in open(args.minif2f, encoding="utf-8")}

    counts: collections.Counter = collections.Counter()
    n_proofs = n_contaminated = 0
    for row in pq.read_table(path).to_pylist():
        proof = row["full_proof"]
        stmt = proof[proof.find("theorem"):] if "theorem" in proof else proof
        if _norm_statement(stmt) in minif2f:
            n_contaminated += 1
            continue
        n_proofs += 1
        counts.update(proof_tokens(proof))
    cands = [t for t, c in counts.most_common() if c >= args.min_freq]
    print(f"{n_proofs} pruebas ({n_contaminated} excluidas por coincidir con miniF2F), {len(cands)} candidatos con freq >= {args.min_freq}", flush=True)

    checker = LeanChecker(imports=["Mathlib", "Aesop"], timeout_s=1800)
    sigs = checker.signatures(cands, header=HEADER)
    checker.close()
    rows = [{"decl_name": t, "freq": counts[t], "type": sigs[t]} for t in cands if t in sigs and is_lemma_type(sigs[t])]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    meta = {"source": args.repo, "revision": revision, "n_proofs": n_proofs, "n_contaminated_excluded": n_contaminated, "n_candidates": len(cands), "n_resolved": len(sigs), "n_lemmas": len(rows), "out": args.out}
    Path(args.out).with_suffix(".meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2), flush=True)


if __name__ == "__main__":
    main()
