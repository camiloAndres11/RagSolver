"""Fase 2 de docs/PLAN_CORRECCIONES.md — comprobación por contenido (SEC-2):
`scripts/check_no_leaked_content.py` y el saneado de `scripts/publish_hf.py`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_no_leaked_content import check, find_leaks, pdf_text_fingerprints  # noqa: E402
from publish_hf import _sanitize_run_dir  # noqa: E402


def _write_informal(tmp_path: Path, texts: list[str]) -> Path:
    p = tmp_path / "informal.jsonl"
    p.write_text("".join(json.dumps({"content_md": t}) + "\n" for t in texts), encoding="utf-8")
    return p


def test_pdf_text_fingerprints_takes_the_first_n_chars_of_each_row(tmp_path):
    p = _write_informal(tmp_path, ["A" * 100, "short", "B" * 60])
    fps = pdf_text_fingerprints(p, n_chars=60)
    assert fps == ["A" * 60, "B" * 60]  # "short" (<60) se descarta: no es una huella fiable


def test_find_leaks_flags_only_files_containing_a_fingerprint(tmp_path):
    leak = tmp_path / "leak.txt"
    leak.write_text("...prefijo... " + "X" * 60 + " ...sufijo...", encoding="utf-8")
    clean = tmp_path / "clean.txt"
    clean.write_text("nada que ver aquí", encoding="utf-8")
    leaks = find_leaks(["X" * 60], [leak, clean])
    assert set(leaks) == {str(leak)}


def test_check_end_to_end_on_a_directory_tree(tmp_path):
    informal = _write_informal(tmp_path, ["contenido protegido del pdf " * 3])
    fp = pdf_text_fingerprints(informal)[0]
    (tmp_path / "results.jsonl").write_text(json.dumps({"citations": [{"corpus": "informal", "snippet": fp}]}), encoding="utf-8")
    (tmp_path / "safe.md").write_text("no hay nada acá", encoding="utf-8")
    leaks = check([tmp_path / "results.jsonl", tmp_path / "safe.md"], informal)
    assert list(leaks) == [str(tmp_path / "results.jsonl")]


def test_sanitize_run_dir_blanks_informal_snippets_but_keeps_mathlib_ones(tmp_path):
    src = tmp_path / "eval-v1"
    src.mkdir()
    rows = [
        {"problem_id": "p1", "citations": [{"corpus": "informal", "snippet": "texto del pdf protegido"}, {"corpus": "mathlib", "snippet": "Nat.add_zero"}]},
        {"problem_id": "p2", "citations": []},
    ]
    (src / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (src / "traces").mkdir()
    (src / "traces" / "p1.jsonl").write_text('{"agent": "retriever"}\n', encoding="utf-8")

    dst = tmp_path / "sanitized"
    _sanitize_run_dir(src, dst)

    out_rows = [json.loads(l) for l in (dst / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert out_rows[0]["citations"][0]["snippet"] == ""  # informal: vaciado
    assert out_rows[0]["citations"][1]["snippet"] == "Nat.add_zero"  # mathlib: intacto
    assert (dst / "traces" / "p1.jsonl").exists()  # el resto de la corrida se copia igual
    # el original nunca se toca
    orig_rows = [json.loads(l) for l in (src / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert orig_rows[0]["citations"][0]["snippet"] == "texto del pdf protegido"
