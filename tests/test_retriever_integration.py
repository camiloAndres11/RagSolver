"""Fase 3 — integración sobre el índice real (data/index/lancedb): carga el
embedder (CPU, lento en /mnt/c). Verifica que el Retriever no duplica la lógica
de búsqueda (su orden denso = `vector_search` crudo) y el criterio de aceptación
del modo `error` (identificador exacto en el top-1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ragsolver.rag.embed import embed_queries
from ragsolver.rag.index import open_table, vector_search
from ragsolver.rag.retrieve import Retriever
from ragsolver.rag.schema import RetrievalRequest

DB = Path(__file__).resolve().parent.parent / "data/index/lancedb"
pytestmark = pytest.mark.skipif(not (DB / "mathlib.lance").exists(), reason="requiere el índice LanceDB de Fase 1")


@pytest.fixture(scope="module")
def retriever(tmp_path_factory):
    r = Retriever(db_path=str(DB))
    r.trace_dir = str(tmp_path_factory.mktemp("traces"))
    return r


def test_dense_channel_reproduces_raw_vector_search_order(retriever):
    q = "the greatest common divisor is commutative"
    retriever.channels = ("dense",)
    hits = retriever.search(RetrievalRequest(queries=[q], corpora=["mathlib"], k=10, rerank=False, context={"problem_id": "it"})).hits
    retriever.channels = ("dense", "sparse")
    raw = vector_search(open_table(str(DB), "mathlib"), embed_queries([q], retriever.versions["embedding"]["model"], instruction=retriever.config["query_instructions"]["nl"])[0], 10)
    assert [h.chunk_id for h in hits] == [r["chunk_id"] for r in raw]


def test_error_mode_returns_the_named_declaration_first(retriever):
    table = open_table(str(DB), "mathlib")
    decl = table.search().where("kind = 'theorem'").select(["decl_name"]).limit(1).to_list()[0]["decl_name"]
    res = retriever.search(RetrievalRequest(queries=[f"unknown identifier '{decl}'"], mode="error", corpora=["mathlib"], k=5, rerank=True, context={"problem_id": "it"}))
    assert res.hits[0].citation.locator["decl_name"] == decl
    assert res.hits[0].citation.label.startswith(decl)
