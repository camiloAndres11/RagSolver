"""CLI `ragsolver` (docs/PLAN_RAG.md Fase 2 tarea 9, Fase 3 tarea 7).

  ragsolver search "..." [--corpus mathlib|informal] [--mode nl|formal|error|exemplar] [--k 8]
  ragsolver ingest --pdf <pdf> [--skip-llm] [--sections none|front|all]
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "search":
        from ragsolver.rag.retrieve import _cli

        _cli(rest)
    elif cmd == "ingest":
        import json

        from ragsolver.rag.ingest_informal import run

        args = dict(zip(rest[::2], rest[1::2]))
        rep = run(args.get("--pdf", "problemas resueltos olimpiadas.pdf"), args.get("--mineru-dir"), skip_llm="--skip-llm" in rest, sections=args.get("--sections", "none"))
        print(json.dumps({k: v for k, v in rep.items() if k not in ("gaps", "formula_diffs")}, indent=2, ensure_ascii=False))
    else:
        print(__doc__)
        sys.exit(0 if cmd in ("", "-h", "--help") else 2)


if __name__ == "__main__":
    main()
