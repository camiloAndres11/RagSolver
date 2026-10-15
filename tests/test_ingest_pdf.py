"""Fase 2, tareas 1-4, corridas contra el PDF real de ejemplo (no un mock):
la red de seguridad de figuras vectoriales y el inventario deben funcionar
sobre el corpus real que el usuario va a resolver, no sólo en un caso sintético.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ragsolver.rag.ingest_pdf import detect_vector_figures, inventory, render_pages

PDF_PATH = Path(__file__).resolve().parent.parent / "problemas resueltos olimpiadas.pdf"

pytestmark = pytest.mark.skipif(not PDF_PATH.exists(), reason="PDF de ejemplo no está en el repo")


def test_inventory_flags_unreliable_text_layer():
    inv = inventory(PDF_PATH)
    assert inv.n_pages == 77
    # docs/PLAN_RAG.md §3: la capa de texto de este PDF corrompe fórmulas (Equation Editor)
    assert inv.text_layer_reliable is False
    assert inv.short_line_ratio > 0.5


def test_render_pages_produces_one_png_per_page(tmp_path):
    paths = render_pages(PDF_PATH, tmp_path, dpi=100)
    assert len(paths) == 77
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)


# Problemas con figura confirmados visualmente en la sesión de diseño (docs/PLAN_RAG.md §3,
# §10 Fase 2 tarea 10 QA gate) -> página PDF donde aparecen.
KNOWN_FIGURE_PAGES = {8: 10, 16: 11, 17: 11, 22: 12, 24: 12, 37: 45, 99: 24}


def test_vector_figures_detected_on_known_geometric_pages():
    """Problemas 22, 24 y 37 usan dibujo vectorial (no imagen raster)."""
    figs = detect_vector_figures(PDF_PATH, min_size_pt=15.0)
    pages_with_vector_figs = {f.page for f in figs}
    for prob in (22, 24, 37):
        assert KNOWN_FIGURE_PAGES[prob] in pages_with_vector_figs, f"problema {prob} sin figura vectorial detectada"


def test_min_paths_six_would_have_missed_real_figures():
    """Regresión: el umbral original de 6 trazos (antes de recalibrar con datos reales,
    ver docs/DECISIONS.md) sólo detectaba 4/115 clusters reales del documento."""
    import pymupdf

    doc = pymupdf.open(PDF_PATH)
    n_with_old_threshold = 0
    for page in doc:
        drawings = page.get_drawings()
        if not drawings:
            continue
        for rect in page.cluster_drawings(drawings=drawings):
            n = sum(1 for d in drawings if pymupdf.Rect(d["rect"]).intersects(rect))
            if n >= 6:
                n_with_old_threshold += 1
    assert n_with_old_threshold < 10  # documenta por qué se cambió a un filtro por tamaño


def test_problems_without_vector_content_use_raster_images_instead():
    """Problemas 8, 16 y 17 no tienen dibujo vectorial: sus figuras son imágenes raster
    embebidas -- MinerU debe cubrirlas como bloques `image`, no la red de seguridad."""
    import pymupdf

    doc = pymupdf.open(PDF_PATH)
    for prob in (8, 16, 17):
        page = doc[KNOWN_FIGURE_PAGES[prob] - 1]
        assert len(page.get_images()) >= 1, f"problema {prob}: se esperaba imagen raster"
