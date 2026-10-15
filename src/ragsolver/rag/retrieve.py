"""Fase 3 — Servicio de recuperación y citación (docs/PLAN_RAG.md §6.3, §6.4, §8).

`Retriever.search(RetrievalRequest) -> RetrievalResult`:
  plantilla por `mode` -> por consulta: denso top-N + FTS top-N (prefiltro SQL)
  -> RRF multi-consulta y multi-canal -> rerank (cross-encoder) contra `rerank_against`
  -> dedupe (figura vs. su problem card; máx. por módulo opcional) -> top-k
  -> Citation[] + figuras adjuntas + evento JSONL.

Modo `error`: los identificadores Lean del mensaje se buscan de forma exacta
(`decl_name IN (...)`) y se fijan al tope del resultado; el resto de la consulta
va por los canales normales. Costo en llamadas LLM: siempre 0 (RNF-3).
"""

from __future__ import annotations

import os
import re
import time
import uuid
from functools import lru_cache
from pathlib import Path

import yaml

from ragsolver.logging.jsonl_writer import append_event
from ragsolver.rag.embed import embed_queries
from ragsolver.rag.index import exact_match, fts_search, open_table, rrf_fuse, sql_quote, vector_search
from ragsolver.rag.schema import (
    Citation,
    CitationScores,
    Hit,
    HitRecord,
    RetrievalFilters,
    RetrievalRequest,
    RetrievalResult,
    RetrievalTrace,
    TraceEvent,
    VersionsPin,
)

# `unknown identifier 'Nat.Prime.two_le'`, `unknown constant 'Foo.bar'`, `` `Foo.bar` ``
_QUOTED_IDENT_RE = re.compile(r"(?:identifier|constant|namespace|declaration)\s+'([^']+)'|`([^`\s]+)`")
_DOTTED_IDENT_RE = re.compile(r"\b[A-Z][A-Za-z0-9_']*(?:\.[A-Za-z0-9_']+)+\b")


def lean_identifiers(text: str) -> list[str]:
    """Identificadores Lean citados en un mensaje de error (sin duplicados, en orden)."""
    found = [a or b for a, b in _QUOTED_IDENT_RE.findall(text)]
    found += _DOTTED_IDENT_RE.findall(_QUOTED_IDENT_RE.sub(" ", text))  # los citados ya están; evita `Baz.qux` de `Baz.qux'`
    return list(dict.fromkeys(f for f in found if f))


def _mathlib_citation_label(row: dict) -> str:
    return f"{row.get('decl_name', '?')} — {row.get('module_path', '?')}"


def _mathlib_locator(row: dict) -> dict:
    return {
        "decl_name": row.get("decl_name"),
        "module_path": row.get("module_path"),
        "docs_url": row.get("docs_url"),
    }


def _informal_citation_label(row: dict) -> str:
    item = row.get("item_number")
    page = row.get("page_start")
    suffix = f", ítem {item}" if item else ""
    suffix += f", p. {page}" if page else ""
    return f"{row.get('doc_id', '?')}{suffix}"


def _informal_locator(row: dict) -> dict:
    return {
        "doc_id": row.get("doc_id"),
        "page_start": row.get("page_start"),
        "page_end": row.get("page_end"),
        "item_number": row.get("item_number"),
        "figure_ids": list(row.get("figure_ids") or []),
    }


_LABEL_BUILDERS = {"mathlib": _mathlib_citation_label, "informal": _informal_citation_label}
_LOCATOR_BUILDERS = {"mathlib": _mathlib_locator, "informal": _informal_locator}


def mathlib_table_name() -> str:
    """MET-1: qué tabla LanceDB sirve el corpus `mathlib` — misma fuente de verdad
    para el `Retriever` y para el pin de versiones que registran los agentes."""
    return os.environ.get("RAGSOLVER_MATHLIB_TABLE", "mathlib")


def sql_filter(corpus: str, filters: RetrievalFilters) -> str | None:
    """Prefiltro SQL de LanceDB a partir de §8.1 `filters` (sólo los campos que aplican al corpus)."""
    clauses = []
    if filters.doc_ids:
        clauses.append(f"doc_id IN ({', '.join(sql_quote(d) for d in filters.doc_ids)})")
    if filters.unit_types:
        clauses.append(f"unit_type IN ({', '.join(sql_quote(u) for u in filters.unit_types)})")
    if corpus == "mathlib" and filters.kinds:
        clauses.append(f"kind IN ({', '.join(sql_quote(k) for k in filters.kinds)})")
    if corpus == "informal" and filters.math_area:
        clauses.append(f"array_has_any(math_area, [{', '.join(sql_quote(a) for a in filters.math_area)}])")
    return " AND ".join(clauses) or None


