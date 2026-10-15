"""Fase 2 — lector/render de bloques MinerU y cross-check con la capa de texto,
contra la salida real guardada en data/parsed/edd3436ca148/mineru/."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ragsolver.rag.mineru_blocks import (
    extract_item_numbers,
    find_uncovered_regions,
    image_locators,
    insert_block,
    load_pages,
    render_markdown,
    split_item_number,
    text_block,
)
from ragsolver.rag.parse_structure import pair_problems_and_solutions, parse_markdown, split_item

ROOT = Path(__file__).resolve().parent.parent
PDF_PATH = ROOT / "problemas resueltos olimpiadas.pdf"
MINERU_DIR = ROOT / "data/parsed/edd3436ca148/mineru"
ORIG_MD = ROOT / "data/parsed/edd3436ca148/full.md"

pytestmark = pytest.mark.skipif(not (PDF_PATH.exists() and MINERU_DIR.exists()), reason="requiere el PDF y la salida de MinerU")


@pytest.fixture(scope="module")
def pages():
    pages = load_pages(MINERU_DIR)
    found = extract_item_numbers(pages)
    assert sorted(n for _, n in found) == [27, 52, 74, 88, 100]
    return pages


def test_split_item_number_recovers_numerals_absorbed_by_equations():
    assert split_item_number("2 7. A = B") == (27, "A = B")
    assert split_item_number(r"\begin{array}{l} 5 2. \text {Tenemos} x") == (52, r"\begin{array}{l} \text {Tenemos} x")
    assert split_item_number(r"\left(\frac{1}{2}\right)\tag{88.}") == (88, r"\left(\frac{1}{2}\right)")
    # numerales de ecuación reales (sin punto) y decimales espaciados NO se tocan
    assert split_item_number(r"x + y \tag {1}") == (None, r"x + y \tag {1}")
    assert split_item_number("1 0 . 5 + x") == (None, "1 0 . 5 + x")
    assert split_item_number("1 + 3 + 5 = n^2") == (None, "1 + 3 + 5 = n^2")


def test_render_matches_mineru_markdown_except_known_fixes(pages):
    """El render propio reproduce el full.md de mineru-kit; las diferencias
    admitidas son los numerales recuperados, tablas en formato pipe y estilos."""
    mine = render_markdown(pages, "edd3436")
    orig = ORIG_MD.read_text(encoding="utf-8")

    def by_page(md):
        out = {}
        for p in re.split(r"(?=<!-- page \d+ of \d+ -->)", md):
            m = re.match(r"<!-- page (\d+) of", p)
            if m:
                out[int(m.group(1))] = [l for l in p.strip().splitlines() if l.strip() and not re.fullmatch(r"\d{1,2}", l.strip())]
        return out

    a, b = by_page(orig), by_page(mine)
    assert set(a) == set(b) == set(range(1, 78))
    differing = {pg for pg in a if a[pg] != b[pg]}
    assert differing <= {7, 16, 36, 37, 50, 51, 58, 63, 70, 75, 76, 77}, differing


def test_numeral_fixes_raise_pairing_to_98_without_llm(pages):
    md = render_markdown(pages, "edd3436")
    cards, warnings = pair_problems_and_solutions(parse_markdown(md))
    paired = sum(1 for c in cards if c["problem"] and c["solution"])
    assert paired == 98
    # los dos restantes son los casos que sí requieren VLM/LLM (docs/DECISIONS.md)
    assert {int(re.search(r"\d+", w).group()) for w in warnings} == {55, 77}


def test_text_layer_cross_check_finds_the_lost_solution_77(pages):
    regions = find_uncovered_regions(PDF_PATH, pages, min_words=4)
    by_page = {r.page: r for r in regions}
    assert 64 in by_page and "77." in by_page[64].words
    # radicales del Equation Editor dentro de bloques equation/text no cuentan
    assert len(regions) <= 12


def test_insert_block_keeps_reading_order_and_reaches_the_parser(pages):
    pg64 = [dict(b) for b in pages[64]]
    fake = {64: pg64}
    insert_block(fake, 64, text_block("77. Solución transcrita.", [0.1, 0.5, 0.9, 0.7]))
    ys = [b["bbox"][1] for b in fake[64]]
    assert ys == sorted(ys)
    md = render_markdown({**pages, 64: fake[64]}, "edd3436")
    cards, warnings = pair_problems_and_solutions(parse_markdown(md))
    assert not any("77" in w for w in warnings)


def test_image_locators_match_markdown_locators(pages):
    md = render_markdown(pages, "edd3436")
    in_md = set(re.findall(r"\(doc:[^)]+\)", md))
    assert {f"({k})" for k in image_locators(pages, "edd3436")} == in_md
    assert len(in_md) == 53


def test_split_item_cuts_inside_display_block_at_its_opener():
    from ragsolver.rag.parse_structure import Item

    item = Item(54, "54. texto\n\n$$\nx = 1\n$$\n\nmás texto", 51, 52, [])
    head, tail = split_item(item, 55, "x = 1")
    assert head.text_md == "54. texto"
    assert tail.number == 55 and tail.text_md.startswith("55.\n\n$$\nx = 1")
    assert split_item(item, 55, "no existe") is None


def test_section_chunks_cover_front_matter_with_section_paths(pages):
    """Camino `paper_section` (papers/apuntes): las páginas de introducción del PDF, que el
    parser de problemas ignora, salen como chunks de ~3000 caracteres con `section_path`."""
    from ragsolver.rag.mineru_blocks import section_chunks

    md = render_markdown(pages, "edd3436")
    chunks = section_chunks(md, "edd3436ca148", stop_at="Problemario")
    assert 5 <= len(chunks) <= 40
    assert all(c["unit_type"] == "paper_section" and 200 <= len(c["content_md"]) <= 3600 for c in chunks)
    assert all(c["page_start"] <= c["page_end"] < 9 for c in chunks)  # todo antes del Problemario (p. 9)
    titles = {t for c in chunks for t in c["section_path"]}
    assert "Introducción" in titles and "Acerca de las Olimpiadas de Matemáticas" in titles
    assert not any("Problemario" in c["section_path"] for c in chunks)
    ids = [c["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids))
