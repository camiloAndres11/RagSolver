"""Fase 3, tarea 6 — Retriever con LanceDB temporal y embedder/reranker falsos.

Vectores de dimensión 4 elegidos a mano para controlar el orden denso; el
reranker falso puntúa por longitud del texto. Así cada test aísla una pieza:
filtros SQL, modo `error`, dedupe, figuras adjuntas, ablación, traza, plantillas.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ragsolver.logging.jsonl_writer import read_events
from ragsolver.rag import retrieve as R
from ragsolver.rag.index import INFORMAL_FTS_COLUMNS, MATHLIB_FTS_COLUMNS, build_table
from ragsolver.rag.retrieve import Retriever, lean_identifiers, sql_filter
from ragsolver.rag.schema import RetrievalFilters, RetrievalRequest

E1, E2, E3, E4 = [1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]

MATHLIB = [
    {"chunk_id": "mathlib:Nat.Prime.two_le:0", "doc_id": "mathlib", "corpus": "mathlib", "unit_type": "mathlib_decl", "decl_name": "Nat.Prime.two_le", "module_path": "Mathlib.Data.Nat.Prime.Defs", "kind": "theorem", "signature": "theorem Nat.Prime.two_le {p : ℕ} (hp : p.Prime) : 2 ≤ p", "informal_name": "prime is at least two", "informal_description": "A prime number is at least 2.", "docs_url": "https://x#Nat.Prime.two_le", "text": "prime is at least two", "embedding": E4},
    {"chunk_id": "mathlib:Nat.Coprime:0", "doc_id": "mathlib", "corpus": "mathlib", "unit_type": "mathlib_decl", "decl_name": "Nat.Coprime", "module_path": "Mathlib.Data.Nat.GCD.Basic", "kind": "definition", "signature": "def Nat.Coprime (m n : ℕ) : Prop", "informal_name": "coprime naturals", "informal_description": "gcd m n = 1", "docs_url": "https://x#Nat.Coprime", "text": "coprime naturals gcd equals one", "embedding": E1},
    {"chunk_id": "mathlib:Nat.gcd_comm:0", "doc_id": "mathlib", "corpus": "mathlib", "unit_type": "mathlib_decl", "decl_name": "Nat.gcd_comm", "module_path": "Mathlib.Data.Nat.GCD.Basic", "kind": "theorem", "signature": "theorem Nat.gcd_comm (m n : ℕ) : gcd m n = gcd n m", "informal_name": "gcd is commutative", "informal_description": "gcd commutes", "docs_url": "https://x#Nat.gcd_comm", "text": "gcd is commutative", "embedding": E2},
    {"chunk_id": "mathlib:Int.gcd_comm:0", "doc_id": "mathlib", "corpus": "mathlib", "unit_type": "mathlib_decl", "decl_name": "Int.gcd_comm", "module_path": "Mathlib.Data.Int.GCD", "kind": "theorem", "signature": "theorem Int.gcd_comm (m n : ℤ) : gcd m n = gcd n m", "informal_name": "integer gcd is commutative", "informal_description": "gcd commutes for integers", "docs_url": "https://x#Int.gcd_comm", "text": "integer gcd is commutative", "embedding": E3},
]
INFORMAL = [
    {"chunk_id": "informal:d1:problem_card:8", "doc_id": "d1", "corpus": "informal", "unit_type": "problem_card", "parent_id": None, "item_number": 8, "page_start": 10, "math_area": ["geometry"], "keywords": ["circle"], "theorems_used": [], "figure_ids": ["d1:fig:10:2"], "content_md": "8. tangent circles", "text": "problem about tangent circles and an inscribed angle", "embedding": E1},
    {"chunk_id": "informal:d1:figure:10:2", "doc_id": "d1", "corpus": "informal", "unit_type": "figure", "parent_id": "informal:d1:problem_card:8", "item_number": 8, "page_start": 10, "math_area": ["geometry"], "keywords": ["circle"], "theorems_used": [], "figure_ids": ["d1:fig:10:2"], "content_md": "a circle tangent to a bigger circle", "text": "figure: a circle tangent to a bigger circle", "embedding": [0.9, 0.1, 0, 0]},
    {"chunk_id": "informal:d1:problem_card:86", "doc_id": "d1", "corpus": "informal", "unit_type": "problem_card", "parent_id": None, "item_number": 86, "page_start": 22, "math_area": ["number_theory"], "keywords": ["divisibility"], "theorems_used": [], "figure_ids": [], "content_md": "86. divisible by 7", "text": "prove that a power sum is divisible by seven", "embedding": E2},
    {"chunk_id": "informal:d2:problem_card:1", "doc_id": "d2", "corpus": "informal", "unit_type": "problem_card", "parent_id": None, "item_number": 1, "page_start": 1, "math_area": ["algebra"], "keywords": [], "theorems_used": [], "figure_ids": [], "content_md": "1. other doc", "text": "an algebra problem from another document", "embedding": E3},
]
FIGURES = [{"figure_id": "d1:fig:10:2", "image_path": "/tmp/p010_b2.png", "description": "two tangent circles", "page": 10, "bbox": [0.1, 0.1, 0.5, 0.5], "figure_type": "geometric_diagram", "labels": ["A", "B"], "detected_by": "layout"}]


@pytest.fixture
def retriever(tmp_path, monkeypatch):
    db = str(tmp_path / "db")
    build_table(db, "mathlib", MATHLIB, MATHLIB_FTS_COLUMNS)
    build_table(db, "informal", INFORMAL, INFORMAL_FTS_COLUMNS)
    build_table(db, "figures", FIGURES)
    cfg = yaml.safe_load(open("configs/retrieval.yaml", encoding="utf-8"))
    (tmp_path / "retrieval.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    calls = {"instructions": [], "queries": []}

    def fake_embed(texts, model_name, batch_size=8, instruction=None):
        calls["instructions"].append(instruction)
        calls["queries"].append(list(texts))
        table = {"gcd": E2, "coprime": E1, "prime": E4, "integer": E3, "circle": E1, "seven": E2, "algebra": E3}
        return [next((v for k, v in table.items() if k in t.lower()), [0.5, 0.5, 0.5, 0.5]) for t in texts]

    monkeypatch.setattr(R, "embed_queries", fake_embed)

    class FakeReranker:  # más largo = mejor, para poder predecir el orden
        def predict(self, pairs):
            return [float(len(doc)) for _, doc in pairs]

    r = Retriever(db_path=db, config_path=str(tmp_path / "retrieval.yaml"))
    monkeypatch.setattr(Retriever, "_reranker", lambda self: FakeReranker())
    r.trace_dir = str(tmp_path / "traces")
    r._calls = calls
    return r


def _req(**kw):
    base = dict(queries=["gcd commutes"], corpora=["mathlib"], k=8, rerank=False, context={"problem_id": "p1", "subgoal_id": "s1", "attempt": 2, "agent": "test"})
    base.update(kw)
    return RetrievalRequest(**base)


def test_lean_identifiers_from_error_messages():
    assert lean_identifiers("unknown identifier 'Nat.Prime.two_le'") == ["Nat.Prime.two_le"]
    assert lean_identifiers("unknown constant 'Foo.bar' and `Baz.qux'` here, also Nat.gcd_comm") == ["Foo.bar", "Baz.qux'", "Nat.gcd_comm"]
    assert lean_identifiers("no identifiers here") == []


def test_sql_filter_quotes_and_scopes_by_corpus():
    f = RetrievalFilters(doc_ids=["d'1"], kinds=["theorem"], math_area=["geometry"], unit_types=["problem_card"])
    assert sql_filter("mathlib", f) == "doc_id IN ('d''1') AND unit_type IN ('problem_card') AND kind IN ('theorem')"
    assert sql_filter("informal", f) == "doc_id IN ('d''1') AND unit_type IN ('problem_card') AND array_has_any(math_area, ['geometry'])"
    assert sql_filter("mathlib", RetrievalFilters()) is None


def test_error_mode_pins_exact_identifier_at_top_even_against_dense_and_rerank(retriever):
    # el embedding falso de la consulta apunta a los gcd (E2) y el reranker prefiere textos largos:
    # ninguno favorece a Nat.Prime.two_le; el modo error lo fija igual en el top-1.
    res = retriever.search(_req(queries=["unknown identifier 'Nat.Prime.two_le' in goal gcd commutes"], mode="error", rerank=True))
    assert res.hits[0].chunk_id == "mathlib:Nat.Prime.two_le:0"
    assert res.hits[0].citation.citation_id == "M1"
    assert res.hits[0].citation.locator["docs_url"].endswith("#Nat.Prime.two_le")
    assert len(res.hits) == 4


def test_filters_prefilter_kinds_doc_ids_and_math_area(retriever):
    res = retriever.search(_req(filters={"kinds": ["definition"]}))
    assert [h.chunk_id for h in res.hits] == ["mathlib:Nat.Coprime:0"]

    res = retriever.search(_req(queries=["algebra"], corpora=["informal"], filters={"doc_ids": ["d1"]}))
    assert all(h.metadata["doc_id"] == "d1" for h in res.hits) and res.hits

    res = retriever.search(_req(queries=["circle"], corpora=["informal"], filters={"math_area": ["number_theory"]}))
    assert [h.chunk_id for h in res.hits] == ["informal:d1:problem_card:86"]


def test_figure_chunk_collapses_into_its_card_and_figures_are_attached(retriever):
    res = retriever.search(_req(queries=["circle"], corpora=["informal"]))
    ids = [h.chunk_id for h in res.hits]
    assert "informal:d1:problem_card:8" in ids and "informal:d1:figure:10:2" not in ids
    card = next(h for h in res.hits if h.chunk_id == "informal:d1:problem_card:8")
    assert card.figures == [{"figure_id": "d1:fig:10:2", "image_path": "/tmp/p010_b2.png", "description": "two tangent circles", "page": 10, "bbox": [0.1, 0.1, 0.5, 0.5], "figure_type": "geometric_diagram", "labels": ["A", "B"]}]
    assert card.citation.locator["figure_ids"] == ["d1:fig:10:2"]
    assert card.citation.citation_id == "I1" and card.citation.snippet == "8. tangent circles"

    res = retriever.search(_req(queries=["circle"], corpora=["informal"], include_figures=False))
    assert all(h.figures == [] for h in res.hits)


def test_rerank_reorders_and_scores_are_reported(retriever):
    res = retriever.search(_req(rerank=True))
    texts = [h.text for h in res.hits]
    assert texts == sorted(texts, key=len, reverse=True)  # reranker falso: más largo primero
    assert all(h.citation.scores.rerank is not None and h.citation.scores.fused is not None for h in res.hits)


def test_multi_query_rrf_and_query_instruction_per_mode(retriever):
    res = retriever.search(_req(queries=["gcd commutes", "coprime"], mode="formal", k=2))
    assert {h.chunk_id for h in res.hits} <= {"mathlib:Nat.gcd_comm:0", "mathlib:Nat.Coprime:0", "mathlib:Int.gcd_comm:0"}
    assert retriever._calls["instructions"][-1] == retriever.config["query_instructions"]["formal"]
    assert retriever._calls["queries"][-1] == ["gcd commutes", "coprime"]


def test_max_per_module_limits_mathlib_hits(retriever):
    retriever.config["max_per_module"] = 1
    res = retriever.search(_req(queries=["gcd commutes coprime"], k=8))
    modules = [h.metadata["module_path"] for h in res.hits]
    assert len(modules) == len(set(modules))


def test_ablation_disabled_returns_empty_but_still_traces(retriever):
    retriever.config["ablation"]["enabled"] = False
    res = retriever.search(_req())
    assert res.hits == [] and res.citations == []
    events = [e for f in Path(retriever.trace_dir).glob("*.jsonl") for e in read_events(f.stem, retriever.trace_dir)]
    assert len(events) == 1 and events[0].hits == [] and events[0].llm_calls == 0


def test_trace_event_records_request_hits_and_versions(retriever):
    res = retriever.search(_req(k=2))
    files = list(Path(retriever.trace_dir).glob("p1:*.jsonl"))
    assert len(files) == 1
    ev = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert ev["agent"] == "retriever" and ev["llm_calls"] == 0 and ev["subgoal_id"] == "s1" and ev["attempt"] == 2
    assert [h["chunk_id"] for h in ev["hits"]] == [h.chunk_id for h in res.hits]
    assert ev["versions"]["mathlib_tag"] == retriever.versions["lean"]["mathlib_tag"]
    assert ev["request"]["queries"] == ["gcd commutes"]


def test_trace_event_pins_the_mathlib_table_being_served(retriever):
    """MET-1: la traza registra qué tabla sirve `mathlib` (muestra vs índice completo)."""
    build_table(retriever.db_path, "mathlib_full", MATHLIB, MATHLIB_FTS_COLUMNS)
    retriever.tables["mathlib"] = "mathlib_full"
    retriever.search(_req(k=2))
    f = next(Path(retriever.trace_dir).glob("p1:*.jsonl"))
    ev = json.loads(f.read_text(encoding="utf-8").splitlines()[-1])
    assert ev["versions"]["mathlib_table"] == "mathlib_full"


def test_run_id_in_context_groups_searches_into_one_trace_file(retriever):
    """TRZ-1: con `context.run_id`, los eventos de varias búsquedas del mismo par
    (config, problema) van al mismo archivo — antes cada búsqueda creaba el suyo,
    sin poder atribuirla a una corrida/config."""
    ctx = {"problem_id": "p1", "subgoal_id": "s1", "attempt": 1, "agent": "test", "run_id": "eval-v1:full:p1"}
    retriever.search(_req(context=ctx))
    retriever.search(_req(context={**ctx, "subgoal_id": "s2"}))
    run_files = list(Path(retriever.trace_dir).glob("eval-v1*.jsonl"))
    assert len(run_files) == 1
    events = run_files[0].read_text(encoding="utf-8").splitlines()
    assert len(events) == 2
    assert not list(Path(retriever.trace_dir).glob("p1:*.jsonl"))  # nada quedó en el formato viejo (por-búsqueda)
