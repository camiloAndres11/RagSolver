"""docs/PLAN_MEJORA_RAG.md Fases 2-4 y 2b — flujo por goal con REPL y LLM simulados:
esqueleto -> goals -> CLOSE (premisas por goal) -> FILL -> VERIFY; registro de goals probados entre
intentos; replan con memoria (DES-1); línea base `baseline_auto`; piezas puras (fill_sorries, prior)."""

from __future__ import annotations

import json
import re

import pytest

from ragsolver.agents.agents import Agents, load_agents_config
from ragsolver.agents.schemas import Problem, Verdict
from ragsolver.lean.verify import count_sorries, fill_sorries, skeletonize
from ragsolver.llm import LLMResponse
from ragsolver.orchestrator import graph as G
from ragsolver.orchestrator.run import effective_config, run_auto
from ragsolver.rag import premises as P
from ragsolver.rag.schema import RetrievalResult

PROBLEM = Problem(problem_id="g1", informal_statement="2ab <= a^2 + b^2", formal_statement="theorem g1 (a b : ℝ) : 2 * a * b ≤ a ^ 2 + b ^ 2 := by sorry")
HAVE_RE = re.compile(r"have\s+\S+\s*:\s*(.+?)\s*:=\s*by\s+sorry")


class FakeChecker:
    """`goals`: un goal por `sorry` ("a b : ℝ\\n⊢ <afirmación del have>" o "⊢ main"); `step`: cierra si la
    táctica está en `closes[afirmación]`; `check`: ok si la prueba no tiene `done`/`bad`."""

    def __init__(self, closes: dict[str, set[str]]):
        self.closes, self.steps, self.goal_calls = closes, [], 0

    def goals(self, stmt, skeleton, header="", hb=400000):
        self.goal_calls += 1
        out = []
        for line in skeleton.splitlines():
            for _ in re.findall(r"\bsorry\b", line):
                m = HAVE_RE.search(line)
                out.append({"goal": f"a b : ℝ\n⊢ {m.group(1) if m else 'main'}", "proof_state": len(out)})
        return out, []

    def step(self, ps, tactic, timeout=60, heartbeats=None, gen=None):
        goal = self._last_goals[ps] if hasattr(self, "_last_goals") else None
        self.steps.append(tactic)
        target = goal
        ok = any(tactic.startswith(t) for t in self.closes.get(target, set()))
        return ok, "" if ok else "linarith failed", ["Try this:\n  [apply] exact sq_nonneg (a - b)"] if tactic == "try?" and ok else []

    def check(self, stmt, tactics, header="", hb=400000):
        bad = "done" in tactics or "bad" in tactics or "sorry" in tactics
        return Verdict(ok=not bad, errors=[{"line": 1, "col": 0, "message": "unsolved goals"}] if bad else [])


def _wire_goal_targets(checker: FakeChecker):
    orig = checker.goals

    def goals(*a, **k):
        gs, errs = orig(*a, **k)
        checker._last_goals = {g["proof_state"]: g["goal"].rsplit("⊢", 1)[-1].strip() for g in gs}
        return gs, errs

    checker.goals = goals
    return checker


class FakeRetriever:
    def __init__(self):
        self.requests = []

    def search(self, req):
        self.requests.append(req)
        return RetrievalResult(hits=[])


class FakeLLM:
    def __init__(self, skeletons, fills=(), critic=None):
        self.skeletons, self.fills, self.critic = list(skeletons), list(fills), critic or {}
        self.prompts: list[str] = []
        self.n_skel = self.n_fill = 0
        self.exhausted, self.models, self.tokens_in, self.tokens_out = [], ["fake"], 0, 0

    def generate(self, prompt, schema=None, images=(), system=None, max_tokens=None):
        self.prompts.append(prompt)
        if "Decompose the problem" in prompt:
            out = {"strategy": "AM-GM", "subgoals": [{"subgoal_id": "s1", "statement_nl": "square nonneg", "search_queries": ["square nonneg"]}]}
        elif "proof sketch in natural language" in prompt:
            out = {"sketch": "(a-b)^2 >= 0.", "used_citations": []}
        elif "proof SKELETON" in prompt:
            out = {"proof_tactics": self.skeletons[min(self.n_skel, len(self.skeletons) - 1)], "notes": ""}
            self.n_skel += 1
        elif "Prove each open Lean 4 goal" in prompt:
            out = {"proofs": self.fills[min(self.n_fill, len(self.fills) - 1)] if self.fills else []}
            self.n_fill += 1
        elif "rejected the proof" in prompt:
            out = {"error_type": "lean_syntax", "action": "retry", "missing_identifiers": [], "feedback": "fix h2", **self.critic}
        else:
            raise AssertionError(prompt[:80])
        return LLMResponse(json.dumps(out), "fake", 10, 5)


