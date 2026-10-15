"""Fase 2 — lectura de la salida de MinerU 4 (esquema `docvortex.middle` 2.0).

MinerU deja un JSON por rango de páginas con bloques tipados (text / equation /
image / table / paragraph_title / ...) con bbox normalizado y contenido (LaTeX
para ecuaciones). Este módulo:

1. Carga esos bloques (`load_pages`).
2. Cruza la capa de texto del PDF contra los bbox de MinerU para encontrar
   contenido que el layout se saltó (`find_uncovered_regions`) — el PDF de
   ejemplo perdió una solución entera (pág. 64) y varias líneas sueltas.
   La capa de texto NO sirve para leer fórmulas (docs/PLAN_RAG.md §3), pero
   sí para saber *dónde hay texto*.
3. Regenera el Markdown con el mismo formato que `mineru-kit` (`render_markdown`),
   para poder insertar bloques sintéticos (transcripciones VLM de las regiones
   perdidas) y correcciones de LaTeX antes de parsear la estructura.

Bloques sintéticos: dicts con la misma forma (`type`, `bbox`, `content`) más
`synthetic: True`; `insert_block` los ubica por orden de lectura (y0).
"""

from __future__ import annotations

import glob
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# Numeral de ítem que MinerU absorbió dentro de una ecuación en display:
#   "2 7. A = B", "\begin{array}{l} 5 2. \text{...}", "... \tag{88.}"
# Sólo con el punto pegado al último dígito (un decimal espaciado sale "1 0 . 5").
_LEADING_NUM_RE = re.compile(r"^(?:\\begin\{array\}\{[a-z ]*\}\s*)?(\d)(?:\s?(\d))?(?:\s?(\d))?\.\s+")
_TAG_NUM_RE = re.compile(r"\\tag\s*\{(\d{1,3})\.\}\s*$")


def load_pages(mineru_dir: str | Path) -> dict[int, list[dict]]:
    """{página 1-based: [bloques en orden de lectura]} a partir de los *.json de MinerU."""
    pages: dict[int, list[dict]] = {}
    for f in sorted(glob.glob(str(Path(mineru_dir) / "*.json"))):
        for p in json.load(open(f, encoding="utf-8"))["pages"]:
            pages[p["page_idx"] + 1] = sorted(p["blocks"], key=lambda b: b["index"])
    return pages


def _item_bboxes(block: dict) -> list[list[float]]:
    boxes = [block["bbox"]]
    if isinstance(block.get("content"), list):
        boxes += [it["bbox"] for it in block["content"] if isinstance(it, dict) and "bbox" in it]
    return boxes


@dataclass
class UncoveredRegion:
    page: int  # 1-based
    bbox: tuple[float, float, float, float]  # puntos PDF
    words: str


def find_uncovered_regions(
    pdf_path: str | Path, pages: dict[int, list[dict]], min_words: int = 4, tol_pt: float = 4.0
) -> list[UncoveredRegion]:
    """Palabras de la capa de texto cuyo centro no cae en ningún bbox de MinerU.

    Una región por página (unión de las palabras sueltas); se ignora el pie de
    página. `min_words` filtra ruido (separadores "* * *", etc.).
    """
    doc = pymupdf.open(pdf_path)
    regions = []
    for pg in range(1, len(doc) + 1):
        page = doc[pg - 1]
        W, H = page.rect.width, page.rect.height
        boxes = [
            pymupdf.Rect(x0 * W - tol_pt, y0 * H - tol_pt, x1 * W + tol_pt, y1 * H + tol_pt)
            for b in pages.get(pg, [])
            for x0, y0, x1, y1 in _item_bboxes(b)
        ]
        loose = []
        for x0, y0, x1, y1, word, *_ in page.get_text("words"):
            if y0 > 0.92 * H:
                continue
            c = pymupdf.Point((x0 + x1) / 2, (y0 + y1) / 2)
            if not any(bx.contains(c) for bx in boxes):
                loose.append((pymupdf.Rect(x0, y0, x1, y1), word))
        if len(loose) >= min_words:
            r = pymupdf.Rect(loose[0][0])
            for rr, _ in loose[1:]:
                r |= rr
            regions.append(UncoveredRegion(pg, tuple(r), " ".join(w for _, w in loose)))
    return regions


def insert_block(pages: dict[int, list[dict]], page: int, block: dict) -> None:
    """Inserta un bloque sintético en `page` por orden de lectura (y0 del bbox)."""
    blocks = pages.setdefault(page, [])
    y0 = block["bbox"][1]
    pos = next((i for i, b in enumerate(blocks) if b["bbox"][1] > y0), len(blocks))
    blocks.insert(pos, {**block, "synthetic": True})


def text_block(text: str, bbox_norm: list[float]) -> dict:
    return {"type": "text", "index": -1, "bbox": list(bbox_norm), "content": [{"type": "text", "content": text}]}


# ---------------------------------------------------------------------------
# Render Markdown (mismo formato que `mineru-kit`, ver data/parsed/*/full.md)
# ---------------------------------------------------------------------------


def _inline(items: list[dict]) -> str:
    out = []
    for it in items:
        if it["type"] == "equation_inline":
            out.append(f"${it['content']}$")
        elif isinstance(it.get("content"), list):  # index: items anidados
            out.append(_inline(it["content"]))
        else:
            s = it.get("content", "")
            for style in it.get("styles", []):
                s = {"bold": f"**{s}**", "superscript": f"<sup>{s}</sup>", "subscript": f"<sub>{s}</sub>"}.get(style, s)
            out.append(s)
    return "".join(out)


