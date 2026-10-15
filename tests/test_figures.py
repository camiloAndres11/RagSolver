"""Fase 2, QA gate de figuras (docs/PLAN_RAG.md Fase 2, tarea 10): los problemas
8, 16, 17, 22, 24, 37 y 99 tienen su figura recortada, y la red de seguridad
vectorial no encuentra dibujos que MinerU haya dejado fuera."""

from __future__ import annotations

from pathlib import Path

import pytest

from ragsolver.rag.figures import crop_figures
from ragsolver.rag.mineru_blocks import image_locators, load_pages, render_markdown
from ragsolver.rag.parse_structure import pair_problems_and_solutions, parse_markdown

ROOT = Path(__file__).resolve().parent.parent
PDF_PATH = ROOT / "problemas resueltos olimpiadas.pdf"
MINERU_DIR = ROOT / "data/parsed/edd3436ca148/mineru"

pytestmark = pytest.mark.skipif(not (PDF_PATH.exists() and MINERU_DIR.exists()), reason="requiere el PDF y la salida de MinerU")

QA_GATE_PROBLEMS = (8, 16, 17, 22, 24, 37, 99)


def test_qa_gate_figures_are_cropped_and_linked(tmp_path):
    pages = load_pages(MINERU_DIR)
    figures, warnings = crop_figures(PDF_PATH, pages, tmp_path, "edd3436ca148", dpi=72)
    assert warnings == []  # MinerU cubrió todos los dibujos vectoriales reales
    assert len(figures) == 53 and all(Path(f.image_path).stat().st_size > 0 for f in figures)

    by_id = {f.figure_id: f for f in figures}
    locs = image_locators(pages, "edd3436")
    cards, _ = pair_problems_and_solutions(parse_markdown(render_markdown(pages, "edd3436")))
    for card in cards:
        if card["item_number"] in QA_GATE_PROBLEMS:
            fids = [f"edd3436ca148:fig:{locs[l][0]}:{l.rsplit(':', 1)[1]}" for l in card["image_locators"]]
            assert fids and all(fid in by_id for fid in fids), f"problema {card['item_number']} sin figura"

    # problema 37: seis sub-figuras con su pie ("tipo uno" ... "tipo seis")
    p37 = next(c for c in cards if c["item_number"] == 37)
    captions = {by_id[f"edd3436ca148:fig:{locs[l][0]}:{l.rsplit(':', 1)[1]}"].caption for l in p37["image_locators"]}
    assert captions == {"tipo uno", "tipo dos", "tipo tres", "tipo cuatro", "tipo cinco", "tipo seis"}
    # los dibujos vectoriales (22, 24, 37) quedan marcados como detectados por ambas vías
    assert {f.detected_by for f in figures} >= {"layout", "both"}
