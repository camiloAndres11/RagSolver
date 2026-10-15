"""Fase 2, tarea 5 — parser estructural sobre el Markdown de MinerU.

Emparienta problema N (sección "Problemario") con su solución N (sección
"Soluciones"), y extrae los locators de imagen (`doc:.../block:N`) que caen
dentro de cada ítem. No usa LLM: es la ruta determinista que describe
docs/PLAN_RAG.md §6.2 ("casos ambiguos -> una llamada LLM", que no aplica acá
porque el documento tiene una estructura numerada regular).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PAGE_RE = re.compile(r"^<!--\s*page (\d+) of \d+\s*-->\s*$")
# "21. texto", "## 21. texto", o "13." solo (la solución sigue en la línea siguiente,
# frecuente cuando el ítem arranca directo con una fórmula en bloque).
_ITEM_RE = re.compile(r"^(?:## )?(\d{1,3})\.\s*(?:\S.*)?$")
_IMAGE_RE = re.compile(r"!\[Image block\]\((doc:[^)]+)\)")


@dataclass
class Item:
    number: int
    text_md: str
    page_start: int
    page_end: int
    image_locators: list[str] = field(default_factory=list)


def _split_section(lines: list[tuple[int, str]]) -> list[Item]:
    """`lines`: [(page_num, line_text), ...] ya recortado a una sección.

    Sólo se acepta un nuevo ítem si su número es >= el siguiente esperado
    (no-decreciente, no estrictamente secuencial: a veces el número de un
    ítem real queda atrapado dentro de una fórmula LaTeX -- p.ej. `\tag{100.}`
    -- y ese ítem nunca hace match, lo cual no debe romper el resto de la
    numeración). Rechazar los números MENORES al esperado es lo que evita que
    un sub-caso dentro de una explicación (p.ej. "1. Prolongar el segmento
    DC...", "2. Prolongar DA...") se confunda con el inicio de un ítem nuevo y
    pise al ítem real con ese número -- pasó de verdad con la solución 1 en el
    PDF de ejemplo (ver docs/DECISIONS.md).
    """
    items: list[Item] = []
    current: list[tuple[int, str]] | None = None
    current_number: int | None = None
    expected = 1

    # La sección "Soluciones" del PDF de ejemplo arranca con la solución 1 sin
    # repetir el numeral: si el primer ítem numerado es el 2, lo que hay antes
    # (salvo el título de sección) es el ítem 1.
    first = next((i for i, (_, t) in enumerate(lines) if _ITEM_RE.match(t)), None)
    if first is not None and int(_ITEM_RE.match(lines[first][1]).group(1)) == 2:
        current, current_number, expected = [(p, t) for p, t in lines[:first] if not t.startswith("## ")], 1, 2
        lines = lines[first:]

    def flush():
        nonlocal current, current_number
        if current_number is not None and current:
            text = "\n".join(t for _, t in current).strip()
            pages = [p for p, _ in current]
            images = _IMAGE_RE.findall(text)
            items.append(Item(current_number, text, min(pages), max(pages), images))
        current, current_number = [], None

    for page, line in lines:
        m = _ITEM_RE.match(line)
        number = int(m.group(1)) if m else None
        if number is not None and number >= expected:
            flush()
            current_number = number
            expected = number + 1
            current = [(page, line)]
        elif current is not None:
            current.append((page, line))
    flush()
    return items


def parse_markdown(md_text: str) -> dict:
    """Devuelve {"problems": [Item...], "solutions": [Item...], "warnings": [...]}."""
    lines_with_page: list[tuple[int, str]] = []
    page = 0
    for raw_line in md_text.splitlines():
        pm = _PAGE_RE.match(raw_line)
        if pm:
            page = int(pm.group(1))
            continue
        lines_with_page.append((page, raw_line))

    full_text = "\n".join(t for _, t in lines_with_page)
    idx_problemario = full_text.find("## Problemario")
    idx_soluciones = full_text.find("## Soluciones")
    idx_bibliografia = full_text.find("## Bibliografía")

    warnings: list[str] = []
    for name, idx in [("Problemario", idx_problemario), ("Soluciones", idx_soluciones), ("Bibliografía", idx_bibliografia)]:
        if idx == -1:
            warnings.append(f"sección '{name}' no encontrada")

    def _char_offset_to_line_index(char_offset: int) -> int:
        acc = 0
        for i, (_, t) in enumerate(lines_with_page):
            acc += len(t) + 1
            if acc > char_offset:
                return i
        return len(lines_with_page)

    i_problemario = _char_offset_to_line_index(idx_problemario) if idx_problemario >= 0 else 0
    i_soluciones = _char_offset_to_line_index(idx_soluciones) if idx_soluciones >= 0 else len(lines_with_page)
    i_bibliografia = _char_offset_to_line_index(idx_bibliografia) if idx_bibliografia >= 0 else len(lines_with_page)

    problems = _split_section(lines_with_page[i_problemario:i_soluciones])
    solutions = _split_section(lines_with_page[i_soluciones:i_bibliografia])

    return {"problems": problems, "solutions": solutions, "warnings": warnings}


def split_item(item: Item, n: int, first_line: str) -> tuple[Item, Item] | None:
    """Parte `item` en (item, nuevo ítem `n`) en la línea `first_line` (verbatim).

    Si esa línea está dentro de un bloque `$$`, el corte sube al `$$` de apertura.
    Devuelve None si la línea no aparece. Los locators de imagen se reparten según
    en qué mitad quedan.
    """
    lines = item.text_md.splitlines()
    first_line = first_line.strip()
    idx = next((i for i, l in enumerate(lines) if l.strip() == first_line), None)
    if idx is None:
        idx = next((i for i, l in enumerate(lines) if first_line and first_line in l), None)
    if idx is None or idx == 0:
        return None
    opens = [i for i, l in enumerate(lines[:idx]) if l.strip() == "$$"]
    if len(opens) % 2 == 1:  # dentro de un display: cortar en su apertura
        idx = opens[-1]
    head, tail = "\n".join(lines[:idx]).strip(), "\n".join(lines[idx:]).strip()
    if not tail.startswith(f"{n}."):
        tail = f"{n}.\n\n{tail}"
    return (
        Item(item.number, head, item.page_start, item.page_end, _IMAGE_RE.findall(head)),
        Item(n, tail, item.page_start, item.page_end, _IMAGE_RE.findall(tail)),
    )


def pair_problems_and_solutions(parsed: dict) -> tuple[list[dict], list[str]]:
    """Empareja por número. Devuelve (cards, warnings) — cards con problema+solución."""
    problems_by_n = {p.number: p for p in parsed["problems"]}
    solutions_by_n = {s.number: s for s in parsed["solutions"]}

    warnings = list(parsed["warnings"])
    all_numbers = sorted(set(problems_by_n) | set(solutions_by_n))
    cards = []
    for n in all_numbers:
        prob = problems_by_n.get(n)
        sol = solutions_by_n.get(n)
        if prob is None:
            warnings.append(f"solución {n} sin problema correspondiente")
        if sol is None:
            warnings.append(f"problema {n} sin solución correspondiente")
        cards.append(
            {
                "item_number": n,
                "problem": prob,
                "solution": sol,
                "image_locators": (prob.image_locators if prob else []) + (sol.image_locators if sol else []),
            }
        )
    return cards, warnings
