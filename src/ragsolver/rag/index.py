"""Índice LanceDB: construcción de tabla + búsqueda densa/FTS + fusión RRF.

docs/PLAN_RAG.md §6.3 y §9.2. Una tabla por corpus (`mathlib`, `informal`).
No hay clase "Store" genérica: son funciones sobre `lancedb.Table`, que ya es
la abstracción — ponytail rung 2 (reusar lo que ya existe).
"""

from __future__ import annotations

import json
from pathlib import Path

import lancedb
from lancedb.index import FTS

MATHLIB_FTS_COLUMNS = ["decl_name", "informal_name", "informal_description", "signature"]
INFORMAL_FTS_COLUMNS = ["text", "keywords", "theorems_used"]

_FTS_COLUMN = "_fts_text"  # columna derivada: lancedb >=0.25 sólo indexa FTS de a un campo


def load_jsonl(path: str | Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _write(db, table, table_name: str, batch: list[dict]):
    if table is None:
        return db.create_table(table_name, data=batch)
    table.add(batch)  # devuelve un AddResult (truthy) en lancedb >= 0.25: no encadenar con `or`
    return table


def build_table(db_path: str, table_name: str, rows, fts_columns: list[str] | None = None, chunk: int = 5000) -> lancedb.table.Table:
    """`fts_columns=None` crea una tabla de metadatos sin índice FTS (p.ej. `figures`).
    `rows` puede ser un iterador (JSONL grande): se escribe por bloques de `chunk` filas."""
    db = lancedb.connect(db_path)
    if table_name in db.list_tables().tables:
        db.drop_table(table_name)

    def prep(row):
        if fts_columns:
            # lancedb >=0.25 sólo indexa FTS de un campo por índice; concatenamos las
            # columnas buscables en una columna derivada en vez de mantener N índices.
            return {**row, _FTS_COLUMN: " ".join(str(row.get(c, "")) for c in fts_columns)}
        return row

    table = None
    batch: list[dict] = []
    for row in rows:
        batch.append(prep(row))
        if len(batch) >= chunk:
            table = _write(db, table, table_name, batch)
            batch = []
    if batch or table is None:
        table = _write(db, table, table_name, batch)
    if fts_columns:
        table.create_index(_FTS_COLUMN, config=FTS())
    return table


def iter_jsonl(path: str | Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def open_table(db_path: str, table_name: str) -> lancedb.table.Table:
    db = lancedb.connect(db_path)
    return db.open_table(table_name)


# Índice ANN (sólo tablas grandes, p.ej. `mathlib_full`): IVF con cuantización escalar de 8 bits y
# re-ranking exacto de los `refine_factor × k` mejores → casi exacto con ~1 % de las lecturas. En la
# búsqueda plana (sin índice) estos parámetros no tienen efecto. Ver DECISIONS #60.
ANN_NPROBES = 32
ANN_REFINE = 5


def build_ann_index(table: lancedb.table.Table, num_partitions: int = 256) -> None:
    table.create_index(metric="l2", vector_column_name="embedding", index_type="IVF_SQ", num_partitions=num_partitions, replace=True)


def vector_search(table: lancedb.table.Table, query_vector: list[float], k: int, where: str | None = None) -> list[dict]:
    q = table.search(query_vector, vector_column_name="embedding").nprobes(ANN_NPROBES).refine_factor(ANN_REFINE)
    if where:
        q = q.where(where, prefilter=True)
    return q.limit(k).to_list()


def fts_search(table: lancedb.table.Table, query_text: str, k: int, where: str | None = None) -> list[dict]:
    q = table.search(query_text, query_type="fts", fts_columns=_FTS_COLUMN)
    if where:
        q = q.where(where, prefilter=True)
    return q.limit(k).to_list()


def sql_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def exact_match(table: lancedb.table.Table, column: str, values: list[str], where: str | None = None) -> list[dict]:
    """Filas cuyo `column` es exactamente uno de `values` (modo `error`: identificador Lean literal)."""
    if not values:
        return []
    expr = f"{column} IN ({', '.join(sql_quote(v) for v in values)})"
    if where:
        expr = f"({expr}) AND ({where})"
    return table.search().where(expr).limit(len(values)).to_list()


def rrf_fuse(ranked_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion estándar (docs/PLAN_RAG.md §6.3).

    `ranked_lists`: una lista de listas de ids, ya ordenadas por relevancia
    (rank 0 = mejor). Devuelve ids fusionados ordenados por score RRF desc.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Construye (o reemplaza) una tabla LanceDB desde un JSONL embebido.")
    ap.add_argument("--in", dest="in_path", default=None, help="p.ej. data/corpus/informal_embedded.jsonl (no hace falta con --ann-only)")
    ap.add_argument("--table", required=True, choices=["mathlib", "mathlib_full", "informal", "figures"])
    ap.add_argument("--db-path", default="data/index/lancedb")
    ap.add_argument("--ann-only", action="store_true", help="no reconstruye: sólo crea el índice ANN (IVF_SQ) sobre la tabla existente")
    args = ap.parse_args()
    if args.ann_only:
        table = lancedb.connect(args.db_path).open_table(args.table)
        build_ann_index(table)
        print(json.dumps({"table": args.table, "rows": table.count_rows(), "indices": [str(i.index_type) for i in table.list_indices()]}))
        raise SystemExit
    if not args.in_path:
        ap.error("--in es obligatorio salvo con --ann-only")
    fts = {"mathlib": MATHLIB_FTS_COLUMNS, "mathlib_full": MATHLIB_FTS_COLUMNS, "informal": INFORMAL_FTS_COLUMNS, "figures": None}[args.table]
    table = build_table(args.db_path, args.table, iter_jsonl(args.in_path), fts_columns=fts)
    print(json.dumps({"table": args.table, "rows": table.count_rows(), "db_path": args.db_path}))