SKELETON = "have h1 : 0 ≤ (a - b) ^ 2 := by sorry\nhave h2 : (a - b) ^ 2 = a ^ 2 - 2 * a * b + b ^ 2 := by sorry\nnlinarith [h1, h2]"


@pytest.fixture
def make(tmp_path, monkeypatch):
    base = load_agents_config()
    monkeypatch.setattr(P, "load_prior", lambda path=P.PRIOR_PATH: ({"decl_name": "sq_nonneg", "freq": 100, "type": "∀ a, 0 ≤ a ^ 2", "_salient": frozenset()},))

    def _make(llm, closes, config="full", **over):
        closes.setdefault("main", {"nlinarith [h1, h2]"})  # el cierre del esqueleto: su propio bloque lo cierra
        checker = _wire_goal_targets(FakeChecker(closes))
        cfg = effective_config(base, config)
        cfg.update(over)
        orch = G.Orchestrator(Agents(llm, base, trace_dir=str(tmp_path / "traces")), FakeRetriever(), checker)
        return orch, cfg, checker

    return _make


def test_close_proves_every_goal_without_fill_and_cites_the_premise(make):
    llm = FakeLLM([SKELETON])
    closes = {"0 ≤ (a - b) ^ 2": {"grind +lax [sq_nonneg"}, "(a - b) ^ 2 = a ^ 2 - 2 * a * b + b ^ 2": {"try?"}}
    orch, cfg, checker = make(llm, closes)
    rep = orch.solve(PROBLEM, cfg, "run:g1")
    assert rep.status == "done" and rep.llm_calls == 3  # plan + generate + skeleton; CLOSE y VERIFY: 0 LLM
    assert "grind +lax [sq_nonneg]" in rep.lean_code and "exact sq_nonneg (a - b)" in rep.lean_code and "try?" not in rep.lean_code
    assert rep.n_used_in_proof == 1 and rep.citations[0].locator["decl_name"] == "sq_nonneg"
    assert not any("Prove each open" in p for p in llm.prompts)


def test_fill_only_sees_open_goals_and_ledger_reuses_proved_ones_on_retry(make):
    fills = [[{"goal_id": "G1", "tactics": "bad"}], [{"goal_id": "G1", "tactics": "ring"}]]
    llm = FakeLLM([SKELETON, SKELETON], fills=fills)  # el reintento conserva la afirmación de h1
    closes = {"0 ≤ (a - b) ^ 2": {"try?", "exact sq_nonneg"}, "(a - b) ^ 2 = a ^ 2 - 2 * a * b + b ^ 2": {"ring"}}
    orch, cfg, checker = make(llm, closes)
    rep = orch.solve(PROBLEM, cfg, "run:g1b")
    assert rep.status == "done" and rep.attempts == 2
    fill_prompts = [p for p in llm.prompts if "Prove each open" in p]
    assert len(fill_prompts) == 2 and all("⊢ 0 ≤ (a - b) ^ 2" not in p for p in fill_prompts)  # h1 nunca llega a FILL
    skel_prompts = [p for p in llm.prompts if "proof SKELETON" in p]
    assert "0 ≤ (a - b) ^ 2 := by exact sq_nonneg (a - b)" in skel_prompts[1]  # el registro de probados llega al reintento
    # intento 1: h1 recorre la batería hasta `try?`; intento 2: un solo paso con el bloque ya probado
    assert checker.steps.count("try?") == 3 and checker.steps.count("exact sq_nonneg (a - b)") == 1  # try?: h1 + h2 (×2 intentos)


