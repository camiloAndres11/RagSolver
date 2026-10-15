"""docs/PLAN_MEJORA_RAG.md — flujo por goal de punta a punta con Lean REAL y LLM simulado (0 llamadas de
pago): esqueleto -> goals del REPL -> CLOSE -> FILL con un bloque de varias líneas -> archivo final ->
kernel + auditoría de axiomas. Marcador `slow` (import de Mathlib).

Uso: uv run pytest -m slow tests/test_goal_mode_slow.py -q
"""

from __future__ import annotations

import pytest

from ragsolver.agents.agents import Agents, load_agents_config
from ragsolver.agents.schemas import Problem
from ragsolver.lean.verify import LEAN_PROJECT, LeanChecker, fill_sorries
from ragsolver.orchestrator import graph as G
from ragsolver.orchestrator.run import effective_config
from test_goal_mode import FakeLLM, FakeRetriever  # mismo directorio (pytest prepend)

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (LEAN_PROJECT / ".lake" / "packages" / "mathlib" / ".lake" / "build").exists(), reason="requiere Lean/Mathlib compilados (ver docs/install.md)"),
]

PROBLEM = Problem(problem_id="t_goal_e2e", informal_statement="2ab <= a^2 + b^2", formal_statement="theorem t_goal_e2e (a b : ℝ) (ha : 0 < a) (hb : 0 < b) : 2 * a * b ≤ a ^ 2 + b ^ 2 := by sorry", header="open BigOperators Real Nat Topology Rat")
SKELETON = "have h1 : 0 ≤ (a - b) ^ 2 := by sorry\nhave h2 : (a - b) ^ 2 = a ^ 2 - 2 * a * b + b ^ 2 := by\n  sorry\nnlinarith [h1, h2]"


@pytest.fixture(scope="module")
def checker():
    c = LeanChecker(imports=["Mathlib", "Aesop"])
    c.check("theorem _warmup : True := by sorry", "trivial")
    yield c
    c.close()


def test_multiline_block_assembled_by_fill_sorries_passes_the_kernel(checker):
    proof = fill_sorries(SKELETON, ["positivity", "have e := sub_sq a b\nnlinarith [e]"])
    v = checker.check(PROBLEM.formal_statement, proof, PROBLEM.header)
    assert v.ok, v.errors


def test_goal_mode_end_to_end_with_real_lean(checker, tmp_path):
    base = load_agents_config()
    cfg = effective_config(base, "full")
    llm = FakeLLM([SKELETON], fills=[[{"goal_id": "G1", "tactics": "have e := sub_sq a b\nnlinarith [e]"}]])
    orch = G.Orchestrator(Agents(llm, base, trace_dir=str(tmp_path / "traces")), FakeRetriever(), checker)
    rep = orch.solve(PROBLEM, cfg, "run:e2e")
    assert rep.status == "done", rep.error_types
    assert rep.verdict.ok and set(rep.verdict.axioms) <= {"propext", "Classical.choice", "Quot.sound"}
    assert "sorry" not in rep.lean_code and "?" not in rep.lean_code.split(":= by", 1)[1]
    assert rep.llm_calls <= 4  # plan + generate + skeleton (+ fill si la batería no cerró algún goal)
