"""Fase 1, tareas 7-8 de docs/PLAN_CORRECCIONES.md — endurecimiento del
verificador probado contra Lean real (SEC-1, VER-1). Marcador `slow`: import
de Mathlib (~3-5 min la primera vez, ~0.3 s por chequeo después). Se salta si
no hay caché de Mathlib (`~/ragsolver_cache/lean_project` o `RAGSOLVER_LEAN_PROJECT`);
en CI no corre (`-m "not slow"`, docs/PLAN_CORRECCIONES.md Fase 6).

Uso: uv run pytest -m slow tests/test_verify_slow.py -q
"""

from __future__ import annotations

import pytest

from ragsolver.lean.verify import LEAN_PROJECT, LeanChecker, find_forbidden_tactic

pytestmark = [
    pytest.mark.slow,
    # mismo chequeo que ragsolver.lean.verify._lean_project(): el proyecto en sí no
    # se compila (`lake build`), sólo se restaura la caché de sus dependencias.
    pytest.mark.skipif(not (LEAN_PROJECT / ".lake" / "packages" / "mathlib" / ".lake" / "build").exists(), reason="requiere Lean/Mathlib compilados (ver docs/install.md)"),
]


@pytest.fixture(scope="module")
def checker():
    c = LeanChecker(imports=["Mathlib"])
    c.check("theorem _warmup : True := by sorry", "trivial")  # precalienta el import (minutos)
    yield c
    c.close()


def test_valid_proof_is_accepted_with_only_standard_axioms(checker):
    v = checker.check("theorem t_add_zero (n : ℕ) : n + 0 = n := by sorry", "simp")
    assert v.ok is True and v.errors == []
    assert set(v.axioms) <= {"propext", "Classical.choice", "Quot.sound"}


def test_injected_command_never_reaches_the_repl(checker):
    """SEC-1, regresión directa del exploit encontrado en la auditoría 2026-09-23:
    un `#eval` tras una táctica legítima se rechaza antes de tocar Lean."""
    assert find_forbidden_tactic('simp\n#eval IO.println "COMANDO INYECTADO"') is not None
    v = checker.check("theorem t_inj (n : ℕ) : n + 0 = n := by sorry", 'simp\n#eval IO.println "COMANDO INYECTADO"')
    assert v.ok is False


def test_native_decide_is_rejected_by_axiom_audit_if_the_denylist_were_bypassed(checker):
    """VER-1: aunque `find_forbidden_tactic` ya bloquea `decide +native` por
    nombre, esta prueba verifica la segunda capa (auditoría de axiomas) que la
    respalda: se comprueba directamente contra Lean pasando por alto la
    validación previa, para confirmar que el kernel de verdad exige el 'ok' de
    `_axiom_check`, no sólo la lista de rechazo."""
    from ragsolver.lean import verify as V

    code, name = V.theorem_block("theorem t_native : (2 : ℕ) ^ 10 = 1024 := by sorry", "decide +native")
    with checker._lock:
        if checker._server is None or not checker._server.is_alive():
            checker._start()
        from lean_interact import Command

        r = checker._server.run(Command(cmd=code, env=checker._env), timeout=checker.timeout_s)
    msgs = [{"line": 0, "col": 0, "severity": m.severity, "message": m.data} for m in r.messages]
    errors = [m for m in msgs if m["severity"] == "error"]
    axioms = V.parse_axioms("\n".join(m["message"] for m in msgs if m["severity"] == "info"))
    assert not errors  # el kernel SÍ acepta `decide +native` — por eso hace falta la auditoría de axiomas
    assert "Lean.ofReduceBool" in axioms or "Lean.trustCompiler" in axioms
    ok, _ = V._axiom_check(errors, False, axioms)
    assert ok is False  # `_axiom_check` rechaza igual, aunque el kernel haya aceptado la táctica


def test_check_identifiers_exist_confirms_a_real_and_a_fake_lemma(checker):
    found, total = checker.check_identifiers_exist(["Nat.add_zero", "Nat.this_lemma_does_not_exist_xyz"])
    assert total == 2 and found == 1


def test_goals_returns_one_goal_per_sorry_and_step_closes_them(checker):
    """docs/PLAN_MEJORA_RAG.md Fase 2: modo sonda (esqueleto con `sorry`) + `ProofStep` por goal."""
    stmt = "theorem t_skel (a b : ℝ) : 2 * a * b ≤ a ^ 2 + b ^ 2 := by sorry"
    skel = "have h1 : 0 ≤ (a - b) ^ 2 := by sorry\nhave h2 : (a - b) ^ 2 = a ^ 2 - 2 * a * b + b ^ 2 := by\n  sorry\nnlinarith [h1, h2]"
    goals, errors = checker.goals(stmt, skel, "open BigOperators Real Nat Topology Rat")
    assert errors == [] and len(goals) == 2
    assert goals[0]["goal"].endswith("⊢ 0 ≤ (a - b) ^ 2") and "h1 : 0 ≤ (a - b) ^ 2" in goals[1]["goal"]
    assert checker.step(goals[0]["proof_state"], "sorry")[0] is False  # validador antes de tocar Lean
    closed, _msg, infos = checker.step(goals[0]["proof_state"], "try?")
    assert closed and any("sq_nonneg" in i for i in infos)  # "Try this: [apply] exact sq_nonneg (a - b)"
    assert checker.step(goals[1]["proof_state"], "ring")[0] is True
    assert checker.step(goals[1]["proof_state"], "positivity")[0] is False
    assert checker.step(goals[1]["proof_state"], "ring", heartbeats=200000)[0] is True  # envuelto en `set_option … in`
    assert checker.step(goals[1]["proof_state"], "set_option maxHeartbeats 1 in ring")[0] is False  # el LLM no puede


def test_goals_rejects_forbidden_tactics_but_allows_sorry(checker):
    goals, errors = checker.goals("theorem t_skel2 (n : ℕ) : n + 0 = n := by sorry", "sorry\n#eval 1")
    assert goals == [] and "forbidden" in errors[0]["message"]


def test_signatures_resolves_core_lemmas_and_ambiguous_root_names(checker):
    sigs = checker.signatures(["Nat.mul_mod", "sq_nonneg", "eq_comm", "not_a_lemma_xyz"], header="open BigOperators Real Nat Topology Rat")
    assert set(sigs) == {"Nat.mul_mod", "sq_nonneg", "eq_comm"} and "%" in sigs["Nat.mul_mod"]


def test_stale_proof_state_is_rejected_after_a_restart(checker):
    goals, _ = checker.goals("theorem t_stale (n : ℕ) : n + 0 = n := by sorry", "sorry")
    with checker._lock:
        checker._server.kill()
        checker._server = None
    closed, msg, _ = checker.step(goals[0]["proof_state"], "simp", gen=goals[0]["gen"])
    assert closed is False and "stale proof state" in msg
    goals2, _ = checker.goals("theorem t_stale (n : ℕ) : n + 0 = n := by sorry", "sorry")  # reinicia el REPL (minutos)
    assert goals2[0]["gen"] == goals[0]["gen"] + 1 and checker.step(goals2[0]["proof_state"], "simp", gen=goals2[0]["gen"])[0] is True


def test_check_reports_the_constants_the_accepted_term_uses(checker):
    v = checker.check("theorem t_used (a b : ℝ) : 0 ≤ (a - b) ^ 2 := by sorry", "exact sq_nonneg (a - b)")
    assert v.ok and "sq_nonneg" in v.used_constants