def test_no_rag_runs_the_plain_battery_without_premises(make):
    llm = FakeLLM([SKELETON])
    closes = {"0 ≤ (a - b) ^ 2": {"nlinarith"}, "(a - b) ^ 2 = a ^ 2 - 2 * a * b + b ^ 2": {"grind"}}
    orch, cfg, checker = make(llm, closes, config="no_rag")
    rep = orch.solve(PROBLEM, cfg, "run:g1c")
    assert rep.status == "done" and rep.n_retrieved == 0
    assert not any(x in s for s in checker.steps for x in ("+lax", "+suggestions", "sq_nonneg"))  # ni premisas, ni hints, ni canal B


def test_skeleton_with_wrong_sorry_count_goes_to_the_critic(make):
    llm = FakeLLM(["rcases h with ⟨x, hx⟩ <;> sorry\n" + SKELETON, SKELETON], critic={"action": "retry"})
    closes = {"0 ≤ (a - b) ^ 2": {"grind"}, "(a - b) ^ 2 = a ^ 2 - 2 * a * b + b ^ 2": {"grind"}}
    orch, cfg, checker = make(llm, closes)
    checker.goals = (lambda orig: (lambda *a, **k: ([{"goal": "⊢ x", "proof_state": 0}, {"goal": "⊢ y", "proof_state": 1}], []) if "<;>" in a[1] else orig(*a, **k)))(checker.goals)
    rep = orch.solve(PROBLEM, cfg, "run:g1d")
    assert rep.status == "done" and rep.attempts == 2
    critic = [p for p in llm.prompts if "rejected the proof" in p][0]
    assert "exactly one `sorry` per goal" in critic


def test_replan_carries_critic_feedback_and_proved_claims(make):
    fills = [[{"goal_id": "G1", "tactics": "bad"}]]
    llm = FakeLLM([SKELETON], fills=fills, critic={"action": "replan", "feedback": "h2 is wrong, expand instead"})
    closes = {"0 ≤ (a - b) ^ 2": {"grind +lax"}}
    orch, cfg, checker = make(llm, closes, k_max=2)
    orch.solve(PROBLEM, cfg, "run:g1e")
    plans = [p for p in llm.prompts if "Decompose the problem" in p]
    assert len(plans) == 2
    assert "(none)" in plans[0].split("## Replan feedback")[1]
    second = plans[1].split("## Replan feedback")[1]
    assert "h2 is wrong, expand instead" in second and "Previous strategy: AM-GM" in second and "0 ≤ (a - b) ^ 2" in second


def test_baseline_auto_uses_zero_llm_calls(make):
    base = load_agents_config()
    cfg = effective_config(base, "baseline_auto")
    checker = _wire_goal_targets(FakeChecker({"main": {"try?"}}))
    rep = run_auto(PROBLEM, cfg, checker)
    assert rep.status == "done" and rep.llm_calls == 0 and "exact sq_nonneg (a - b)" in rep.lean_code
    assert checker.steps[0] == "grind"  # batería sin premisas


def test_fill_sorries_single_multi_and_inline():
    sk = "have h1 : A := by sorry\nhave h2 : B := by\n  sorry\nexact foo (by sorry) h1"
    out = fill_sorries(sk, ["simp", "nlinarith [x]\nlinarith", "ring_nf\nsimp"])
    assert count_sorries(sk) == 3 and "sorry" not in out
    assert "have h1 : A := by simp" in out
    assert "have h2 : B := by\n\n    nlinarith [x]\n    linarith" in out
    assert "exact foo (by (ring_nf; simp)) h1" in out
    assert fill_sorries("have h : A := by sorry\nsimp", ["a\nb"]) == "have h : A := by\n  a\n  b\nsimp"


