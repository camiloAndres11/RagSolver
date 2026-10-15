"""Integración real contra LanceDB (tabla sintética, sin modelo de embeddings).

Verifica que build_table/vector_search/fts_search funcionan con la versión de
lancedb instalada, antes de confiar en index.py para el índice real de Mathlib.
"""

from __future__ import annotations

from ragsolver.rag.index import build_table, fts_search, vector_search

ROWS = [
    {
        "chunk_id": "mathlib:Nat.Coprime:0",
        "corpus": "mathlib",
        "unit_type": "mathlib_decl",
        "decl_name": "Nat.Coprime",
        "informal_name": "Coprimality",
        "informal_description": "two naturals are coprime if gcd is one",
        "signature": "Nat -> Nat -> Prop",
        "text": "Coprimality: two naturals are coprime if gcd is one",
        "embedding": [1.0, 0.0, 0.0],
    },
    {
        "chunk_id": "mathlib:Nat.Prime:0",
        "corpus": "mathlib",
        "unit_type": "mathlib_decl",
        "decl_name": "Nat.Prime",
        "informal_name": "Primality",
        "informal_description": "a natural number with exactly two divisors",
        "signature": "Nat -> Prop",
        "text": "Primality: a natural number with exactly two divisors",
        "embedding": [0.0, 1.0, 0.0],
    },
    {
        "chunk_id": "geom:triangle_area:0",
        "corpus": "mathlib",
        "unit_type": "mathlib_decl",
        "decl_name": "EuclideanGeometry.area",
        "informal_name": "Triangle area",
        "informal_description": "area of a triangle is base times height over two",
        "signature": "Triangle -> Real",
        "text": "Triangle area: base times height over two",
        "embedding": [0.0, 0.0, 1.0],
    },
]


def test_vector_search_returns_nearest_by_cosine(tmp_path):
    table = build_table(str(tmp_path / "db"), "mathlib", ROWS, fts_columns=["decl_name", "informal_name", "informal_description", "signature"])
    hits = vector_search(table, [1.0, 0.0, 0.0], k=1)
    assert hits[0]["chunk_id"] == "mathlib:Nat.Coprime:0"


def test_fts_search_finds_exact_identifier(tmp_path):
    table = build_table(str(tmp_path / "db"), "mathlib", ROWS, fts_columns=["decl_name", "informal_name", "informal_description", "signature"])
    hits = fts_search(table, "Primality divisors", k=3)
    assert hits
    assert hits[0]["chunk_id"] == "mathlib:Nat.Prime:0"


def test_build_table_replaces_existing(tmp_path):
    db_path = str(tmp_path / "db")
    build_table(db_path, "mathlib", ROWS, fts_columns=["decl_name"])
    table = build_table(db_path, "mathlib", ROWS[:1], fts_columns=["decl_name"])
    assert table.count_rows() == 1


def test_build_table_streams_in_chunks(tmp_path):
    """Con `chunk` menor que el número de filas se escribe por bloques (`table.add` devuelve
    AddResult en lancedb >= 0.25; antes se encadenaba con `or` y el segundo bloque fallaba)."""
    table = build_table(str(tmp_path / "db"), "mathlib", ROWS, fts_columns=["decl_name"], chunk=1)
    assert table.count_rows() == len(ROWS)
