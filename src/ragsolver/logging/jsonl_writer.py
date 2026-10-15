"""Escritor de traza JSONL (RNF-5, docs/PLAN_RAG.md §7.6).

Un archivo por corrida (`run_id`), un evento por línea. `append_event` es la
única función que necesita el resto del código: no hay clase, no hay estado
oculto más allá del path del archivo — ponytail: no crear un logger genérico
para un único formato de evento.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ragsolver.rag.schema import TraceEvent


def trace_path(run_id: str, base_dir: str | Path = "data/eval/traces") -> Path:
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{run_id}.jsonl"


_LOCK = threading.Lock()


def append_event(event: TraceEvent, base_dir: str | Path = "data/eval/traces") -> Path:
    """Serializa `event` como una línea JSON y la agrega al archivo de la corrida."""
    path = trace_path(event.run_id, base_dir)
    with _LOCK, path.open("a", encoding="utf-8") as f:
        f.write(event.model_dump_json() + "\n")
    return path


def read_events(run_id: str, base_dir: str | Path = "data/eval/traces") -> list[TraceEvent]:
    path = trace_path(run_id, base_dir)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [TraceEvent.model_validate(json.loads(line)) for line in f if line.strip()]