def test_prior_channel_filters_by_goal_symbols_and_hints_use_goal_variables():
    prior = tuple({"decl_name": n, "freq": f, "type": t, "_salient": frozenset(P.salient(t))} for n, f, t in [
        ("sq_nonneg", 100, "∀ {α : Type u_1} [inst : LinearOrderedRing α] (a : α), 0 ≤ a ^ 2"),
        ("Real.sqrt_nonneg", 90, "∀ (x : ℝ), 0 ≤ √x"),
        ("Nat.mul_mod", 80, "∀ (a b n : ℕ), a * b % n = a % n * (b % n) % n"),
    ])
    assert P.prior_channel("a b : ℝ\n⊢ 2 * a * b ≤ a ^ 2 + b ^ 2", prior=prior) == ["sq_nonneg"]
    assert P.prior_channel("x : ℝ\nh : 0 ≤ x\n⊢ √x * √x = x", prior=prior) == ["sq_nonneg", "Real.sqrt_nonneg"]
    assert P.prior_channel("n : ℕ\n⊢ n * n % 4 < 4", prior=prior) == ["sq_nonneg", "Nat.mul_mod"]
    assert P.prior_channel("n : ℕ\n⊢ n * n % 4 < 4", prior=prior, filtered=False) == ["sq_nonneg", "Real.sqrt_nonneg", "Nat.mul_mod"]
    hints = P.nlinarith_hints("a b : ℝ\nha : 0 < a\nhb : 0 < b\n⊢ 2 * a * b ≤ a ^ 2 + b ^ 2")
    assert "sq_nonneg (a - b)" in hints and "mul_pos ha hb" in hints


def test_premise_helpers():
    assert P.strip_field("sub_ne_zero.mpr") == "sub_ne_zero" and P.strip_field("Nat.Prime.two_le") == "Nat.Prime.two_le"
    assert P.is_lemma_type("∀ (a b n : ℕ), a * b % n = a % n * (b % n) % n") and not P.is_lemma_type("ℝ → ℝ") and not P.is_lemma_type("ℕ → ℕ → ℕ → Prop")
    assert P.proof_tokens("theorem t : x := by\n  nlinarith [sq_nonneg (a - b), h₀, Nat.mul_mod 2 3 4, sub_nonneg.mpr h]") >= {"sq_nonneg", "Nat.mul_mod", "sub_nonneg"}
    assert not P.is_premise_constant("Mathlib.Tactic.Ring.add_pf_add_lt") and not P.is_premise_constant("Eq.mpr") and P.is_premise_constant("sq_nonneg")
    assert G.render_tactic({"rag": "grind +lax [{premises}]", "plain": "grind"}, True, [], []) == "grind"
    assert G.render_tactic({"rag": "grind +suggestions", "plain": None}, False, ["x"], []) is None
    assert G.concrete_block("try?", ["Try these:\n  [apply] grind\n  [apply] grind only"]) == "grind"


def test_close_goal_stops_on_a_restarted_repl():
    class Restarted:
        calls = 0

        def step(self, ps, tactic, timeout=60, heartbeats=None, gen=None):
            self.calls += 1
            return False, "stale proof state (lean repl restarted)", []

    ck = Restarted()
    block, log = G.close_goal(ck, {"goal": "⊢ x", "proof_state": 3, "gen": 1}, load_agents_config()["close"]["tactics"], False, [], [])
    assert block is None and ck.calls == 1  # no sigue mandando tácticas a un estado que ya no existe


def test_full_proof_without_sorry_is_split_into_goals_and_only_the_bad_step_is_repaired(make):
    """Smoke 2026-10-14: el modelo escribe la prueba entera en vez del esqueleto. Cada `have` es un goal
    con el bloque del modelo como primer candidato; sólo el paso que Lean rechaza pasa por la batería."""
    proof = "have h1 : 0 ≤ (a - b) ^ 2 := by\n  bad_tactic\nhave h2 : (a - b) ^ 2 = a ^ 2 - 2 * a * b + b ^ 2 := by ring\nnlinarith [h1, h2]"
    llm = FakeLLM([proof])
    closes = {"0 ≤ (a - b) ^ 2": {"grind +lax"}, "(a - b) ^ 2 = a ^ 2 - 2 * a * b + b ^ 2": {"ring"}}
    orch, cfg, checker = make(llm, closes)
    rep = orch.solve(PROBLEM, cfg, "run:g1f")
    assert rep.status == "done" and rep.llm_calls == 3 and rep.attempts == 1
    assert "bad_tactic" not in rep.lean_code and "grind +lax [sq_nonneg]" in rep.lean_code and ":= by ring" in rep.lean_code
    assert checker.steps[:2] == ["bad_tactic", "grind +lax [sq_nonneg]"]  # primero el bloque del modelo, después la batería
    assert rep.n_used_in_proof == 1


