"""docs/PLAN_REDISENO_PROVER.md — demostrador local con servidor simulado (0 API, sin GPU):
etapa 0 sobre el teorema completo, autocorrección con nombres sugeridos por el RAG, línea base
`baseline_prover`, degradación ante salidas ilegibles del Crítico, y las piezas puras."""

from __future__ import annotations

import json

from ragsolver.agents.agents import Agents, load_agents_config
from ragsolver.agents.schemas import Problem, Verdict
from ragsolver.llm import LLMResponse
from ragsolver.orchestrator import graph as G
from ragsolver.orchestrator.run import effective_config, run_prover_only
from ragsolver.prover import extract_tactics, goal_to_theorem, prove, renamed_inaccessible, unknown_names
from ragsolver.rag.schema import Citation, Hit, RetrievalResult

PROBLEM = Problem(problem_id="p1", informal_statement="x = x", formal_statement="theorem p1 (x : ℝ) (h : 0 < x) : x ≤ x + 1 := by sorry", header="open Real")


def _answer(tactics: str, name: str = "p1") -> str:
    return f"<think>plan</think>\nProof plan...\n```lean4\nimport Mathlib\n\ntheorem {name} (x : ℝ) (h : 0 < x) : x ≤ x + 1 := by\n" + "\n".join("  " + l for l in tactics.splitlines()) + "\n```"


class FakeProver:
    def __init__(self, answers):
        self.answers, self.requests = list(answers), []

    def sample(self, messages, k, seed0=13):
        self.requests.append((messages, k))
        out = [self.answers.pop(0) if self.answers else "(nothing)" for _ in range(k)]
        return out


class FakeChecker:
    """ok si la táctica es `linarith`; `Real.add_pow_le_pow_mul_pow_of_sq_le_sq` (nombre viejo) da unknown constant."""

    def __init__(self):
        self.checked = []

    def check(self, stmt, tactics, header="", hb=400000):
        self.checked.append(tactics)
        if "old_lemma_name" in tactics:
            return Verdict(ok=False, errors=[{"line": 3, "col": 2, "message": "Unknown constant `old_lemma_name`"}])
        return Verdict(ok=tactics.strip() in ("linarith", "exact le_add_of_nonneg_right zero_le_one"), errors=[] if tactics.strip() in ("linarith", "exact le_add_of_nonneg_right zero_le_one") else [{"line": 3, "col": 2, "message": "linarith failed"}])

    def signatures(self, names, header=""):
        return {n: "∀ {a b : α}, 0 ≤ b → a ≤ a + b" for n in names if n == "le_add_of_nonneg_right"}

    def goals(self, stmt, skeleton, header="", hb=400000):
        return [], []


class FakeRetriever:
    def __init__(self):
        self.requests = []

    def search(self, req):
        self.requests.append(req)
        cit = Citation(citation_id="M1", chunk_id="mathlib:le_add_of_nonneg_right:0", corpus="mathlib", unit_type="mathlib_decl", label="x", locator={"decl_name": "le_add_of_nonneg_right"}, retrieved_for={"problem_id": "p1"})
        return RetrievalResult(hits=[Hit(chunk_id=cit.chunk_id, corpus="mathlib", unit_type="mathlib_decl", rank=1, text="", metadata={"decl_name": "le_add_of_nonneg_right"}, citation=cit)])


class NoLLM:
    exhausted, models, tokens_in, tokens_out = [], ["none"], 0, 0

    def generate(self, *a, **k):
        raise AssertionError("la etapa 0 no debe llamar a la API")


def test_root_stage_solves_with_zero_api_calls(tmp_path):
    base = load_agents_config()
    cfg = effective_config(base, "full")
    orch = G.Orchestrator(Agents(NoLLM(), base, trace_dir=str(tmp_path)), FakeRetriever(), FakeChecker(), FakeProver([_answer("nlinarith"), _answer("linarith")] + ["x"] * 6))
    rep = orch.solve(PROBLEM, cfg, "run:p1")
    assert rep.status == "done" and rep.llm_calls == 0 and rep.solved_by == "prover_root"
    assert ":= by\n  linarith" in rep.lean_code and rep.prover_samples >= 2


