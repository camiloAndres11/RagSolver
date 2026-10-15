"""Fase 2 — Corpus B de punta a punta (docs/PLAN_RAG.md §6.2, tareas 3-9):

  MinerU (JSON de bloques)
    -> huecos del layout transcritos por VLM (Plan B acotado)
    -> QA de fórmulas por página (6b)
    -> Markdown regenerado -> problem cards (parse_structure)
    -> soluciones con numeral perdido: split por LLM
    -> recortes de figuras + descripciones (6a)
    -> resumen contextual por card (6c)
    -> chunks `problem_card` + `figure` -> data/corpus/informal.jsonl, figures.jsonl

Idempotente: MinerU no se re-ejecuta (lee su salida ya guardada) y toda llamada
al VLM/LLM queda cacheada en data/parsed/<doc_id>/enrich/. `--skip-llm` produce
el corpus sin enriquecimiento (context_summary vacío), útil para tests.

Uso: python -m ragsolver.rag.ingest_informal --pdf "problemas resueltos olimpiadas.pdf"
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pymupdf
import yaml

from ragsolver.rag.enrich import Enricher
from ragsolver.rag.figures import crop_figures
from ragsolver.rag.ingest_pdf import Inventory, inventory
from ragsolver.rag.mineru_blocks import (
    extract_item_numbers,
    find_uncovered_regions,
    image_locators,
    insert_block,
    load_pages,
    render_markdown,
    section_chunks,
    text_block,
)
from ragsolver.rag.parse_structure import pair_problems_and_solutions, parse_markdown, split_item
from ragsolver.rag.schema import Figure, InformalChunk


def build_document_record(inv: Inventory, pdf_path: str) -> dict:
    # Metadatos leídos del propio PDF (portada + metadata de Acrobat) durante el
    # análisis inicial de la sesión de diseño — no vienen de un LLM.
    return {
        "doc_id": inv.doc_id,
        "title": "Colectivo de Problemas resueltos para Estudiantes de Alto Rendimiento",
        "authors": ["Miguel Angel Moreno Núñez", "Eduardo Tellechea Armenta"],
        "year": 2002,
        "source_type": "olympiad_collection",
        "language": "es",
        "file_path": pdf_path,
        "sha256": inv.sha256,
        "n_pages": inv.n_pages,
        "license": "",
        "redistributable": False,  # no se conoce la licencia -> no publicar en HF
        "parser": "mineru",
        "parser_version": "4.0.3",
        "parser_tier": "standard",
        "text_layer_reliable": inv.text_layer_reliable,
        "index_version": "0.1.0",
    }


def _page_png(doc: pymupdf.Document, page: int, out_dir: Path, dpi: int, clip: pymupdf.Rect | None = None, name: str | None = None) -> Path:
    out = out_dir / (name or f"page_{page:03d}.png")
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        doc[page - 1].get_pixmap(dpi=dpi, clip=clip).save(out)
    return out


def fill_gaps(pdf_path: str, pages: dict, enricher: Enricher, out_dir: Path, min_words: int, dpi: int) -> list[dict]:
    """Plan B acotado: transcribe con el VLM las regiones que MinerU no cubrió y las
    inserta como bloques de texto sintéticos en orden de lectura."""
    doc = pymupdf.open(pdf_path)
    log = []
    for r in find_uncovered_regions(pdf_path, pages, min_words=min_words):
        page = doc[r.page - 1]
        W, H = page.rect.width, page.rect.height
        x0, y0, x1, y1 = r.bbox
        clip = pymupdf.Rect(0.08 * W, y0 - 6, 0.95 * W, y1 + 6)  # ancho completo de columna
        png = _page_png(doc, r.page, out_dir / "gaps", dpi, clip=clip, name=f"gap_p{r.page:03d}.png")
        md = enricher.transcribe_region(png)
        insert_block(pages, r.page, text_block(md, [clip.x0 / W, clip.y0 / H, clip.x1 / W, clip.y1 / H]))
        log.append({"page": r.page, "bbox_pt": [round(v) for v in r.bbox], "text_layer": r.words, "transcription": md})
    return log


def qa_formulas(pdf_path: str, pages: dict, enricher: Enricher, out_dir: Path, dpi: int) -> list[dict]:
    """6b: por página con >= 1 ecuación, el VLM revisa el LaTeX contra la imagen.
    Modifica los bloques in-place; devuelve el registro de cambios."""
    doc = pymupdf.open(pdf_path)
    jobs = []
    for pg in sorted(pages):
        refs: list[tuple[dict, bool]] = []  # (contenedor con clave "content", es inline)
        for b in pages[pg]:
            if b.get("synthetic"):
                continue
            if b["type"] == "equation":
                refs.append((b, False))
            elif isinstance(b.get("content"), list):
                refs += [(it, True) for it in b["content"] if it.get("type") == "equation_inline"]
        if refs:
            jobs.append((pg, refs, _page_png(doc, pg, out_dir / "pages", dpi)))
    results = enricher.map(lambda j: enricher.qa_formulas(j[2], [r["content"] for r, _ in j[1]], inline=[i for _, i in j[1]]), jobs)
    diffs = []
    for (pg, refs, _), items in zip(jobs, results):
        for item in items:
            ref, _ = refs[item["i"]]
            if item["changed"]:
                diffs.append({"page": pg, "before": ref["content"], "after": item["latex"]})
                ref["content"] = item["latex"]
            elif "rejected" in item:
                diffs.append({"page": pg, "before": ref["content"], "rejected": item["rejected"]})
    return diffs


def recover_missing_solutions(parsed: dict, enricher: Enricher) -> list[str]:
    """Soluciones cuyo numeral perdió el OCR: el LLM señala la primera línea dentro
    de la solución anterior y `split_item` corta ahí (docs/PLAN_RAG.md: 'casos ambiguos')."""
    problems = {p.number: p for p in parsed["problems"]}
    solutions = {s.number: s for s in parsed["solutions"]}
    notes = []
    for n in sorted(problems):
        if n in solutions or (n - 1) not in solutions:
            continue
        prev = solutions[n - 1]
        first_line = enricher.split_solution(n, prev.text_md, problems[n - 1].text_md, problems[n].text_md)
        res = split_item(prev, n, first_line) if first_line else None
        if res is None:
            notes.append(f"solución {n}: el LLM no encontró un corte verbatim ('{first_line[:60]}')")
            continue
        solutions[n - 1], solutions[n] = res
        notes.append(f"solución {n}: recuperada por split de la {n - 1} en '{first_line[:60]}'")
    parsed["solutions"] = [solutions[k] for k in sorted(solutions)]
    return notes


def build_chunks(cards: list[dict], figures_by_locator: dict[str, Figure], doc_id: str, enricher: Enricher | None) -> tuple[list[InformalChunk], list[Figure]]:
    empty_meta = {"context_summary": "", "math_area": [], "techniques": [], "theorems_used": [], "difficulty": None, "keywords": []}
    prepared = []
    for card in cards:
        n = card["item_number"]
        prob, sol = card["problem"], card["solution"]
        statement = prob.text_md if prob else ""
        solution = sol.text_md if sol else ""
        card_id = f"informal:{doc_id}:problem_card:{n}"
        figs = [figures_by_locator[loc] for loc in card["image_locators"] if loc in figures_by_locator]
        for f in figs:
            f.linked_chunk_ids = [card_id]
        prepared.append({"n": n, "prob": prob, "sol": sol, "statement": statement, "solution": solution, "card_id": card_id, "figs": figs})

    if enricher is not None:  # 6a en paralelo
        jobs = [(f, c["statement"], c["solution"]) for c in prepared for f in c["figs"]]

        def _describe(job):
            f, statement, solution = job
            d = enricher.describe_figure(f.image_path, statement, solution)
            f.description, f.labels, f.objects = d["description"], d["labels"], d["objects"]
            f.relations, f.given_values, f.figure_type = d["relations"], d["given_values"], d["figure_type"]
            f.description_model, f.description_version = d["model"], enricher.version

        enricher.map(_describe, jobs)

    for c in prepared:
        parts = [c["statement"]] if c["statement"] else []
        if c["solution"]:
            parts.append("## Solución\n\n" + c["solution"])
        if any(f.description for f in c["figs"]):
            parts.append("## Figures\n\n" + "\n\n".join(f"[{f.figure_id}] {f.description}" for f in c["figs"] if f.description))
        c["content_md"] = "\n\n".join(parts)

    if enricher is not None:  # 6c en paralelo
        metas = enricher.map(lambda c: enricher.summarize_card(c["content_md"], [f.description for f in c["figs"] if f.description]), prepared)
    else:
        metas = [dict(empty_meta) for _ in prepared]

    chunks: list[InformalChunk] = []
    used_figures: list[Figure] = []
    for c, meta in zip(prepared, metas):
        n, figs, card_id, content_md = c["n"], c["figs"], c["card_id"], c["content_md"]
        used_figures += figs
        text = (meta["context_summary"] + "\n\n" + content_md).strip()
        pages = [p for item in (c["prob"], c["sol"]) if item for p in (item.page_start, item.page_end)]
        chunks.append(
            InformalChunk(
                chunk_id=card_id,
                doc_id=doc_id,
                unit_type="problem_card",
                text=text,
                content_md=content_md,
                context_summary=meta["context_summary"],
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                section_path=["Problemario", str(n)],
                item_number=n,
                math_area=meta["math_area"],
                techniques=meta["techniques"],
                theorems_used=meta["theorems_used"],
                difficulty=meta["difficulty"],
                keywords=meta["keywords"],
                figure_ids=[f.figure_id for f in figs],
                children_ids=[f"informal:{doc_id}:figure:{f.figure_id.rsplit(':fig:', 1)[1]}" for f in figs if f.description],
                content_sha=hashlib.sha256(content_md.encode("utf-8")).hexdigest()[:16],
                index_version="0.1.0",
            )
        )
        for f in figs:
            if not f.description:
                continue
            chunks.append(
                InformalChunk(
                    chunk_id=f"informal:{doc_id}:figure:{f.figure_id.rsplit(':fig:', 1)[1]}",
                    doc_id=doc_id,
                    unit_type="figure",
                    text=f"{f.description}\n\nProblem: {c['statement']}",
                    content_md=f.description,
                    parent_id=card_id,
                    page_start=f.page,
                    page_end=f.page,
                    bboxes=[[f.page, *f.bbox]],
                    section_path=["Problemario", str(n)],
                    item_number=n,
                    language="en",
                    math_area=meta["math_area"],
                    keywords=f.labels + f.objects,
                    figure_ids=[f.figure_id],
                    content_sha=hashlib.sha256(f.description.encode("utf-8")).hexdigest()[:16],
                    index_version="0.1.0",
                )
            )
    return chunks, used_figures


def run(pdf_path: str, mineru_dir: str | None = None, parsed_root: str = "data/parsed", corpus_dir: str = "data/corpus", skip_llm: bool = False, ingest_yaml: str = "configs/ingest.yaml", sections: str = "none") -> dict:
    """`sections`: `none` (sólo problem cards), `front` (además, chunks `paper_section` de lo
    anterior al Problemario), `all` (todo el documento por secciones: papers/apuntes)."""
    cfg = yaml.safe_load(open(ingest_yaml, encoding="utf-8"))["pdf"]
    inv = inventory(pdf_path)
    doc_id = inv.doc_id
    out_dir = Path(parsed_root) / doc_id
    mineru_dir = mineru_dir or str(out_dir / "mineru")
    pages = load_pages(mineru_dir)
    report_numerals = extract_item_numbers(pages)
    doc_short = doc_id[:7]  # MinerU usa los 7 primeros hex del sha256 en sus locators
    enricher = None if skip_llm else Enricher(out_dir / "enrich")
    report: dict = {"doc_id": doc_id, "n_pages": inv.n_pages, "item_numbers_from_equations": report_numerals}

    if enricher is not None:
        report["gaps"] = fill_gaps(pdf_path, pages, enricher, out_dir, cfg["gap_min_words"], cfg["render_dpi"])
        report["formula_diffs"] = qa_formulas(pdf_path, pages, enricher, out_dir, cfg["render_dpi"])

    md = render_markdown(pages, doc_short)
    (out_dir / "full.fixed.md").write_text(md, encoding="utf-8")
    parsed = parse_markdown(md)
    if enricher is not None:
        report["split_notes"] = recover_missing_solutions(parsed, enricher)
    cards, warnings = pair_problems_and_solutions(parsed)

    figures, fig_warnings = crop_figures(pdf_path, pages, out_dir / "figures", doc_id, pad_pt=cfg["figure_crop_padding_pt"], dpi=cfg["render_dpi"], min_overlap=cfg["vector_cluster_min_overlap"])
    by_id = {f.figure_id: f for f in figures}
    by_locator = {}
    for loc, (pg, _) in image_locators(pages, doc_short).items():
        fid = f"{doc_id}:fig:{pg}:{loc.rsplit(':', 1)[1]}"
        if fid in by_id:
            by_locator[loc] = by_id[fid]
    # ponytail: figuras vectoriales no cubiertas por MinerU (ninguna en el PDF de ejemplo)
    # se asignan al primer ítem cuyo rango de páginas las contiene; afinar por orden de
    # lectura si aparece un corpus donde sí ocurra.
    for f in figures:
        if f.detected_by == "vector_cluster":
            for card in cards:
                items = [it for it in (card["problem"], card["solution"]) if it]
                if any(it.page_start <= f.page <= it.page_end for it in items):
                    loc = f"vector:{f.figure_id}"
                    card["image_locators"].append(loc)
                    by_locator[loc] = f
                    break

    chunks, used_figures = build_chunks(cards, by_locator, doc_id, enricher)
    if sections != "none":  # docs/PLAN_RAG.md §6.2 paso 7: papers/apuntes -> paper_section (400-800 tokens)
        for sc in section_chunks(md, doc_id, stop_at="Problemario" if sections == "front" else None):
            chunks.append(InformalChunk(**sc, text=sc["content_md"], content_sha=hashlib.sha256(sc["content_md"].encode("utf-8")).hexdigest()[:16], index_version="0.1.0"))
        report["n_sections"] = sum(1 for c in chunks if c.unit_type == "paper_section")

    corpus = Path(corpus_dir)
    corpus.mkdir(parents=True, exist_ok=True)
    with (corpus / "informal.jsonl").open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(c.model_dump_json() + "\n")
    with (corpus / "figures.jsonl").open("w", encoding="utf-8") as f:
        for fig in used_figures:
            f.write(fig.model_dump_json() + "\n")
    (out_dir / "document.json").write_text(json.dumps(build_document_record(inv, pdf_path), ensure_ascii=False, indent=2), encoding="utf-8")

    report.update(
        n_cards=len(cards),
        n_paired=sum(1 for c in cards if c["problem"] and c["solution"]),
        n_chunks=len(chunks),
        n_figures=len(used_figures),
        n_figures_detected=len(figures),
        figure_warnings=fig_warnings,
        warnings=warnings,
        llm_calls=enricher.calls if enricher else 0,
        models_exhausted=enricher.exhausted if enricher else [],
    )
    (out_dir / "ingest_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default="problemas resueltos olimpiadas.pdf")
    ap.add_argument("--mineru-dir", default=None, help="por defecto data/parsed/<doc_id>/mineru")
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--sections", default="none", choices=["none", "front", "all"], help="chunks paper_section: none | front (antes del Problemario) | all (papers/apuntes)")
    args = ap.parse_args()
    rep = run(args.pdf, args.mineru_dir, skip_llm=args.skip_llm, sections=args.sections)
    print(json.dumps({k: v for k, v in rep.items() if k not in ("gaps", "formula_diffs")}, indent=2, ensure_ascii=False))
    print("gaps:", len(rep.get("gaps", [])), "formula_diffs:", len(rep.get("formula_diffs", [])))