def test_skeletonize_shapes():
    sk, c = skeletonize("rcases h with ⟨k, hk⟩\nhave h1 : A := by\n  simp\n  ring\nhave h2 : B := by norm_num\nhave h3 : C := foo\nexact ⟨h1, h2⟩")
    assert sk == "rcases h with ⟨k, hk⟩\nhave h1 : A := by sorry\nhave h2 : B := by sorry\nsorry"  # lo que sigue al último `have … := by` es el cierre
    assert c == ["simp\nring", "norm_num", "have h3 : C := foo\nexact ⟨h1, h2⟩"]
    assert skeletonize("rfl") == ("sorry", ["rfl"])
    assert skeletonize("have h : A := by sorry\nexact h") == ("have h : A := by sorry\nsorry", [None, "exact h"])
    sk, c = skeletonize("induction n with\n| zero => simp\n| succ n ih => omega")
    assert sk == "sorry" and c == ["induction n with\n| zero => simp\n| succ n ih => omega"]
    sk, c = skeletonize("have h : A := by sorry\nexact foo (by sorry) h")
    assert count_sorries(sk) == len(c) == 2 and c == [None, "exact foo (by sorry) h"]


def test_used_in_proof_counts_only_constants_the_kernel_term_uses(make, monkeypatch):
    """`grind +lax [a, b, …]` nombra todas las premisas pero usa pocas: con `Verdict.used_constants`
    (REPL `all_tactics`) sólo cuentan las que están en el término aceptado (DECISIONS #101)."""
    monkeypatch.setattr(P, "load_prior", lambda path=P.PRIOR_PATH: tuple({"decl_name": n, "freq": 10, "type": "∀ a, 0 ≤ a ^ 2", "_salient": frozenset()} for n in ("sq_nonneg", "mul_pos")))
    llm = FakeLLM([SKELETON])
    closes = {"0 ≤ (a - b) ^ 2": {"grind +lax"}, "(a - b) ^ 2 = a ^ 2 - 2 * a * b + b ^ 2": {"grind +lax"}}
    orch, cfg, checker = make(llm, closes)
    real_check = checker.check
    checker.check = lambda *a, **k: real_check(*a, **k).model_copy(update={"used_constants": ["sq_nonneg", "HPow.hPow"]})
    rep = orch.solve(PROBLEM, cfg, "run:g1u")
    assert rep.status == "done" and "grind +lax [sq_nonneg, mul_pos]" in rep.lean_code
    used = {c.locator["decl_name"] for c in rep.citations if c.used_in_proof}
    assert used == {"sq_nonneg"}  # `mul_pos` está en el texto de la prueba pero no en el término


def test_unreadable_critic_output_retries_instead_of_failing_the_problem(make):
    """#110: JSON inválido del Crítico (2 llamadas, contadas) -> reintento con los errores de Lean como
    feedback; antes el problema terminaba como `llm_output_invalid:critic` (6 de 43 fallos de eval-v2)."""

    class BadCritic(FakeLLM):
        def generate(self, prompt, schema=None, images=(), system=None, max_tokens=None):
            if "rejected the proof" in prompt:
                self.prompts.append(prompt)
                return LLMResponse("{not json", "fake", 10, 5)
            return super().generate(prompt, schema, images, system, max_tokens)

    llm = BadCritic([SKELETON, SKELETON], fills=[[{"goal_id": "G1", "tactics": "bad"}], [{"goal_id": "G1", "tactics": "ring"}]])
    closes = {"0 ≤ (a - b) ^ 2": {"grind +lax"}, "(a - b) ^ 2 = a ^ 2 - 2 * a * b + b ^ 2": {"ring"}}
    orch, cfg, checker = make(llm, closes)
    rep = orch.solve(PROBLEM, cfg, "run:g1x")
    assert rep.status == "done" and rep.attempts == 2
    assert "unknown" in rep.error_types and not any(e.startswith("llm_output_invalid") for e in rep.error_types)
    assert rep.llm_calls == 3 + 2 + 3 + 1  # plan+gen+skel+fill, crítico (2 intentos ilegibles), gen+skel+fill
