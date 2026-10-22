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


class DraftLLM:
    """Planificador + Generador para DRAFT; cualquier otro agente es un error (no hay respaldo por API)."""

    exhausted, models, tokens_in, tokens_out = [], ["fake"], 0, 0

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, schema=None, images=(), system=None, max_tokens=None):
        self.prompts.append(prompt)
        if "Decompose the problem" in prompt:
            out = {"strategy": "STRATEGY-AM-GM", "subgoals": [{"subgoal_id": "s1", "statement_nl": "STEP-ONE x is positive"}]}
        elif "proof sketch in natural language" in prompt:
            out = {"sketch": "SKETCH-TEXT add one to both sides", "used_citations": []}
        else:
            raise AssertionError(f"llamada a la API fuera de DRAFT: {prompt[:80]}")
        return LLMResponse(json.dumps(out), "fake", 10, 5)


class GoalChecker(FakeChecker):
    def goals(self, stmt, skeleton, header="", hb=400000):
        return [{"goal": "x : ℝ\nh : 0 < x\n⊢ x ≤ x + 1", "proof_state": 0}], []


def test_api_fallback_off_ends_after_stage0_without_api_calls(tmp_path):
    cfg = effective_config(load_agents_config(), "full")
    assert cfg["prover"]["api_fallback"] is False and not cfg["prover"]["dsp"]
    orch = G.Orchestrator(Agents(NoLLM(), load_agents_config(), trace_dir=str(tmp_path)), FakeRetriever(), FakeChecker(), FakeProver(["x"] * 12))
    rep = orch.solve(PROBLEM, cfg, "run:p1")
    assert rep.status == "fail" and rep.llm_calls == 0 and any("api_fallback" in e for e in rep.error_types)


def test_draft_puts_plan_sketch_and_rag_lemmas_in_the_prover_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(G.P, "prior_channel", lambda goal, k=32: ["le_add_of_nonneg_right"])
    monkeypatch.setattr(G.P, "load_prior", lambda: ({"decl_name": "le_add_of_nonneg_right", "type": "0 ≤ b → a ≤ a + b"},))
    base = load_agents_config()
    cfg = effective_config(base, "dsp")
    llm = DraftLLM()
    # etapa 0: 8 muestras + 2 correcciones fallan; DRAFT: la primera muestra con el borrador verifica
    pr = FakeProver([_answer("nlinarith")] * 10 + [_answer("linarith")])
    orch = G.Orchestrator(Agents(llm, base, trace_dir=str(tmp_path)), FakeRetriever(), GoalChecker(), pr)
    rep = orch.solve(PROBLEM, cfg, "run:p1")
    assert rep.status == "done" and rep.solved_by == "prover_draft" and rep.llm_calls == 2
    draft_prompt = pr.requests[-1][0][-1]["content"]
    assert "STRATEGY-AM-GM" in draft_prompt and "STEP-ONE" in draft_prompt and "SKETCH-TEXT" in draft_prompt
    assert "le_add_of_nonneg_right : 0 ≤ b → a ≤ a + b" in draft_prompt
    assert "le_add_of_nonneg_right" in {c.locator.get("decl_name") for c in rep.citations}  # el lema mostrado queda citado
    assert "SKETCH-TEXT" not in pr.requests[0][0][-1]["content"]  # la etapa 0 no lleva borrador


class RepairChecker(FakeChecker):
    """Las pruebas del demostrador tienen un paso malo (`BAD`): el resto elabora y cierra."""

    def __init__(self):
        super().__init__()
        self.steps = []

    def check(self, stmt, tactics, header="", hb=400000):
        ok = "BAD" not in tactics and "sorry" not in tactics
        return Verdict(ok=ok, errors=[] if ok else [{"line": 3, "col": 2, "message": "nlinarith failed"}])

    def goals(self, stmt, skeleton, header="", hb=400000):
        return [{"goal": "x : ℝ\nh : 0 < x\n⊢ 0 ≤ 1", "proof_state": 0}, {"goal": "x : ℝ\nh : 0 < x\nh1 : 0 ≤ 1\n⊢ x ≤ x + 1", "proof_state": 1}], []

    def step(self, proof_state, tactic, timeout=60, heartbeats=None, gen=None):
        self.steps.append(tactic)
        return "BAD" not in tactic, "" if "BAD" not in tactic else "nlinarith failed", []