def test_correction_round_gets_rag_name_suggestions_and_cites_them():
    pr = FakeProver([_answer("exact old_lemma_name zero_le_one")] + ["(nothing)"] * 3 + [_answer("exact le_add_of_nonneg_right zero_le_one")])
    ck, rt = FakeChecker(), FakeRetriever()
    tac, log = prove(pr, ck, PROBLEM.formal_statement, PROBLEM.header, "p1", k=4, correction_rounds=1, retriever=rt)
    assert tac == "exact le_add_of_nonneg_right zero_le_one" and log["solved_in"] == "correction1"
    fix_msg = pr.requests[-1][0][-1]["content"]
    assert "old_lemma_name` does not exist" in fix_msg and "le_add_of_nonneg_right : " in fix_msg  # sugerencia validada contra Lean
    assert log["suggested"] == {"le_add_of_nonneg_right": "∀ {a b : α}, 0 ≤ b → a ≤ a + b"} and rt.requests[0].mode == "error"
    # sin RAG: la corrección lleva sólo los errores de Lean
    pr2 = FakeProver([_answer("exact old_lemma_name zero_le_one")] + ["(nothing)"] * 3 + ["(nothing)"])
    prove(pr2, FakeChecker(), PROBLEM.formal_statement, PROBLEM.header, "p1", k=4, correction_rounds=1, retriever=None)
    assert "Existing declarations" not in pr2.requests[-1][0][-1]["content"]


def test_baseline_prover_has_no_rag_and_no_api():
    cfg = effective_config(load_agents_config(), "baseline_prover")
    rep = run_prover_only(PROBLEM, cfg, FakeChecker(), FakeProver([_answer("linarith")] + ["x"] * 7))
    assert rep.status == "done" and rep.llm_calls == 0 and rep.solved_by == "prover_root"


def test_pure_helpers():
    out = "x\n```lean4\ntheorem t (x : ℝ) : x = x := by\n  rfl\n\ntheorem other : True := by\n  trivial\n```"
    assert extract_tactics(out, "t") == "rfl"
    assert extract_tactics("no code", "t") is None and extract_tactics("```lean4\ntheorem t : True := by\n```", "t") is None
    assert unknown_names([{"message": "unknown identifier 'foo.bar'"}, {"message": "Unknown constant `baz`"}]) == ["foo.bar", "baz"]
    g = "n : ℕ\ninst✝ : Fact (Nat.Prime n)\nx✝ : ℕ\nh : x✝ ∣ n\n⊢ x✝ = 1 ∨ x✝ = n"
    assert goal_to_theorem(g, "g") == "theorem g (n : ℕ) [Fact (Nat.Prime n)] (x_0 : ℕ) (h : x_0 ∣ n) : x_0 = 1 ∨ x_0 = n := by sorry"
    assert renamed_inaccessible(g) == ["x_0"]
    assert goal_to_theorem("no turnstile", "g") is None


def test_disk_cache_reuses_identical_samples(tmp_path, monkeypatch):
    from ragsolver.prover import LocalProver

    calls = {"n": 0}

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "```lean4\ntheorem t : True := by\n  trivial\n```"}}], "usage": {"completion_tokens": 7}}

    def post(url, json):
        calls["n"] += 1
        return Resp()

    p1 = LocalProver(url="http://x", cache_path=str(tmp_path / "c.jsonl"))
    monkeypatch.setattr(p1.client, "post", post)
    msgs = [{"role": "user", "content": "q"}]
    a = p1.sample(msgs, 2)
    p2 = LocalProver(url="http://x", cache_path=str(tmp_path / "c.jsonl"))  # otra corrida: lee la caché del disco
    monkeypatch.setattr(p2.client, "post", post)
    assert p2.sample(msgs, 2) == a and calls["n"] == 2  # 2 semillas distintas, generadas una sola vez
    assert p2.sample(msgs, 3)[2] and calls["n"] == 3  # sólo la semilla nueva va al servidor


def test_batches_stop_early_once_solved():
    pr = FakeProver([_answer("linarith")] + ["x"] * 3 + ["y"] * 4)
    tac, log = prove(pr, FakeChecker(), PROBLEM.formal_statement, PROBLEM.header, "p1", k=8, correction_rounds=0, batch=4)
    assert tac == "linarith" and len(pr.requests) == 1 and log["samples"] == 4
