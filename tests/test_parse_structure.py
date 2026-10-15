from __future__ import annotations

from pathlib import Path

import pytest

from ragsolver.rag.parse_structure import pair_problems_and_solutions, parse_markdown

SAMPLE_MD = """\
<!-- page 1 of 3 -->
## Problemario

1. Enunciado del problema uno.

2. Enunciado del problema dos con una figura.

![Image block](doc:abc123/tier:standard/page:1/block:5)

<!-- page 2 of 3 -->
## Soluciones

1. Solución del problema uno.

2.

$$
x = 2
$$

<!-- page 3 of 3 -->
## Bibliografía

Referencia 1.
"""


def test_parse_markdown_splits_problems_and_solutions():
    parsed = parse_markdown(SAMPLE_MD)
    assert parsed["warnings"] == []
    assert [p.number for p in parsed["problems"]] == [1, 2]
    assert [s.number for s in parsed["solutions"]] == [1, 2]


def test_problem_page_and_image_locator_captured():
    parsed = parse_markdown(SAMPLE_MD)
    p2 = next(p for p in parsed["problems"] if p.number == 2)
    assert p2.page_start == 1
    assert p2.image_locators == ["doc:abc123/tier:standard/page:1/block:5"]


def test_solution_starting_as_bare_number_then_formula_is_captured():
    """Caso real encontrado en el PDF de ejemplo: 'N.' solo en su línea, la
    fórmula sigue después (docs/DECISIONS.md, solución 13)."""
    parsed = parse_markdown(SAMPLE_MD)
    s2 = next(s for s in parsed["solutions"] if s.number == 2)
    assert "x = 2" in s2.text_md


def test_pair_problems_and_solutions_matches_by_number():
    parsed = parse_markdown(SAMPLE_MD)
    cards, warnings = pair_problems_and_solutions(parsed)
    assert len(cards) == 2
    assert warnings == []
    assert cards[0]["problem"].number == 1
    assert cards[0]["solution"].number == 1


def test_embedded_subcase_numbering_does_not_hijack_an_earlier_item():
    """Regresión: una solución posterior puede traer sub-casos numerados en su
    propio texto ("1. Prolongar...", "2. Prolongar...") que un split por línea
    confundiría con el inicio de un ítem nuevo, pisando el ítem 1 real (pasó
    de verdad con la solución 1 del PDF de ejemplo -- ver docs/DECISIONS.md)."""
    md = """\
## Soluciones

Contenido de la solución 1 sin numeral propio.

2. Solución dos, con dos sub-casos:

1. Prolongar el segmento DC por el lado de C.

2. Prolongar el segmento DA por el lado de A.

3. Solución tres.
"""
    parsed = parse_markdown(md)
    numbers = [s.number for s in parsed["solutions"]]
    assert numbers == [1, 2, 3]
    sol1 = next(s for s in parsed["solutions"] if s.number == 1)
    assert sol1.text_md == "Contenido de la solución 1 sin numeral propio."  # sin numeral -> lo que precede al 2
    sol2 = next(s for s in parsed["solutions"] if s.number == 2)
    assert "Prolongar el segmento DC" in sol2.text_md
    assert "Prolongar el segmento DA" in sol2.text_md  # los sub-casos quedan DENTRO de la solución 2


def test_pair_reports_missing_solution_without_dropping_the_problem():
    parsed = parse_markdown(SAMPLE_MD.replace("2.\n\n$$\nx = 2\n$$\n\n", ""))
    cards, warnings = pair_problems_and_solutions(parsed)
    assert len(cards) == 2
    assert any("problema 2 sin solución" in w for w in warnings)
    assert cards[1]["problem"].number == 2
    assert cards[1]["solution"] is None


# --- Integración contra la salida real de MinerU sobre el PDF de ejemplo ---

REAL_MD_PATH = Path(__file__).resolve().parent.parent / "data/parsed/edd3436ca148/full.md"

pytestmark_real = pytest.mark.skipif(not REAL_MD_PATH.exists(), reason="requiere haber corrido mineru parse sobre el PDF de ejemplo")


@pytestmark_real
def test_real_document_pairs_at_least_90_percent_of_100_problems():
    md = REAL_MD_PATH.read_text(encoding="utf-8")
    parsed = parse_markdown(md)
    cards, warnings = pair_problems_and_solutions(parsed)

    numbers = {c["item_number"] for c in cards}
    assert numbers == set(range(1, 101)), "todos los problemas 1-100 deben aparecer como card, con o sin solución"

    paired = sum(1 for c in cards if c["problem"] is not None and c["solution"] is not None)
    # docs/DECISIONS.md: 92/100 con el parser determinista (sin LLM). Los 8 faltantes
    # son casos donde el número de ítem no aparece como texto plano al inicio de línea:
    # embebido dentro de una fórmula LaTeX (\tag{100.}, "2 7. A = B" dentro de un $$),
    # o -- la solución 1 -- directamente ausente en el documento original (la única
    # solución que no repite su número). No capturable sin una pasada con LLM.
    assert paired >= 90