def test_repair_closes_the_bad_step_of_a_failed_prover_proof_with_zero_api(tmp_path, monkeypatch):
    monkeypatch.setattr(G.P, "prior_channel", lambda goal, k=32: ["le_add_of_nonneg_right"])
    monkeypatch.setattr(G.P, "load_prior", lambda: ({"decl_name": "le_add_of_nonneg_right", "type": "0 ≤ b → a ≤ a + b"},))
    base = load_agents_config()
    cfg = effective_config(base, "repair")
    assert cfg["prover"]["repair"] and not cfg["prover"]["api_fallback"]
    ck = RepairChecker()
    near_miss = _answer("have h1 : (0 : ℝ) ≤ 1 := by\n  BAD\nlinarith")
    orch = G.Orchestrator(Agents(NoLLM(), base, trace_dir=str(tmp_path)), FakeRetriever(), ck, FakeProver([near_miss] * 10))
    rep = orch.solve(PROBLEM, cfg, "run:p1")
    assert rep.status == "done" and rep.solved_by == "prover_repair" and rep.llm_calls == 0
    assert "BAD" not in rep.lean_code and "linarith" in rep.lean_code  # el paso del demostrador que sí servía se conserva
    assert ck.steps[0] == "BAD"  # primero su propio bloque; la batería cierra el paso que fallaba


class SketchLLM:
    """El esqueleto en Lean: un paso `have` con sorry y el cierre; la primera ronda no trae bloque de código."""

    exhausted, models, tokens_in, tokens_out = [], ["fake"], 0, 0

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, schema=None, images=(), system=None, max_tokens=None):
        assert schema is None and "Lean 4 proof skeleton" in prompt  # texto, no JSON (DECISIONS #124)
        self.prompts.append(prompt)
        text = "I could not decide." if len(self.prompts) == 1 else _answer("have h1 : (0 : ℝ) ≤ 1 := by sorry\nlinarith")
        return LLMResponse(text, "fake", 10, 5)


def test_sketch_skeleton_steps_are_closed_by_automation_and_the_prover(tmp_path, monkeypatch):
    monkeypatch.setattr(G.P, "prior_channel", lambda goal, k=32: ["le_add_of_nonneg_right"])
    monkeypatch.setattr(G.P, "load_prior", lambda: ({"decl_name": "le_add_of_nonneg_right", "type": "0 ≤ b → a ≤ a + b"},))
    base = load_agents_config()
    cfg = effective_config(base, "sketch")
    llm = SketchLLM()
    orch = G.Orchestrator(Agents(NoLLM(), base, trace_dir=str(tmp_path)), FakeRetriever(), RepairChecker(), FakeProver(["x"] * 12), Agents(llm, base, trace_dir=str(tmp_path)))
    rep = orch.solve(PROBLEM, cfg, "run:p1")
    assert rep.status == "done" and rep.solved_by == "prover_sketch" and rep.llm_calls == 2
    assert "theorem p1" in llm.prompts[1] and "had no ```lean4 block" in llm.prompts[1]  # la ronda 2 recibe el motivo
    assert "sorry" not in rep.lean_code.split(":= by", 1)[1] and "linarith" in rep.lean_code


def test_proof_corpus_parsing_strips_comments_and_keeps_statement():
    from ragsolver.rag.proofs import norm_statement, parse

    raw = "import Mathlib\nopen Real\n\n/- prove that $x \\le x + 1$ -/\ntheorem lean_workbook_1 (x : ℝ) : x ≤ x + 1 := by\n  /- informal\n  explanation -/\n  -- a comment\n  nlinarith [sq_nonneg x]\n"
    p = parse(raw)
    assert p == {"informal": "prove that $x \\le x + 1$", "statement": "theorem lean_workbook_1 (x : ℝ) : x ≤ x + 1", "proof": "nlinarith [sq_nonneg x]"}
    assert norm_statement(p["statement"]) == norm_statement("theorem other  (x : ℝ) :  x ≤ x + 1 := by sorry")
    assert parse("no theorem here") is None


def test_rag_proofs_tries_retrieved_proofs_then_puts_them_in_the_prover_prompt(tmp_path, monkeypatch):
    base = load_agents_config()
    cfg = effective_config(base, "rag_proofs")
    hit = {"problem_id": "lean_workbook_7", "statement": "theorem lean_workbook_7 (y : ℝ) : y ≤ y + 2", "proof": "nlinarith [sq_nonneg y]", "sim": 0.81}
    monkeypatch.setattr(G, "similar_proofs", lambda text, k=3, max_sim=0.95: [hit])
    ck = FakeChecker()
    pr = FakeProver(["x"] * 8 + [_answer("linarith")] + ["x"] * 7)
    rep = G.Orchestrator(Agents(NoLLM(), base, trace_dir=str(tmp_path)), FakeRetriever(), ck, pr).solve(PROBLEM, cfg, "run:p1")
    assert rep.status == "done" and rep.solved_by == "prover_examples" and rep.llm_calls == 0
    assert "nlinarith [sq_nonneg y]" in ck.checked  # primero la prueba recuperada tal cual (no cierra: otro enunciado)
    prompt = pr.requests[-1][0][-1]["content"]
    assert "theorem lean_workbook_7 (y : ℝ) : y ≤ y + 2 := by\n  nlinarith [sq_nonneg y]" in prompt
    assert "lean_workbook_7" not in pr.requests[0][0][-1]["content"]  # la etapa 0 no lleva ejemplos (comparte caché)
    # la prueba recuperada que cierra el enunciado tal cual resuelve sin GPU
    monkeypatch.setattr(G, "similar_proofs", lambda text, k=3, max_sim=0.95: [{**hit, "proof": "linarith"}])
    pr2 = FakeProver(["x"] * 8)
    rep2 = G.Orchestrator(Agents(NoLLM(), base, trace_dir=str(tmp_path)), FakeRetriever(), FakeChecker(), pr2).solve(PROBLEM, cfg, "run:p1")
    assert rep2.solved_by == "prover_examples" and len(pr2.requests) == 2  # sólo los 2 lotes de la etapa 0


