"""Fase 2, tareas 3-4 — recortes de figuras a partir de los bloques `image` de
MinerU, unidos a la red de seguridad de clusters vectoriales (docs/PLAN_RAG.md §6.2).

Un recorte por bloque imagen, con padding para que entren las etiquetas de los
puntos (A, B, O...) que el PDF deja como texto suelto alrededor del dibujo.
`figure_id = {doc_id}:fig:{page}:{block}` coincide con el locator markdown
`.../page:{page}/block:{block}` que ya usa parse_structure, así el enlace
figura <-> problem card no necesita nada más.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf

from ragsolver.rag.ingest_pdf import detect_vector_figures
from ragsolver.rag.mineru_blocks import _item_bboxes
from ragsolver.rag.schema import Figure


def _covered(rect: pymupdf.Rect, boxes: list[pymupdf.Rect]) -> float:
    """Fracción de `rect` cubierta por la unión (aproximada por suma) de `boxes`."""
    if rect.is_empty or rect.get_area() == 0:
        return 0.0
    area = sum((rect & b).get_area() for b in boxes if not (rect & b).is_empty)
    return min(1.0, area / rect.get_area())


def _crop(page: pymupdf.Page, rect: pymupdf.Rect, out: Path, dpi: int) -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    page.get_pixmap(dpi=dpi, clip=rect & page.rect).save(out)
    return hashlib.sha256(out.read_bytes()).hexdigest()[:16]


def crop_figures(
    pdf_path: str | Path,
    pages: dict[int, list[dict]],
    out_dir: str | Path,
    doc_id: str,
    pad_pt: float = 10.0,
    dpi: int = 200,
    min_overlap: float = 0.6,
) -> tuple[list[Figure], list[str]]:
    """Devuelve (figuras, avisos). Avisos = clusters vectoriales que ningún bloque de
    MinerU cubre (>= min_overlap): se recortan igual con detected_by=vector_cluster."""
    doc = pymupdf.open(pdf_path)
    out_dir = Path(out_dir)
    vectors: dict[int, list[pymupdf.Rect]] = {}
    for v in detect_vector_figures(pdf_path):
        vectors.setdefault(v.page, []).append(pymupdf.Rect(*v.bbox))

    figures: list[Figure] = []
    warnings: list[str] = []
    for pg in sorted(pages):
        page = doc[pg - 1]
        W, H = page.rect.width, page.rect.height
        to_pt = lambda bb: pymupdf.Rect(bb[0] * W, bb[1] * H, bb[2] * W, bb[3] * H)
        all_boxes = [to_pt(bb) for b in pages[pg] for bb in _item_bboxes(b)]
        image_boxes = []
        for b in pages[pg]:
            if b["type"] not in ("image", "chart"):
                continue
            caption = " ".join(
                it_c.get("content", "")
                for it in b["content"] if it["type"] in ("image_caption", "image_footnote")
                for it_c in it["content"]
            ).strip()
            for it in b["content"]:
                if it["type"] not in ("image_body", "chart_body"):
                    continue
                rect = to_pt(it["bbox"])
                image_boxes.append(rect)
                padded = rect + (-pad_pt, -pad_pt, pad_pt, pad_pt)
                block = it["index"] + 1
                png = out_dir / f"p{pg:03d}_b{block}.png"
                sha = _crop(page, padded, png, dpi)
                has_vector = any(_covered(v, [rect]) >= min_overlap for v in vectors.get(pg, []))
                figures.append(
                    Figure(
                        figure_id=f"{doc_id}:fig:{pg}:{block}",
                        image_path=str(png),
                        image_sha=sha,
                        page=pg,
                        bbox=[round(x, 4) for x in it["bbox"]],
                        detected_by="both" if has_vector else "layout",
                        caption=caption,
                    )
                )
        # Red de seguridad: dibujo vectorial que MinerU no cubrió con ningún bloque
        # (ni imagen, ni ecuación/texto — los radicales del Equation Editor caen en
        # bloques equation/text y no cuentan como figura perdida).
        for k, v in enumerate(vectors.get(pg, []), start=1):
            if _covered(v, all_boxes) >= min_overlap:
                continue
            png = out_dir / f"p{pg:03d}_v{k}.png"
            sha = _crop(page, v, png, dpi)
            figures.append(
                Figure(
                    figure_id=f"{doc_id}:fig:{pg}:v{k}",
                    image_path=str(png),
                    image_sha=sha,
                    page=pg,
                    bbox=[round(v.x0 / W, 4), round(v.y0 / H, 4), round(v.x1 / W, 4), round(v.y1 / H, 4)],
                    detected_by="vector_cluster",
                )
            )
            warnings.append(f"página {pg}: cluster vectorial {tuple(round(x) for x in v)} sin bloque de MinerU -> {png.name}")
    return figures, warnings
