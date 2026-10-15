"""Fase 1 de docs/PLAN_CORRECCIONES.md — endurecimiento del verificador
(SEC-1, VER-1, BUG-2). Sin Lean: `find_forbidden_tactic`, la auditoría de
axiomas y que la fuente que se reporta sea la misma que se verifica.
El test con Lean real vive en test_verify_slow.py (marcador `slow`)."""

from __future__ import annotations

import pytest

from ragsolver.lean.verify import (
    STANDARD_AXIOMS,
    build_file,
    find_forbidden_tactic,
    non_standard_axioms,
    parse_axioms,
    theorem_block,
)

# -- SEC-1: validación de tácticas --------------------------------------------

DANGEROUS = [
    'simp\n  #eval IO.println "pwned"',  # comando inyectado tras una táctica legítima (el exploit real de la auditoría)
    "open Nat in #eval IO.println \"pwned\"",  # mismo bypass, pero disfrazado con `open ... in` (no anclado a línea)
    "decide +native",
    "exact Lean.ofReduceBool _ _ rfl",
    "native_decide",
    'run_tac (do IO.println "x")',
    "set_option maxHeartbeats 1 in simp",
    "sorry",
    "admit",
    "open Foo",  # `open` SIN `in`: no es una táctica válida por sí sola
    "have h := 1\nimport Mathlib.Tactic",
    "theorem evil : True := trivial",
    "#print axioms foo",
    "#check 1",
    "axiom evil : False",
    "attribute [instance] foo",
]


@pytest.mark.parametrize("tactics", DANGEROUS)
def test_find_forbidden_tactic_rejects_dangerous_patterns(tactics):
    assert find_forbidden_tactic(tactics) is not None


SAFE = [
    "simp",
    "nlinarith [sq_nonneg (a - b)]",
    "norm_num [Nat.Prime] at h",
    "decide",
    "omega",
    "interval_cases x <;> simp_all",
    "have h : 1 = 1 := by rfl\n  exact h",
    "-- esto menciona axiom y sorry, pero es un comentario\nsimp",
    "open Nat in exact zero_le n",  # `open X in`: alcance de nombres, no ejecuta código
    "ring_nf",
    "constructor <;> linarith",
]


@pytest.mark.parametrize("tactics", SAFE)
def test_find_forbidden_tactic_accepts_legitimate_tactics(tactics):
    assert find_forbidden_tactic(tactics) is None


# -- VER-1: auditoría de axiomas -----------------------------------------------


def test_parse_axioms_extracts_the_bracketed_list():
    assert parse_axioms("'foo' depends on axioms: [propext, Classical.choice, Quot.sound]") == ["propext", "Classical.choice", "Quot.sound"]
    assert parse_axioms("'foo' does not depend on any axioms") == []


def test_non_standard_axioms_flags_native_decide_and_sorry():
    assert non_standard_axioms(["propext"]) == []
    assert set(STANDARD_AXIOMS) == {"propext", "Classical.choice", "Quot.sound"}
    assert non_standard_axioms(["propext", "Lean.ofReduceBool", "sorryAx"]) == ["Lean.ofReduceBool", "sorryAx"]


# -- BUG-2: fuente única REPL <-> reporte -------------------------------------


def test_theorem_block_includes_header_opens_and_axiom_audit():
    code, name = theorem_block("theorem t1 (n : ℕ) : n + 0 = n := by sorry", "simp", header="open BigOperators Real")
    assert name == "t1"
    assert code.startswith("open BigOperators Real\n")
    assert "set_option maxHeartbeats 400000 in" in code
    assert "theorem t1 (n : ℕ) : n + 0 = n := by\n  simp" in code
    assert code.rstrip().endswith("#print axioms t1")


def test_theorem_block_ignores_import_lines_in_header():
    """El header de miniF2F trae sólo `open ...` (sin imports, docs/PLAN_RAG.md
    §7.1 `Problem.header`); si alguna vez trajera un `import`, no debe duplicar
    el de `build_file`."""
    code, _ = theorem_block("theorem t : True := by sorry", "trivial", header="import Mathlib\nopen Nat")
    assert "import Mathlib" not in code
    assert code.startswith("open Nat\n")


def test_theorem_block_renames_anonymous_example_deterministically():
    code, name = theorem_block("example : 1 + 1 = 2 := by sorry", "norm_num")
    assert name == "_ragsolver_goal"
    assert "theorem _ragsolver_goal : 1 + 1 = 2 := by\n  norm_num" in code


def test_theorem_block_rejects_unrecognized_statement_shape():
    with pytest.raises(ValueError):
        theorem_block("def foo := 1", "simp")


def test_build_file_lean_code_matches_what_the_repl_would_receive():
    """BUG-2: el `lean_code` reportado debe ser exactamente el bloque que
    verificaría el REPL (imports + el mismo `theorem_block`), incluidos los
    `open` del header."""
    block, _ = theorem_block("theorem t1 : True := by sorry", "trivial", header="open Nat")
    full = build_file("theorem t1 : True := by sorry", "trivial", ["Mathlib", "Aesop"], header="open Nat")
    assert full == "import Mathlib\nimport Aesop\n\n" + block + "\n"


# -- verify(): el rechazo de tácticas ocurre sin invocar Lean ------------------


def test_verify_rejects_forbidden_tactics_without_running_lean(monkeypatch):
    from ragsolver.lean import verify as V

    def boom(*a, **kw):
        raise AssertionError("no debería llegar a ejecutar Lean")

    monkeypatch.setattr(V, "run_lean", boom)
    v = V.verify("theorem t : True := by sorry", "native_decide", "t", ["Mathlib"])
    assert v.ok is False and "native_decide" in v.errors[0]["message"]


def test_verify_rejects_injected_command_without_running_lean(monkeypatch):
    from ragsolver.lean import verify as V

    def boom(*a, **kw):
        raise AssertionError("no debería llegar a ejecutar Lean")

    monkeypatch.setattr(V, "run_lean", boom)
    v = V.verify("theorem t : True := by sorry", 'simp\n#eval IO.println "pwned"', "t", ["Mathlib"])
    assert v.ok is False