class Retriever:
    def __init__(self, db_path: str = "data/index/lancedb", versions_path: str = "configs/versions.yaml", config_path: str = "configs/retrieval.yaml"):
        self.db_path = db_path
        self.versions = yaml.safe_load(open(versions_path, encoding="utf-8"))
        self.config = yaml.safe_load(open(config_path, encoding="utf-8")) if Path(config_path).exists() else {}
        self.channels: tuple[str, ...] = ("dense", "sparse")  # sólo para ablaciones de evaluate.py
        self.trace_dir = "data/eval/traces"
        # el corpus "mathlib" puede servirse desde otra tabla (p.ej. `mathlib_full`, 278k) sin tocar filtros ni citas
        self.tables = {"mathlib": mathlib_table_name()}

    @lru_cache(maxsize=8)
    def _table(self, name: str):
        return open_table(self.db_path, self.tables.get(name, name))

    @lru_cache(maxsize=1)
    def _reranker(self):
        import torch
        from sentence_transformers import CrossEncoder

        # bf16 en CPU: mitad de RAM que fp32 (docs/DECISIONS.md #40); RAGSOLVER_EMBED_BF16=0 restaura fp32
        dtype = torch.bfloat16 if os.environ.get("RAGSOLVER_EMBED_BF16", "1") == "1" else torch.float32
        return CrossEncoder(self.versions["reranker"]["model"], device="cpu", model_kwargs={"torch_dtype": dtype})

    # -- candidatos ------------------------------------------------------------

    def _candidates(self, corpus: str, request: RetrievalRequest) -> tuple[dict, list[list[str]], list[str]]:
        """(filas por id, listas rankeadas para RRF, ids fijados al tope)."""
        table = self._table(corpus)
        where = sql_filter(corpus, request.filters)
        instruction = (self.config.get("query_instructions") or {}).get(request.mode)
        rows_by_id: dict = {}
        ranked: list[list[str]] = []
        pinned: list[str] = []

        if request.mode == "error" and corpus == "mathlib":
            idents = [i for q in request.queries for i in lean_identifiers(q)]
            for row in exact_match(table, "decl_name", idents, where):
                rows_by_id.setdefault(row["chunk_id"], row)
                pinned.append(row["chunk_id"])
            if idents:  # tokens del identificador ("Nat Prime two_le") como consulta FTS extra
                sparse = fts_search(table, " ".join(re.sub(r"[._']", " ", i) for i in idents), request.n_candidates, where)
                for row in sparse:
                    rows_by_id.setdefault(row["chunk_id"], row)
                ranked.append([r["chunk_id"] for r in sparse])

        vectors = embed_queries(request.queries, model_name=self.versions["embedding"]["model"], instruction=instruction) if "dense" in self.channels else [None] * len(request.queries)
        for q_text, q_vec in zip(request.queries, vectors):
            if "dense" in self.channels:
                dense = vector_search(table, q_vec, request.n_candidates, where)
                for row in dense:
                    rows_by_id.setdefault(row["chunk_id"], row)
                ranked.append([r["chunk_id"] for r in dense])
            if "sparse" in self.channels:
                sparse = fts_search(table, q_text, request.n_candidates, where)
                for row in sparse:
                    rows_by_id.setdefault(row["chunk_id"], row)
                ranked.append([r["chunk_id"] for r in sparse])
        return rows_by_id, ranked, list(dict.fromkeys(pinned))

    # -- dedupe ------------------------------------------------------------------

    def _dedupe(self, corpus: str, rows: list[dict]) -> list[dict]:
        out: list[dict] = []
        if corpus == "informal":
            # figura cuyo problem card también fue recuperado: se colapsa en el card
            # (que ya lleva `figure_ids`); la figura sola sigue siendo un hit válido.
            ids = {r["chunk_id"] for r in rows}
            rows = [r for r in rows if not (r.get("unit_type") == "figure" and r.get("parent_id") in ids)]
        per_module: dict[str, int] = {}
        limit = int(self.config.get("max_per_module") or 0)
        for r in rows:
            if corpus == "mathlib" and limit:
                m = r.get("module_path", "")
                per_module[m] = per_module.get(m, 0) + 1
                if per_module[m] > limit:
                    continue
            out.append(r)
        return out

    def _figures(self, figure_ids: list[str]) -> list[dict]:
        if not figure_ids:
            return []
        try:
            table = self._table("figures")
        except Exception:  # tabla `figures` ausente (índice construido sin corpus informal)
            return []
        rows = exact_match(table, "figure_id", figure_ids)
        keep = ("figure_id", "image_path", "description", "page", "bbox", "figure_type", "labels")
        return [{k: r.get(k) for k in keep} for r in rows]

    # -- búsqueda ----------------------------------------------------------------

    def search(self, request: RetrievalRequest) -> RetrievalResult:
        t0 = time.time()
        ablation = self.config.get("ablation") or {}
        corpora = [c for c in request.corpora if c in (ablation.get("corpora") or request.corpora)]
        if ablation.get("enabled", True) is False:
            corpora = []

        all_hits: list[Hit] = []
        for corpus in corpora:
            rows_by_id, ranked, pinned = self._candidates(corpus, request)
            fused = rrf_fuse(ranked, k=int(self.config.get("rrf_k", 60)))
            fused_score = dict(fused)
            order_ids = pinned + [cid for cid, _ in fused if cid not in pinned]
            candidates = self._dedupe(corpus, [rows_by_id[c] for c in order_ids if c in rows_by_id])[: request.n_candidates]

            rerank_scores: dict[str, float] = {}
            if request.rerank and candidates:
                against = request.rerank_against or request.queries[0]
                scores = self._reranker().predict([(against, r["text"]) for r in candidates])
                rerank_scores = {r["chunk_id"]: float(s) for r, s in zip(candidates, scores)}
                unpinned = sorted((r for r in candidates if r["chunk_id"] not in pinned), key=lambda r: rerank_scores[r["chunk_id"]], reverse=True)
                candidates = [r for r in candidates if r["chunk_id"] in pinned] + unpinned

            prefix = "M" if corpus == "mathlib" else "I"
            for rank, row in enumerate(candidates[: request.k], start=1):
                cid = row["chunk_id"]
                citation = Citation(
                    citation_id=f"{prefix}{rank}",
                    chunk_id=cid,
                    corpus=corpus,
                    unit_type=row.get("unit_type", ""),
                    label=_LABEL_BUILDERS[corpus](row),
                    locator=_LOCATOR_BUILDERS[corpus](row),
                    scores=CitationScores(fused=fused_score.get(cid), rerank=rerank_scores.get(cid)),
                    retrieved_for=request.context,
                    snippet=(row.get("content_md") or row.get("text") or "")[:300],
                )
                figures = self._figures(list(row.get("figure_ids") or [])) if (corpus == "informal" and request.include_figures) else []
                all_hits.append(
                    Hit(
                        chunk_id=cid,
                        corpus=corpus,
                        unit_type=row.get("unit_type", ""),
                        rank=rank,
                        text=row.get("text", ""),
                        content_md=row.get("content_md", ""),
                        metadata={k: v for k, v in row.items() if k not in ("embedding", "text", "content_md", "_fts_text", "_distance", "_score")},
                        figures=figures,
                        citation=citation,
                    )
                )

        latency_ms = (time.time() - t0) * 1000
        pin = VersionsPin(
            embedding_model=self.versions["embedding"]["model"],
            reranker_model=self.versions["reranker"]["model"],
            index_version=self.versions["index"]["version"],
            mathlib_tag=self.versions["lean"]["mathlib_tag"],
            mathlib_table=self.tables.get("mathlib", "mathlib"),
        )
        # TRZ-1: si el llamador pasa `context.run_id` (el orquestador pasa el pid de la corrida), el
        # evento va al MISMO archivo que los eventos de los agentes de ese (config, problema) en vez
        # de crear un archivo nuevo por búsqueda. Sin run_id (CLI, demo, evaluate.py) se mantiene el
        # comportamiento anterior: un archivo por búsqueda, con sufijo aleatorio.
        append_event(
            TraceEvent(
                run_id=request.context.run_id or f"{request.context.problem_id}:{uuid.uuid4().hex[:8]}",
                problem_id=request.context.problem_id,
                subgoal_id=request.context.subgoal_id,
                attempt=request.context.attempt,
                agent="retriever",
                latency_ms=latency_ms,
                llm_calls=0,
                request=request.model_dump(mode="json"),
                n_candidates=request.n_candidates,
                hits=[HitRecord(chunk_id=h.chunk_id, rank=h.rank, scores=h.citation.scores) for h in all_hits],
                versions=pin,
            ),
            base_dir=self.trace_dir,
        )
        return RetrievalResult(
            hits=all_hits,
            citations=[h.citation for h in all_hits],
            trace=RetrievalTrace(latency_ms=latency_ms, n_candidates=request.n_candidates, versions=pin),
        )


