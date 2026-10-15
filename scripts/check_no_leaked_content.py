"""Fase 2 del plan de corrección — comprobación por contenido (SEC-2): ningún
fragmento del corpus informal (texto de un documento con licencia desconocida,
`Document.redistributable=false`) debe aparecer en lo que se publica, ni en
git (docs/PLAN_CORRECCIONES.md Fase 2, tarea 2) ni en la copia que sube
`scripts/publish_hf.py` (tarea 3) — misma función, dos llamadores.

Uso: python scripts/check_no_leaked_content.py [--root .] [--informal data/corpus/informal.jsonl]
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def pdf_text_fingerprints(informal_jsonl: str | Path, n_chars: int = 60) -> list[str]:
    """Primeros `n_chars` del `content_md` de cada card/figura del corpus
    informal: huellas cortas y específicas — alcanzan para detectar una fuga
    sin que el propio script de comprobación termine guardando el contenido
    protegido."""
    path = Path(informal_jsonl)
    if not path.exists():
        return []
    prints = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        text = (json.loads(line).get("content_md") or "").strip()
        if len(text) >= n_chars:
            prints.append(text[:n_chars])
    return prints


def find_leaks(fingerprints: list[str], files: list[Path]) -> dict[str, list[str]]:
    """{archivo: [huellas encontradas]} sólo para los archivos con alguna coincidencia."""
    leaks: dict[str, list[str]] = {}
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hits = [fp for fp in fingerprints if fp in text]
        if hits:
            leaks[str(f)] = hits
    return leaks


def git_committable_files(root: str | Path = ".") -> list[Path]:
    """Lo mismo que incluiría `git add .`: trackeados + no ignorados (respeta
    `.gitignore`); usado por la tarea 2 (comprobación antes de un commit)."""
    root = Path(root)
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"], capture_output=True, text=True, check=True)
    return [root / p for p in out.stdout.splitlines() if p.strip() and (root / p).is_file()]


def check(files: list[Path], informal_jsonl: str | Path = "data/corpus/informal.jsonl") -> dict[str, list[str]]:
    """Punto de entrada compartido: huellas del corpus informal contra `files`."""
    return find_leaks(pdf_text_fingerprints(informal_jsonl), files)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".")
    ap.add_argument("--informal", default="data/corpus/informal.jsonl")
    args = ap.parse_args()

    files = git_committable_files(args.root)
    leaks = check(files, args.informal)
    if leaks:
        print(f"FUGA: {len(leaks)} archivo(s) contienen texto del corpus informal:")
        for f, hits in leaks.items():
            print(f"  {f}: {len(hits)} coincidencia(s)")
        raise SystemExit(1)
    n_fp = len(pdf_text_fingerprints(args.informal))
    print(f"OK: 0 coincidencias en {len(files)} archivos ({n_fp} huellas comprobadas)")