def _table(html: str) -> str:
    rows = [re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S)]
    rows = [[c.strip() for c in r] for r in rows if r]
    if not rows:
        return html
    width = max(len(r) for r in rows)
    lines = ["| " + " | ".join(r + [""] * (width - len(r))) + " |" for r in rows]
    lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


def split_item_number(latex: str) -> tuple[int | None, str]:
    """Devuelve (número de ítem, latex sin el numeral) si la ecuación lo absorbió."""
    m = _LEADING_NUM_RE.match(latex)
    if m:
        n = int("".join(g for g in m.groups() if g))
        prefix = latex[: m.start(1)]  # conserva un \begin{array} si lo había
        return n, prefix + latex[m.end() :]
    m = _TAG_NUM_RE.search(latex)
    if m:
        return int(m.group(1)), latex[: m.start()].rstrip()
    return None, latex


def extract_item_numbers(pages: dict[int, list[dict]]) -> list[tuple[int, int]]:
    """Saca el numeral absorbido en ecuaciones a un bloque de texto propio ("N.")
    justo antes. Correr ANTES de la QA de fórmulas (el VLM lo borra como basura)."""
    found = []
    for pg, blocks in pages.items():
        i = 0
        while i < len(blocks):
            b = blocks[i]
            if b["type"] == "equation":
                n, latex = split_item_number(b["content"])
                if n is not None:
                    b["content"] = latex
                    blocks.insert(i, {**text_block(f"{n}.", b["bbox"]), "synthetic": True})
                    found.append((pg, n))
                    i += 1
            i += 1
    return found


def render_page(blocks: list[dict], page: int, n_pages: int, doc_short: str, tier: str) -> str:
    parts = [f"<!-- page {page} of {n_pages} -->"]
    for b in blocks:
        t = b["type"]
        if t == "page_number":
            continue
        if t in ("text",):
            parts.append(_inline(b["content"]))
        elif t in ("paragraph_title", "doc_title"):
            level = 1 if t == "doc_title" else b.get("level", 2)
            parts.append("#" * level + " " + _inline(b["content"]))
        elif t == "equation":
            parts.append(f"$$\n{b['content']}\n$$")
        elif t in ("image", "chart"):
            for it in b["content"]:
                if it["type"] in ("image_body", "chart_body"):
                    parts.append(f"![Image block](doc:{doc_short}/tier:{tier}/page:{page}/block:{it['index'] + 1})")
                else:  # image_caption / image_footnote
                    parts.append(_inline(it["content"]))
        elif t == "table":
            for it in b["content"]:
                parts.append(_table(it["content"]) if it["type"] == "table_body" else _inline(it["content"]))
        elif t == "index":
            parts.append("\n".join(f"- {_inline([it])}" for it in b["content"]))
        else:
            parts.append(_inline(b["content"]) if isinstance(b["content"], list) else str(b["content"]))
    return "\n\n".join(p for p in parts if p.strip())


def render_markdown(pages: dict[int, list[dict]], doc_short: str, tier: str = "standard") -> str:
    n = max(pages)
    return "\n\n".join(render_page(pages[pg], pg, n, doc_short, tier) for pg in sorted(pages)) + "\n"


def image_locators(pages: dict[int, list[dict]], doc_short: str, tier: str = "standard") -> dict[str, tuple[int, list[float]]]:
    """{locator markdown: (página, bbox normalizado)} para cada image_body/chart_body."""
    out = {}
    for pg, blocks in pages.items():
        for b in blocks:
            if b["type"] in ("image", "chart"):
                for it in b["content"]:
                    if it["type"] in ("image_body", "chart_body"):
                        out[f"doc:{doc_short}/tier:{tier}/page:{pg}/block:{it['index'] + 1}"] = (pg, it["bbox"])
    return out


# ---------------------------------------------------------------------------
# Chunking por secciones (docs/PLAN_RAG.md §6.2, paso 7: papers/apuntes -> `paper_section`)
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def section_chunks(md_text: str, doc_id: str, max_chars: int = 3000, min_chars: int = 200, stop_at: str | None = None) -> list[dict]:
    """Corta el Markdown en chunks `paper_section` de ~400-800 tokens (≈ `max_chars`) que
    respetan párrafos y títulos; `section_path` = títulos vigentes; `page_start/end` desde
    los marcadores `<!-- page N of T -->`. `stop_at`: título en el que se deja de cortar
    (p.ej. "Problemario", cuyo contenido va por el camino de problem cards)."""
    chunks: list[dict] = []
    path: list[str] = []
    page = 1
    buf: list[str] = []
    buf_pages: list[int] = []

    def flush():
        text = "\n\n".join(buf).strip()
        if len(text) >= min_chars:
            n = len(chunks)
            chunks.append({"chunk_id": f"informal:{doc_id}:paper_section:{n}", "doc_id": doc_id, "unit_type": "paper_section", "section_path": list(path), "page_start": min(buf_pages), "page_end": max(buf_pages), "content_md": text, "order": n})
        buf.clear()
        buf_pages.clear()

    for para in re.split(r"\n\s*\n", md_text):
        para = para.strip()
        if not para:
            continue
        pm = re.match(r"<!--\s*page (\d+) of \d+\s*-->", para)
        if pm:
            page = int(pm.group(1))
            continue
        hm = _HEADING_RE.match(para)
        if hm:
            title = hm.group(2).strip()
            if stop_at and title.lower().startswith(stop_at.lower()):
                break
            flush()
            level = len(hm.group(1))
            path = path[: level - 1] + [title]
            continue
        if re.fullmatch(r"\d{1,3}", para):  # número de página suelto
            continue
        if buf and sum(len(p) for p in buf) + len(para) > max_chars:
            flush()
        buf.append(para)
        buf_pages.append(page)
    flush()
    return chunks
