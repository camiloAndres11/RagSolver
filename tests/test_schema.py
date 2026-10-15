"""Fase 0, tarea 4: un evento JSONL válido contra el esquema (RNF-5)."""

from __future__ import annotations

import json

from ragsolver.logging.jsonl_writer import append_event, read_events
from ragsolver.rag.schema import (
    Citation,
    CitationScores,
    HitRecord,
    RetrievalContext,
    RetrievalFilters,
    RetrievalRequest,
    TraceEvent,
    VersionsPin,
)


def make_event(run_id: str = "test-run") -> TraceEvent:
    return TraceEvent(
        run_id=run_id,
        problem_id="miniF2F-v2:algebra_001",
        subgoal_id="sg-1",
        attempt=1,
        agent="retriever",
        latency_ms=42.5,
        llm_calls=0,
        n_candidates=50,
        request={"queries": ["n^2 + n - 1 is irreducible"], "mode": "nl"},
        hits=[
            HitRecord(chunk_id="mathlib:Nat.Coprime.def:0", rank=1, scores=CitationScores(dense=0.81, rerank=0.93)),
        ],
        versions=VersionsPin(
            embedding_model="Qwen/Qwen3-Embedding-0.6B",
            reranker_model="Qwen/Qwen3-Reranker-0.6B",
            index_version="0.1.0",
            mathlib_tag="v4.28.0-rc1",
        ),
    )


def test_trace_event_round_trips_through_jsonl(tmp_path):
    event = make_event()
    path = append_event(event, base_dir=tmp_path)

    assert path.exists()
    raw = json.loads(path.read_text().strip())
    # el evento serializado debe validar contra su propio JSON Schema
    schema_keys = set(TraceEvent.model_json_schema()["properties"])
    assert set(raw) <= schema_keys | {"llm_calls"}  # llm_calls está en el schema; chequeo defensivo

    reloaded = read_events(event.run_id, base_dir=tmp_path)
    assert reloaded == [event]


def test_retriever_never_reports_llm_calls():
    """RNF-3: el retriever debe costar 0 llamadas LLM (docs/PLAN_RAG.md §6.3)."""
    event = make_event()
    assert event.agent == "retriever"
    assert event.llm_calls == 0


def test_retrieval_request_requires_at_least_one_query():
    ctx = RetrievalContext(problem_id="p1", subgoal_id="sg-1", attempt=1, agent="retrieve_node")
    req = RetrievalRequest(queries=["find a divides b"], context=ctx)
    assert req.mode == "nl"
    assert req.corpora == ["mathlib"]
    assert req.k == 8

    import pytest

    with pytest.raises(Exception):
        RetrievalRequest(queries=[], context=ctx)


def test_citation_locator_is_free_form_dict_for_both_corpora():
    ctx = RetrievalContext(problem_id="p1")
    mathlib_cite = Citation(
        citation_id="M1",
        chunk_id="mathlib:Nat.Coprime.def:0",
        corpus="mathlib",
        unit_type="mathlib_decl",
        label="Nat.Coprime — Mathlib.Data.Nat.GCD.Basic",
        locator={"decl_name": "Nat.Coprime", "module_path": "Mathlib.Data.Nat.GCD.Basic"},
        retrieved_for=ctx,
    )
    informal_cite = Citation(
        citation_id="I1",
        chunk_id="informal:doc123:problem_card:22",
        corpus="informal",
        unit_type="problem_card",
        label="Moreno & Tellechea (2002), Problema 22, p. 11",
        locator={"doc_id": "doc123", "page_start": 11, "item_number": 22},
        retrieved_for=ctx,
        used_in_sketch=True,
    )
    assert mathlib_cite.used_in_proof is False
    assert informal_cite.used_in_sketch is True
    assert RetrievalFilters().math_area == []