def _cli(argv: list[str] | None = None) -> None:
    """`ragsolver search "query" --corpus mathlib --k 8` (docs/PLAN_RAG.md Fase 3, tarea 7)."""
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(prog="ragsolver search")
    ap.add_argument("query", nargs="+", help="una o más consultas (se fusionan con RRF)")
    ap.add_argument("--corpus", action="append", choices=["mathlib", "informal"], help="repetible; por defecto mathlib")
    ap.add_argument("--mode", default="nl", choices=["nl", "formal", "error", "exemplar"])
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--math-area", action="append", default=[])
    ap.add_argument("--db-path", default="data/index/lancedb")
    args = ap.parse_args(argv)

    req = RetrievalRequest(
        queries=args.query,
        mode=args.mode,
        corpora=args.corpus or ["mathlib"],
        k=args.k,
        rerank=not args.no_rerank,
        filters={"math_area": args.math_area},
        context={"problem_id": "cli", "agent": "cli"},
    )
    result = Retriever(db_path=args.db_path).search(req)
    for hit in result.hits:
        sc = hit.citation.scores
        print(f"[{hit.citation.citation_id}] {hit.citation.label}  (fused={sc.fused and round(sc.fused, 4)}, rerank={sc.rerank and round(sc.rerank, 3)})")
        print(f"    {hit.text[:160]!r}")
        for fig in hit.figures:
            print(f"    fig {fig['figure_id']}: {fig['image_path']} — {(fig['description'] or '')[:100]!r}")
    print(_json.dumps({"latency_ms": round(result.trace.latency_ms, 1), "n_hits": len(result.hits)}))


if __name__ == "__main__":
    _cli()
