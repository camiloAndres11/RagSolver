"""Genera docs/qa_ingest_<doc_id>.md a partir de ingest_report.json, figures.jsonl e
informal.jsonl (docs/PLAN_RAG.md Fase 2, tarea 10). La rúbrica manual de las 15
descripciones de figura se completa a mano en el .md generado.

SEC-2 (docs/AUDIT_2026-09-23.md, docs/PLAN_CORRECCIONES.md Fase 2): este reporte
se publica (git, HF). Si el documento tiene `redistributable=false`, se redacta
todo contenido literal derivado de su texto — fórmulas, transcripciones de
huecos y descripciones de figura — y sólo quedan conteos y metadatos
estructurales; mismo criterio que ya aplica `scripts/publish_hf.py` a las
tablas `informal`/`figures` (no las sube si el documento no es redistribuible).

Uso: python scripts/qa_ingest_report.py --doc-id edd3436ca148
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

QA_GATE = (8, 16, 17, 22, 24, 37, 99)
_REDACTED_NOTE = "Contenido derivado del documento redactado (`redistributable=false`); disponible localmente en `data/parsed/` (gitignored), nunca en git/HF."


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc-id", default="edd3436ca148")
    args = ap.parse_args()
    parsed = Path("data/parsed") / args.doc_id
    report = json.loads((parsed / "ingest_report.json").read_text(encoding="utf-8"))
    chunks = [json.loads(l) for l in open("data/corpus/informal.jsonl", encoding="utf-8") if l.strip()]
    figures = [json.loads(l) for l in open("data/corpus/figures.jsonl", encoding="utf-8") if l.strip()]
    cards = [c for c in chunks if c["unit_type"] == "problem_card"]
    by_n = {c["item_number"]: c for c in cards}
    doc_path = parsed / "document.json"
    redistributable = json.loads(doc_path.read_text(encoding="utf-8")).get("redistributable", False) if doc_path.exists() else False

    diffs = report.get("formula_diffs", [])
    accepted = [d for d in diffs if "after" in d]
    rejected = [d for d in diffs if "rejected" in d]
    areas = Counter(a for c in cards for a in c["math_area"])
    fig_by_id = {f["figure_id"]: f for f in figures}

    lines = [
        f"# QA de ingesta — `{args.doc_id}` (problemas resueltos olimpiadas.pdf)",
        "",
        "Generado por `scripts/qa_ingest_report.py`; criterios en `docs/PLAN_RAG.md` Fase 2, tarea 10.",
        "",
        "## Resumen",
        "",
        f"- Páginas: {report['n_pages']} · cards: {report['n_cards']} · **emparejadas problema↔solución: {report['n_paired']}/100** · chunks: {report['n_chunks']} (cards + figuras)",
        f"- Figuras recortadas: {report['n_figures_detected']} · con descripción VLM: {sum(1 for f in figures if f['description'])} · avisos red de seguridad vectorial: {len(report['figure_warnings'])}",
        f"- Huecos del layout transcritos por VLM: {len(report.get('gaps', []))} regiones",
        f"- QA de fórmulas: {len(accepted)} correcciones aplicadas, {len(rejected)} propuestas rechazadas por las guardas",
        f"- Llamadas al VLM/LLM en esta corrida (sin caché): {report['llm_calls']}",
        f"- Áreas (cards): {dict(areas.most_common())}",
        "",
        "## Gate 1 — emparejamiento problema↔solución (objetivo 100/100)",
        "",
        f"Resultado: **{report['n_paired']}/100**. Avisos: {report['warnings'] or 'ninguno'}.",
        "",
        "Notas del split por LLM:" if redistributable else f"Notas del split por LLM: {len(report.get('split_notes', []))}. {_REDACTED_NOTE}",
        *([f"- {n}" for n in report.get("split_notes", [])] if redistributable else []),
        "",
        "## Gate 2 — figuras de los problemas 8, 16, 17, 22, 24, 37, 99",
        "",
    ]
    if redistributable:
        lines += ["| Problema | Figuras | detected_by | Descripción (inicio) |", "|---|---|---|---|"]
        for n in QA_GATE:
            for fid in by_n[n]["figure_ids"]:
                f = fig_by_id.get(fid)
                if f:
                    lines.append(f"| {n} | `{fid}` | {f['detected_by']} | {f['description'][:110]}… |")
    else:
        lines += [f"_{_REDACTED_NOTE} (las descripciones se generan a partir de las figuras del documento)._", "", "| Problema | Figuras | detected_by |", "|---|---|---|"]
        for n in QA_GATE:
            for fid in by_n[n]["figure_ids"]:
                f = fig_by_id.get(fid)
                if f:
                    lines.append(f"| {n} | `{fid}` | {f['detected_by']} |")
    lines += [
        "",
        "## Rúbrica manual — 15 descripciones (sí/no: ¿nombra todos los puntos?, ¿relaciones correctas?, ¿valores dados?)",
        "",
        "| # | Figura | Puntos | Relaciones | Valores | Observación |",
        "|---|---|---|---|---|---|",
        *[f"| {i+1} | `{f['figure_id']}` |  |  |  |  |" for i, f in enumerate(figures[:15])],
        "",
        "## Huecos del layout de MinerU recuperados por VLM",
        "",
    ]
    if redistributable:
        lines += [
            "| Página | Capa de texto (evidencia) | Transcripción (inicio) |",
            "|---|---|---|",
            *[f"| {g['page']} | {g['text_layer'][:70]} | {g['transcription'][:90].replace(chr(10), ' ')} |" for g in report.get("gaps", [])],
        ]
    else:
        gap_pages = ", ".join(str(g["page"]) for g in report.get("gaps", []))
        lines.append(f"{len(report.get('gaps', []))} regiones recuperadas (páginas: {gap_pages or '—'}). {_REDACTED_NOTE}")
    lines += [
        "",
        "## QA de fórmulas (6b) — correcciones aplicadas",
        "",
    ]
    if redistributable:
        lines += ["| Página | Antes | Después |", "|---|---|---|", *[f"| {d['page']} | `{d['before'][:60]}` | `{d['after'][:60]}` |" for d in accepted]]
    else:
        pages = Counter(d["page"] for d in accepted)
        lines.append(f"{len(accepted)} correcciones en {len(pages)} páginas (páginas: {', '.join(str(p) for p in sorted(pages))}). {_REDACTED_NOTE}")
    lines += [
        "",
        f"Rechazadas por las guardas (cambio sólo cosmético o absorción de texto vecino): {len(rejected)} — detalle en `data/parsed/{args.doc_id}/ingest_report.json` (local, gitignored).",
        "",
        "## Gold de OCR (10 páginas transcritas a mano)",
        "",
        "Pendiente: requiere transcripción manual humana (no se fabrica). Sustituto disponible hoy: las 5 correcciones reales detectadas por el VLM en 2 páginas de muestra (docs/DECISIONS.md #19).",
        "",
    ]
    out = Path("docs") / f"qa_ingest_{args.doc_id}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
