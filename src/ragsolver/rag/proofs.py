"""RAG de pruebas (DECISIONS #127): pruebas Lean verificadas de los problemas más parecidos al pedido.

El RAG de lemas no le aporta al demostrador (los lemas genéricos ya los conoce, DECISIONS #109-#115); lo que
le falta es cómo se resolvió un problema parecido (TacticToe, Tactician, Rango: recuperar pruebas, no sólo
premisas). Corpus: Goedel-LM/Lean-workbook-proofs (29 750 pruebas verificadas, MIT; la misma descarga que el
prior de `scripts/build_premise_prior.py`), sin las que coinciden con un enunciado de miniF2F. Se indexa el
enunciado informal (el comentario `/- … -/`) + el enunciado Lean con Qwen3-Embedding, el modelo del resto del
RAG, en la tabla `proofs`; la prueba va sin comentarios.

Construcción: `python -m ragsolver.rag.proofs build` (con `RAGSOLVER_EMBED_BACKEND=llama`, minutos en la GPU).
"""

from __future__ import annotations

import argparse
import json
import re
import textwrap
from functools import lru_cache
from pathlib import Path

import yaml

TABLE = "proofs"
INSTRUCTION = "Given a competition mathematics problem, retrieve similar problems whose Lean 4 proofs use the same techniques"


def _model() -> str:
    return yaml.safe_load(open("configs/versions.yaml", encoding="utf-8"))["embedding"]["model"]


def norm_statement(s: str) -> str:
    """Enunciado sin nombre ni espacios redundantes (para excluir los de miniF2F, como el prior)."""
    s = re.sub(r"^\s*theorem\s+\S+", "", s.split(":= by", 1)[0])
    return re.sub(r"\s+", " ", s).strip()


def parse(full_proof: str) -> dict | None:
    """Archivo de Lean Workbook -> {informal, statement, proof}; None si no hay `theorem … := by`."""
    i = full_proof.find("theorem")
    m = re.search(r":=\s*by\b", full_proof[i:]) if i >= 0 else None
    if not m:
        return None
    body = re.sub(r"/-.*?-/", "", full_proof[i + m.end() :], flags=re.S)
    lines = [l for l in body.splitlines() if l.strip() and not l.strip().startswith("--")]
    proof = textwrap.dedent("\n".join(lines)).strip()
    if not proof:
        return None
    return {"informal": " ".join(re.findall(r"/-(.*?)-/", full_proof[:i], re.S)).strip(), "statement": full_proof[i : i + m.start()].strip(), "proof": proof}


def build(parquet: str, db_path: str, minif2f: list[str]) -> dict:
    import pyarrow.parquet as pq

    from ragsolver.rag.embed import embed_documents
    from ragsolver.rag.index import build_table

    excluded = {norm_statement(json.loads(l)["formal_statement"]) for path in minif2f for l in open(path, encoding="utf-8")}
    rows, n_excl = [], 0
    for r in pq.read_table(parquet).to_pylist():
        p = parse(r["full_proof"])
        if p is None:
            continue
        if norm_statement(p["statement"]) in excluded:
            n_excl += 1
            continue
        rows.append({"problem_id": r["problem_id"], **p})
    vecs = embed_documents([f"{r['informal']}\n{r['statement']}"[:1500] for r in rows], _model())
    build_table(db_path, TABLE, ({**r, "embedding": v} for r, v in zip(rows, vecs)), fts_columns=None)
    return {"n_rows": len(rows), "n_excluded_minif2f": n_excl}


@lru_cache(maxsize=1)
def _table(db_path: str):
    from ragsolver.rag.index import open_table

    return open_table(db_path, TABLE)


def similar(text: str, k: int = 3, max_sim: float = 0.95, db_path: str = "data/index/lancedb") -> list[dict]:
    """Las k pruebas más parecidas a `text` (informal + enunciado Lean) con similitud coseno < `max_sim`:
    por encima es el mismo problema (o casi) y la prueba sería la respuesta, no un ejemplo."""
    from ragsolver.rag.embed import embed_queries
    from ragsolver.rag.index import vector_search

    q = embed_queries([text[:1500]], _model(), instruction=INSTRUCTION)[0]
    out = []
    for row in vector_search(_table(db_path), q, 4 * k):
        sim = sum(a * b for a, b in zip(q, row["embedding"]))  # vectores normalizados
        if sim < max_sim:
            out.append({"problem_id": row["problem_id"], "statement": row["statement"], "proof": row["proof"], "sim": round(sim, 4)})
        if len(out) == k:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "preview"])
    ap.add_argument("--parquet", default="data/raw/lean_workbook_proofs/data/train-00000-of-00001.parquet")
    ap.add_argument("--minif2f", nargs="+", default=["data/eval/minif2f_v2/miniF2F_v2s.jsonl", "data/eval/minif2f_v2/miniF2F_v2c.jsonl"])
    ap.add_argument("--db-path", default="data/index/lancedb")
    ap.add_argument("--problems", nargs="*", default=[], help="preview: JSONL de problemas")
    ap.add_argument("--max-sim", type=float, default=1.01)
    args = ap.parse_args()
    if args.cmd == "build":
        print(json.dumps(build(args.parquet, args.db_path, args.minif2f)), flush=True)
        return
    for path in args.problems:  # preview: qué recuperaría cada problema (para revisar contaminación a mano)
        for l in Path(path).read_text(encoding="utf-8").splitlines():
            p = json.loads(l)
            print(f"\n## {p['problem_id']}\n{p['formal_statement'][:300]}")
            for h in similar(f"{p['informal_statement']}\n{p['formal_statement']}", 3, args.max_sim, args.db_path):
                print(f"  [{h['sim']}] {h['problem_id']}: {h['statement'][:250]}")


if __name__ == "__main__":
    main()