class StaleChecker(RepairChecker):
    """El primer `step` reinicia el REPL (timeout de otro hilo): los proof_state de antes dejan de valer."""

    def __init__(self):
        super().__init__()
        self.gen, self.n_goals = 1, 0

    def goals(self, stmt, skeleton, header="", hb=400000):
        self.n_goals += 1
        gs, errs = super().goals(stmt, skeleton, header, hb)
        return [{**g, "gen": self.gen} for g in gs], errs

    def step(self, proof_state, tactic, timeout=60, heartbeats=None, gen=None):
        self.gen = 2
        if gen != self.gen:
            return False, "stale proof state (lean repl restarted)", []
        return super().step(proof_state, tactic, timeout, heartbeats, gen)


def test_close_reelaborates_the_skeleton_when_the_repl_restarts_instead_of_leaving_steps_unproved(tmp_path, monkeypatch):
    # eval-v4: tras un reinicio del REPL, todos los pasos siguientes quedaban "stale" sin probarse
    monkeypatch.setattr(G.P, "prior_channel", lambda goal, k=32: ["le_add_of_nonneg_right"])
    monkeypatch.setattr(G.P, "load_prior", lambda: ({"decl_name": "le_add_of_nonneg_right", "type": "0 ≤ b → a ≤ a + b"},))
    base = load_agents_config()
    ck = StaleChecker()
    near_miss = _answer("have h1 : (0 : ℝ) ≤ 1 := by\n  BAD\nlinarith")
    rep = G.Orchestrator(Agents(NoLLM(), base, trace_dir=str(tmp_path)), FakeRetriever(), ck, FakeProver([near_miss] * 10)).solve(PROBLEM, effective_config(base, "repair"), "run:p1")
    assert rep.status == "done" and rep.solved_by == "prover_repair" and ck.n_goals == 2  # esqueleto elaborado otra vez


def test_heartbeat_limit_covers_multiline_blocks():
    from ragsolver.lean.verify import limit_heartbeats

    assert limit_heartbeats(" linarith ", 5) == "set_option maxHeartbeats 5 in linarith"
    assert limit_heartbeats("rename_i x\nnlinarith [sq_nonneg x]\n  <;> simp", 5) == "set_option maxHeartbeats 5 in\n· rename_i x\n  nlinarith [sq_nonneg x]\n    <;> simp"


def test_nested_config_overrides_merge_with_base():
    base = load_agents_config()
    p32 = effective_config(base, "baseline_prover32")["prover"]
    assert p32["k_root"] == 32 and p32["max_tokens"] == base["prover"]["max_tokens"] and p32["enabled"]
    assert effective_config(base, "dsp")["prover"]["dsp"] is True and effective_config(base, "dsp")["prover"]["k_draft"] == base["prover"]["k_draft"]


def test_batches_stop_early_once_solved():
    pr = FakeProver([_answer("linarith")] + ["x"] * 3 + ["y"] * 4)
    tac, log = prove(pr, FakeChecker(), PROBLEM.formal_statement, PROBLEM.header, "p1", k=8, correction_rounds=0, batch=4)
    assert tac == "linarith" and len(pr.requests) == 1 and log["samples"] == 4


def test_dead_server_stops_the_run_instead_of_recording_false_failures(tmp_path, monkeypatch):
    import httpx
    import pytest

    from ragsolver.prover import LocalProver, ProverUnavailable

    p = LocalProver(url="http://x", cache_path=None)
    monkeypatch.setattr(p.client, "post", lambda url, json: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    monkeypatch.setattr(p, "alive", lambda: False)
    with pytest.raises(ProverUnavailable):
        p.sample([{"role": "user", "content": "q"}], 2)

    class DeadProver:
        def sample(self, messages, k, seed0=13):
            raise ProverUnavailable("caído")

    orch = G.Orchestrator(Agents(NoLLM(), load_agents_config(), trace_dir=str(tmp_path)), FakeRetriever(), FakeChecker(), DeadProver())
    with pytest.raises(ProverUnavailable):  # ni `fail` ni el bucle por API: el runner corta y se reanuda
        orch.solve(PROBLEM, effective_config(load_agents_config(), "full"), "run:p1")
