"""Contratos tipados del módulo RAG (RNF-4).

Espejo exacto de docs/PLAN_RAG.md §7 (esquema de metadatos) y §8 (contratos del
recuperador). No importar nada de ragsolver.agents/ragsolver.orchestrator desde
aquí — el RAG es una capa independiente que los agentes consumen (docs/PLAN_RAG.md §0).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

Corpus = Literal["mathlib", "informal"]
QueryMode = Literal["nl", "formal", "error", "exemplar"]

# ---------------------------------------------------------------------------
# §7.1 — documents
# ---------------------------------------------------------------------------


class Document(BaseModel):
    doc_id: str
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    source_type: Literal["olympiad_collection", "paper", "textbook", "notes", "mathlib"]
    language: str = "en"
    file_path: str = ""
    sha256: str = ""
    n_pages: int = 0
    url: str = ""
    license: str = ""
    redistributable: bool = False
    parser: str = ""
    parser_version: str = ""
    parser_tier: str = ""
    text_layer_reliable: bool | None = None
    ingested_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    index_version: str = ""


# ---------------------------------------------------------------------------
# §7.2 — chunks (núcleo común) + §7.3/§7.4 extensiones por corpus
# ---------------------------------------------------------------------------


class ChunkBase(BaseModel):
    """Campos comunes a las dos tablas LanceDB (`mathlib`, `informal`)."""

    chunk_id: str
    doc_id: str
    corpus: Corpus
    unit_type: str
    text: str  # lo que se embebe y se indexa en BM25/FTS
    embedding: list[float] | None = None
    embedding_model: str = ""
    embedding_dim: int = 0
    index_version: str = ""
    content_sha: str = ""


class InformalChunk(ChunkBase):
    """Tabla `informal`: problem_card, solution_step, figure, paper_section, theorem."""

    corpus: Literal["informal"] = "informal"
    parent_id: str | None = None
    children_ids: list[str] = Field(default_factory=list)
    order: int = 0
    content_md: str = ""
    context_summary: str = ""
    page_start: int | None = None
    page_end: int | None = None
    bboxes: list[list[float]] = Field(default_factory=list)  # [page, x0, y0, x1, y1] normalizados
    section_path: list[str] = Field(default_factory=list)
    item_number: int | None = None
    language: str = "es"
    math_area: list[str] = Field(default_factory=list)
    techniques: list[str] = Field(default_factory=list)
    theorems_used: list[str] = Field(default_factory=list)
    mathlib_candidates: list[str] = Field(default_factory=list)
    difficulty: int | None = None
    keywords: list[str] = Field(default_factory=list)
    figure_ids: list[str] = Field(default_factory=list)
    refs_to: list[str] = Field(default_factory=list)


class MathlibChunk(ChunkBase):
    """Tabla `mathlib`: una declaración por registro (§7.4)."""

    corpus: Literal["mathlib"] = "mathlib"
    unit_type: Literal["mathlib_decl"] = "mathlib_decl"
    decl_name: str
    decl_names_alt: list[str] = Field(default_factory=list)
    module_path: str = ""
    kind: str = ""
    signature: str = ""
    type: str = ""
    value: str = ""
    informal_name: str = ""
    informal_description: str = ""
    docstring: str = ""
    dependencies: list[str] = Field(default_factory=list)
    mathlib_tag: str = ""
    lean_version: str = ""
    docs_url: str = ""


class Figure(BaseModel):
    """§7.3 — tabla `figures`; también existe como InformalChunk(unit_type='figure')."""

    figure_id: str
    image_path: str
    image_sha: str = ""
    page: int
    bbox: list[float]
    detected_by: Literal["layout", "vector_cluster", "raster", "both"]
    caption: str = ""
    description: str = ""
    labels: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    relations: list[str] = Field(default_factory=list)
    given_values: list[str] = Field(default_factory=list)
    figure_type: Literal["geometric_diagram", "grid", "plot", "table_image", "other"] = "other"
    linked_chunk_ids: list[str] = Field(default_factory=list)
    description_model: str = ""
    description_version: str = ""


# ---------------------------------------------------------------------------
# §7.5 — Citation
# ---------------------------------------------------------------------------


class RetrievalContext(BaseModel):
    problem_id: str
    subgoal_id: str = ""
    attempt: int = 0
    agent: str = ""
    run_id: str = ""  # TRZ-1: si se da, el evento de traza va al archivo de esta corrida en vez de uno por búsqueda


class CitationScores(BaseModel):
    dense: float | None = None
    sparse: float | None = None
    fused: float | None = None
    rerank: float | None = None


class Citation(BaseModel):
    citation_id: str
    chunk_id: str
    corpus: Corpus
    unit_type: str
    label: str
    locator: dict = Field(default_factory=dict)
    scores: CitationScores = Field(default_factory=CitationScores)
    retrieved_for: RetrievalContext
    used_in_proof: bool = False
    used_in_sketch: bool = False
    snippet: str = ""


# ---------------------------------------------------------------------------
# §7.6 — evento de traza JSONL (RNF-5)
# ---------------------------------------------------------------------------


class VersionsPin(BaseModel):
    embedding_model: str = ""
    reranker_model: str = ""
    index_version: str = ""
    mathlib_tag: str = ""
    mathlib_table: str = ""  # MET-1: qué tabla sirve el corpus `mathlib` (`mathlib` muestra / `mathlib_full`)


class HitRecord(BaseModel):
    chunk_id: str
    rank: int
    scores: CitationScores = Field(default_factory=CitationScores)


class TraceEvent(BaseModel):
    run_id: str
    problem_id: str
    subgoal_id: str = ""
    attempt: int = 0
    agent: str = "retriever"
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    latency_ms: float = 0.0
    llm_calls: int = 0  # el retriever SIEMPRE reporta 0 (RNF-3, §6.3 del plan)
    tokens: int = 0
    request: dict = Field(default_factory=dict)
    n_candidates: int = 0
    hits: list[HitRecord] = Field(default_factory=list)
    versions: VersionsPin = Field(default_factory=VersionsPin)


# ---------------------------------------------------------------------------
# §8.1/§8.2 — contratos del recuperador
# ---------------------------------------------------------------------------


class RetrievalFilters(BaseModel):
    math_area: list[str] = Field(default_factory=list)
    doc_ids: list[str] = Field(default_factory=list)
    unit_types: list[str] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list)


class RetrievalRequest(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=4)
    rerank_against: str | None = None
    mode: QueryMode = "nl"
    corpora: list[Corpus] = Field(default_factory=lambda: ["mathlib"])
    k: int = 8
    n_candidates: int = 50
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    rerank: bool = True
    include_figures: bool = True
    context: RetrievalContext


class Hit(BaseModel):
    chunk_id: str
    corpus: Corpus
    unit_type: str
    rank: int
    text: str
    content_md: str = ""
    metadata: dict = Field(default_factory=dict)
    figures: list[dict] = Field(default_factory=list)
    citation: Citation


class RetrievalTrace(BaseModel):
    latency_ms: float = 0.0
    n_candidates: int = 0
    versions: VersionsPin = Field(default_factory=VersionsPin)


class RetrievalResult(BaseModel):
    hits: list[Hit] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    trace: RetrievalTrace = Field(default_factory=RetrievalTrace)
