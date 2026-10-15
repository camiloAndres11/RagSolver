"""Fase 2 — Corpus B: inventario, render y red de seguridad de figuras vectoriales.

docs/PLAN_RAG.md §6.2, Fase 2 tareas 1-4. El parseo estructural (MinerU +
emparejamiento problema/solución) y el enriquecimiento VLM (tareas 5-6) van en
módulos separados porque dependen de MinerU y de un proveedor de LLM/VLM
respectivamente — ver docs/PROGRESS.md.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# Heurística de fiabilidad de la capa de texto (docs/PLAN_RAG.md §3): líneas de
# 1-2 caracteres sueltas y tokens matemáticos aislados delatan una capa de texto
# que separó exponentes/subíndices de su base (p.ej. "2^2001" -> "22001").
_SHORT_LINE_RE = re.compile(r"^\s*.{1,2}\s*$")


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Inventory:
    doc_id: str
    sha256: str
    n_pages: int
    text_layer_reliable: bool
    short_line_ratio: float


def inventory(pdf_path: str | Path) -> Inventory:
    sha = sha256_file(pdf_path)
    doc = pymupdf.open(pdf_path)
    n_pages = len(doc)

    total_lines = short_lines = 0
    for page in doc:
        for line in page.get_text().splitlines():
            if not line.strip():
                continue
            total_lines += 1
            if _SHORT_LINE_RE.match(line):
                short_lines += 1
    ratio = short_lines / total_lines if total_lines else 0.0

    return Inventory(
        doc_id=sha[:12],
        sha256=sha,
        n_pages=n_pages,
        text_layer_reliable=ratio < 0.05,  # umbral conservador; documentos "limpios" están muy por debajo
        short_line_ratio=round(ratio, 4),
    )


def render_pages(pdf_path: str | Path, out_dir: str | Path, dpi: int = 200) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    paths = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi)
        p = out_dir / f"page_{i + 1:03d}.png"
        pix.save(p)
        paths.append(p)
    return paths


@dataclass
class VectorFigure:
    page: int  # 1-based
    bbox: tuple[float, float, float, float]
    n_paths: int


def detect_vector_figures(pdf_path: str | Path, min_size_pt: float = 15.0, padding: float = 8.0) -> list[VectorFigure]:
    """Red de seguridad de Fase 2, tarea 4: agrupa trazos vectoriales en figuras.

    No depende de MinerU — se usa para verificar que MinerU no se comió ningún
    diagrama de línea (docs/PLAN_RAG.md, riesgo "MinerU clasifica mal diagramas").

    Filtra por tamaño mínimo (ambas dimensiones), no por cantidad de trazos: se
    midió sobre el PDF de ejemplo (docs/DECISIONS.md) que diagramas geométricos
    reales pueden tener sólo 2 trazos (un círculo + una línea), mientras que
    reglas/subrayados decorativos con 1 solo trazo son anchos y muy bajos
    (~12-15pt de alto). `min_size_pt=15` separa eso; recalibrar si se agregan
    corpus con otro estilo de diagramación.
    """
    doc = pymupdf.open(pdf_path)
    figures: list[VectorFigure] = []
    for i, page in enumerate(doc):
        drawings = page.get_drawings()
        if not drawings:
            continue
        clusters = page.cluster_drawings(drawings=drawings)
        for rect in clusters:
            if min(rect.width, rect.height) < min_size_pt:
                continue
            padded = pymupdf.Rect(rect) + (-padding, -padding, padding, padding)
            n_paths = sum(1 for d in drawings if padded.intersects(d["rect"]))
            figures.append(VectorFigure(page=i + 1, bbox=tuple(padded), n_paths=n_paths))
    return figures
