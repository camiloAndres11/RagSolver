"""Regenera docs/interfaces/*.schema.json a partir de src/ragsolver/rag/schema.py.

Uso: python scripts/export_schemas.py
Correr después de cualquier cambio a rag/schema.py (RNF-4: los contratos
JSON Schema en docs/ deben reflejar siempre el código, no al revés).
"""

from __future__ import annotations

import json
from pathlib import Path

from ragsolver.agents import schemas as a
from ragsolver.rag import schema as s

MODELS = [
    s.Document,
    s.ChunkBase,
    s.InformalChunk,
    s.MathlibChunk,
    s.Figure,
    s.Citation,
    s.TraceEvent,
    s.RetrievalRequest,
    s.RetrievalResult,
    # Fase 4 — contratos de los agentes (RNF-4)
    a.Problem,
    a.PlannerOutput,
    a.GeneratorOutput,
    a.FormalizerOutput,
    a.CriticOutput,
    a.BaselineAOutput,
    a.BaselineBOutput,
    a.Verdict,
    a.SolutionReport,
]

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "interfaces"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        out_path = OUT_DIR / f"{model.__name__}.schema.json"
        out_path.write_text(json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {out_path.relative_to(OUT_DIR.parent.parent)}")


if __name__ == "__main__":
    main()
