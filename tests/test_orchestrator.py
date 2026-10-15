"""Fase 4 — grafo de agentes con LLM, verificador y retriever simulados (propuesta
§7.1: "cada agente se valida de forma aislada con respuestas de LLM simuladas").
Cubre: camino feliz, re-recuperación tras `lema faltante`, ablación sin Crítico,
presupuesto RNF-3, sin RAG, y las líneas base."""

from __future__ import annotations

import json

import pytest

from ragsolver.agents.agents import Agents, load_agents_config, strip_code_fences
from ragsolver.agents.schemas import Problem, SolutionReport, Verdict
from ragsolver.llm import LLM, LLMResponse
from ragsolver.orchestrator import graph as G
from ragsolver.orchestrator.run import check_index_consistency, effective_config, run_baseline, summarize, wilson
from ragsolver.rag.schema import Citation, Hit, RetrievalResult

PROBLEM = Problem(problem_id="t1", informal_statement="n + 0 = n for every natural n", formal_statement="theorem t1 (n : ℕ) : n + 0 = n := by sorry")


def _hit(decl: str, cid: str = "M1", corpus: str = "mathlib") -> Hit:
    cit = Citation(citation_id=cid, chunk_id=f"{corpus}:{decl}:0", corpus=corpus, unit_type="mathlib_decl", label=decl, locator={"decl_name": decl, "module_path": "Mathlib.X"}, retrieved_for={"problem_id": "t1"})
    return Hit(chunk_id=cit.chunk_id, corpus=corpus, unit_type="mathlib_decl", rank=1, text=decl, metadata={"decl_name": decl, "signature": "sig", "informal_description": "desc", "module_path": "Mathlib.X"}, citation=cit)


class FakeRetriever:
    def __init__(self):
        self.requests = []

    def search(self, req):
        self.requests.append(req)
        if req.mode == "exemplar":
            return RetrievalResult(hits=[])
        if req.mode == "error":
            return RetrievalResult(hits=[_hit("Nat.add_zero", "M9")])
        return RetrievalResult(hits=[_hit("Nat.zero_add", "M1")])


class FakeLLM:
    """Responde según el agente que pregunta; `script` fija qué devuelve el formalizador por intento."""

    def __init__(self, proofs=("simp",), critic=None):
        self.proofs, self.critic, self.calls, self.n_formalize = list(proofs), critic or {}, 0, 0
        self.exhausted, self.models, self.tokens_in, self.tokens_out = [], ["fake"], 0, 0

    @property
    def model(self):
        return "fake"

    def generate(self, prompt, schema=None, images=(), system=None, max_tokens=None):
        self.calls += 1
        if "Decompose the problem" in prompt:
            out = {"strategy": "simp it", "subgoals": [{"subgoal_id": "s1", "statement_nl": "n + 0 = n", "formal_hint": "Nat.add_zero", "search_queries": ["add zero"], "math_area": "algebra"}]}
        elif "proof sketch in natural language" in prompt:
            out = {"sketch": "By Nat.add_zero.", "used_citations": ["M1"]}
        elif "Translate the proof sketch" in prompt:
            p = self.proofs[min(self.n_formalize, len(self.proofs) - 1)]
            self.n_formalize += 1
            out = {"proof_tactics": p, "notes": ""}
        elif "rejected the proof" in prompt:
            out = {"error_type": "missing_lemma", "action": "retry", "missing_identifiers": ["Nat.add_zero"], "feedback": "use Nat.add_zero", **self.critic}
        elif "Prove the following theorem in Lean 4" in prompt:
            out = {"proof_tactics": "simp", "natural_language_proof": "", "self_assessed_correct": None}
        else:
            out = {"proof_tactics": "", "natural_language_proof": "trivial", "self_assessed_correct": True}
        return LLMResponse(json.dumps(out), "fake", 10, 5)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    verdicts = {"exact Nat.add_zero n": True, "exact Nat.zero_add n": True, "simp": True, "bad": False}
    monkeypatch.setattr(G, "verify", lambda stmt, tactics, name, imports, timeout_s, max_heartbeats, header="": Verdict(ok=verdicts.get(tactics, False), errors=[] if verdicts.get(tactics) else [{"line": 5, "col": 2, "message": "unknown identifier 'Nat.add_zero'"}], lean_file=str(tmp_path / f"{name}.lean")))
    monkeypatch.setattr("ragsolver.orchestrator.run.verify", G.verify)
    base = load_agents_config()

    def make(proofs=("simp",), config="full", **over):
        llm = FakeLLM(proofs)
        agents = Agents(llm, base, trace_dir=str(tmp_path / "traces"))
        cfg = effective_config(base, config)
        cfg.update(over)
        return G.Orchestrator(agents, FakeRetriever()), cfg, llm

    return make


def test_happy_path_solves_in_one_attempt_with_verified_citation(setup):
    orch, cfg, llm = setup(proofs=("exact Nat.zero_add n",))
    # el lema recuperado es Nat.zero_add (M1); la prueba lo usa -> used_in_proof
    rep = orch.solve(PROBLEM, cfg, "run:t1")
    assert rep.status == "done" and rep.attempts == 1 and rep.llm_calls == 3  # plan + generate + formalize
    assert rep.n_retrieved == 1 and rep.n_used_in_proof == 1 and rep.n_used_in_sketch == 1
    assert rep.citations[0].locator["decl_name"] == "Nat.zero_add" and rep.citations[0].used_in_proof
    assert "theorem t1 (n : ℕ) : n + 0 = n := by\n  exact Nat.zero_add n" in rep.lean_code
    assert rep.models == ["fake"]


def test_lean_code_reported_includes_the_problem_header(setup):
    """BUG-2: `lean_code` debe incluir los `open` del header — es lo que
    `checker.check`/`verify` reciben de verdad, no un subconjunto."""
    orch, cfg, llm = setup(proofs=("exact Nat.zero_add n",))
    problem = PROBLEM.model_copy(update={"header": "open BigOperators Nat"})
    rep = orch.solve(problem, cfg, "run:t1h")
    assert rep.status == "done"
    lines = rep.lean_code.splitlines()
    assert lines[:2] == ["import Mathlib", "import Aesop"] and lines[3] == "open BigOperators Nat"


def test_missing_lemma_triggers_error_mode_re_retrieval_then_success(setup):
    orch, cfg, llm = setup(proofs=("bad", "exact Nat.add_zero n"))
    rep = orch.solve(PROBLEM, cfg, "run:t1")
    assert rep.status == "done" and rep.attempts == 2
    assert rep.llm_calls == 6  # 3 + crítico + generate + formalize
    assert rep.re_retrievals == 1 and rep.re_retrievals_ok == 1
    assert rep.error_types == ["missing_lemma"]
    modes = [r.mode for r in orch.retriever.requests]
    assert "error" in modes and modes.count("nl") == 2


def test_citation_ids_stay_stable_and_unique_when_a_chunk_reappears(setup):
    """BUG-1: un chunk que ya está en `evidence` (visto en el intento 1) conserva
    su citation_id cuando vuelve a aparecer en una re-recuperación posterior; no
    hay `citation_id` repetidos en el reporte final."""

    class OverlappingRetriever(FakeRetriever):
        def search(self, req):
            self.requests.append(req)
            if req.mode == "exemplar":
                return RetrievalResult(hits=[])
            if req.mode == "error":
                return RetrievalResult(hits=[_hit("Nat.add_zero", "ignored")])  # nuevo en el intento 2
            return RetrievalResult(hits=[_hit("Nat.add_zero", "ignored"), _hit("Nat.zero_add", "ignored")])  # Nat.add_zero YA visto en el intento 1

    orch, cfg, llm = setup(proofs=("bad", "exact Nat.add_zero n"))
    orch.retriever = OverlappingRetriever()
    rep = orch.solve(PROBLEM, cfg, "run:dup")
    assert rep.status == "done"
    ids = [c.citation_id for c in rep.citations]
    assert len(ids) == len(set(ids)), f"citation_id duplicados: {ids}"
    add_zero_ids = {c.citation_id for c in rep.citations if c.locator.get("decl_name") == "Nat.add_zero"}
    assert len(add_zero_ids) == 1  # el mismo chunk conserva un único id pese a aparecer dos veces
    assert any(c.locator["decl_name"] == "Nat.add_zero" and c.used_in_proof for c in rep.citations)


def test_no_critic_ablation_fails_after_one_attempt_without_llm_call(setup):
    orch, cfg, llm = setup(proofs=("bad",), config="no_critic")
    rep = orch.solve(PROBLEM, cfg, "run:t1")
    assert rep.status == "fail" and rep.attempts == 1 and rep.llm_calls == 3
    assert rep.error_types == ["unknown", "exhausted"]


def test_k_max_exhausted_and_budget_guard(setup):
    orch, cfg, llm = setup(proofs=("bad", "bad", "bad", "bad"))
    rep = orch.solve(PROBLEM, cfg, "run:t1")
    assert rep.status == "fail" and rep.attempts == cfg["k_max"] == 3 and rep.error_types[-1] == "exhausted"
    assert rep.llm_calls <= cfg["llm_budget"]

    orch, cfg, llm = setup(proofs=("bad", "bad", "bad"), llm_budget=4)
    rep = orch.solve(PROBLEM, cfg, "run:t1")
    assert rep.status == "fail" and rep.llm_calls <= 4 and rep.error_types[-1] == "budget"


def test_no_rag_uses_no_retrieval(setup):
    orch, cfg, llm = setup(proofs=("simp",), config="no_rag")
    rep = orch.solve(PROBLEM, cfg, "run:t1")
    assert rep.status == "done" and rep.n_retrieved == 0 and orch.retriever.requests == []
    assert cfg["rag"] is False


def test_problem_imports_are_rejected_explicitly_instead_of_ignored(setup):
    """BUG-4: ningún conjunto de problemas usa `imports` hoy; un problema que los
    trae falla con un motivo claro en vez de que el sistema los ignore en silencio."""
    orch, cfg, llm = setup(proofs=("simp",))
    problem = PROBLEM.model_copy(update={"imports": ["Mathlib.Analysis.SpecialFunctions.Trigonometric.Basic"]})
    rep = orch.solve(problem, cfg, "run:imports")
    assert rep.status == "fail" and llm.calls == 0 and "Problem.imports" in rep.error_types[0]


def test_run_baseline_rejects_problem_imports_too(setup):
    orch, cfg_a, llm = setup(config="baseline_a")
    problem = PROBLEM.model_copy(update={"imports": ["Mathlib.Analysis.SpecialFunctions.Trigonometric.Basic"]})
    rep = run_baseline("baseline_a", problem, cfg_a, orch.agents, "run:b")
    assert rep.status == "fail" and rep.llm_calls == 0 and "Problem.imports" in rep.error_types[0]


def test_baselines(setup, tmp_path):
    orch, cfg_a, llm = setup(config="baseline_a")
    rep = run_baseline("baseline_a", PROBLEM, cfg_a, orch.agents, "run:b")
    assert rep.status == "done" and rep.llm_calls == 1 and rep.config == "baseline_a"
    orch, cfg_b, llm = setup(config="baseline_b")
    rep = run_baseline("baseline_b", PROBLEM, cfg_b, orch.agents, "run:b")
    assert rep.status == "fail" and rep.self_assessed_correct is True and rep.verdict is None


def test_check_index_consistency_reports_without_running_lean(capsys):
    """PLAN_RAG.md §12: test de arranque índice <-> Lean. Sin Lean real: comprueba
    que muestrea la tabla, llama a `check_identifiers_exist` y nunca aborta."""

    class FakeTable:
        def search(self):
            return self

        def select(self, cols):
            return self

        def limit(self, n):
            return self

        def to_list(self):
            return [{"decl_name": f"Nat.foo{i}"} for i in range(30)]

        def count_rows(self):
            return 30

    class FakeRetrieverWithTable:
        versions = {"embedding": {"model": "x"}}
        tables = {"mathlib": "mathlib"}

        def _table(self, name):
            return FakeTable()

    class FakeChecker:
        def check_identifiers_exist(self, names):
            return len(names) - 3, len(names)  # simula varios nombres fantasma: por encima de la tolerancia de 1

    check_index_consistency(FakeChecker(), FakeRetrieverWithTable(), n=20, seed=13)
    out = capsys.readouterr().out
    assert "[index-check]" in out and "AVISO" in out


def test_check_index_consistency_never_raises_on_a_broken_retriever():
    class BrokenRetriever:
        versions = {}
        tables = {}

        def _table(self, name):
            raise FileNotFoundError("sin tabla")

    check_index_consistency(object(), BrokenRetriever())  # no debe lanzar


def test_summary_and_wilson():
    assert wilson(0, 0) is None and wilson(5, 5)[0] > 0.5 and wilson(0, 10)[1] < 0.35
    from ragsolver.agents.schemas import SolutionReport

    rs = [SolutionReport(problem_id="a", config="full", status="done", llm_calls=4, n_retrieved=8, n_used_in_proof=2, re_retrievals=1, re_retrievals_ok=1), SolutionReport(problem_id="b", config="full", status="fail", llm_calls=10, error_types=["lean_syntax", "exhausted"])]
    s = summarize(rs)["full"]
    assert s["pass@1"] == 0.5 and s["llm_calls_mean"] == 7 and s["lemma_use_precision"] == 0.25 and s["re_retrieval_success"] == 1.0
    assert s["error_taxonomy"] == {"lean_syntax": 1, "exhausted": 1}


def test_strip_code_fences_and_repeated_statement():
    assert strip_code_fences("```lean\nsimp\n```") == "simp"
    assert strip_code_fences("theorem t1 (n : ℕ) : n + 0 = n := by\n  simp\n  rfl") == "simp\nrfl"
    # tácticas ya indentadas (DeepSeek): se quita la sangría común, se conserva la relativa
    assert strip_code_fences("  have h : 1 = 1 := by\n    rfl\n  simp") == "have h : 1 = 1 := by\n  rfl\nsimp"
    assert strip_code_fences("```lean\n  have h : 1 = 1 := by\n    rfl\n  simp\n```") == "have h : 1 = 1 := by\n  rfl\nsimp"
    # `by` suelto al inicio (DeepSeek): sobra, `splice_proof` ya pone `:= by`
    assert strip_code_fences("by\n  -- paso 1\n  have h : 1 = 1 := by\n    rfl\n  simp") == "-- paso 1\nhave h : 1 = 1 := by\n  rfl\nsimp"
    assert strip_code_fences(":= by\n  simp") == "simp"
    assert strip_code_fences("by_cases h : 1 = 1 <;> simp") == "by_cases h : 1 = 1 <;> simp"  # no confundir con una táctica que empieza por `by`
    # primera línea sin sangría y el resto con sangría (smoke 2026-10-14): el resto baja al nivel de la primera
    assert strip_code_fences("rcases h1 with ⟨k, hk⟩\n  rcases h2 with ⟨l, hl⟩\n  exact ⟨k * l, by rw [hl, hk, mul_assoc]⟩") == "rcases h1 with ⟨k, hk⟩\nrcases h2 with ⟨l, hl⟩\nexact ⟨k * l, by rw [hl, hk, mul_assoc]⟩"
    # …salvo que la primera línea abra un bloque o continúe en la siguiente
    for keep in ("induction n with\n  | zero => simp\n  | succ n ih => simp", "nlinarith [sq_nonneg (a - b),\n  sq_nonneg (a + b)]", "calc a = b := by simp\n  _ = c := by simp"):
        assert strip_code_fences(keep) == keep


def test_splice_proof_keeps_nesting_when_tactics_are_preindented():
    from ragsolver.lean.verify import splice_proof

    stmt = "theorem t : 1 = 1 := by sorry"
    expected = "theorem t : 1 = 1 := by\n  have h : 1 = 1 := by\n    rfl\n  exact h"
    assert splice_proof(stmt, "have h : 1 = 1 := by\n  rfl\nexact h") == expected
    assert splice_proof(stmt, "  have h : 1 = 1 := by\n    rfl\n  exact h") == expected


def test_run_suite_resumes_and_stops_cleanly_on_quota(tmp_path, monkeypatch):
    """Reanudación: con el mismo run_id se saltan los pares ya hechos; si todos los
    modelos agotan cuota, la corrida corta con status quota_exhausted (no 60 fallos)."""
    from ragsolver.llm import DailyQuotaExhausted
    from ragsolver.orchestrator import run as RUN

    class QuotaLLM(FakeLLM):
        def __init__(self, fail_after):
            super().__init__(proofs=("simp",))
            self.fail_after = fail_after

        def generate(self, prompt, **kw):
            if self.calls >= self.fail_after:
                raise DailyQuotaExhausted("all")
            return super().generate(prompt, **kw)

    budget = {"fail_after": 3}  # sólo el primer problema (plan+generate+formalize) antes de agotar
    monkeypatch.setattr(RUN, "LLM", lambda section: QuotaLLM(budget["fail_after"]))
    monkeypatch.setattr(RUN, "Retriever", lambda db_path: FakeRetriever())
    monkeypatch.setattr(RUN, "LeanChecker", lambda imports, timeout_s: None)
    monkeypatch.setattr(RUN, "verify", G.verify)
    monkeypatch.setattr(G, "verify", lambda stmt, tactics, name, imports, timeout_s, max_heartbeats, header="": Verdict(ok=True))
    monkeypatch.setattr(RUN, "verify", G.verify)
    problems = [PROBLEM, Problem(problem_id="t2", informal_statement="x", formal_statement="theorem t2 : True := by sorry")]

    r1 = RUN.run_suite(problems, ["full"], tmp_path, run_id="r")
    assert r1["status"] == "quota_exhausted" and r1["n_done"] == 1 and r1["n_pending"] == 1
    budget["fail_after"] = 100
    r2 = RUN.run_suite(problems, ["full"], tmp_path, run_id="r")
    assert r2["status"] == "completed" and r2["n_done"] == 2 and r2["n_pending"] == 0
    lines = (tmp_path / "r" / "results.jsonl").read_text().splitlines()
    assert len(lines) == 2 and r2["summary"]["full"]["n"] == 2


def test_generator_self_consistency_picks_medoid_and_counts_all_calls(setup):
    orch, cfg, llm = setup(proofs=("simp",))
    orch.agents.cfg = {**orch.agents.cfg, "generator_samples": 3}
    rep = orch.solve(PROBLEM, cfg, "run:sc")
    assert rep.status == "done" and rep.llm_calls == 5 == llm.calls  # 3 muestras + plan + formalize, todas contadas (RNF-3)
    assert rep.tokens_in == 10 * 5  # tokens de las 3 muestras sumados en la respuesta agregada


def test_self_consistency_sums_cost_and_attempts_across_samples(setup):
    """BUG-5: antes `type(resp)(text, model, tokens_in, tokens_out, calls=n)` sólo
    pasaba posicionales + `calls` -> `cost_usd`/`attempts` de la respuesta agregada
    quedaban en los valores por defecto (0.0 / 1), aunque las n muestras hubiesen
    costado más o alguna hubiese reintentado."""
    from ragsolver.agents.schemas import PlannerOutput, Subgoal

    orch, cfg, llm = setup(proofs=("simp",))

    class PricedLLM(type(llm)):
        def generate(self, prompt, **kw):
            r = super().generate(prompt, **kw)
            if "proof sketch in natural language" in prompt:
                r.cost_usd, r.attempts = 0.01, 2
            return r

    orch.agents.llm = PricedLLM(proofs=("simp",))
    orch.agents.cfg = {**orch.agents.cfg, "generator_samples": 3}
    plan = PlannerOutput(strategy="s", subgoals=[Subgoal(subgoal_id="s1", statement_nl="x")])
    out, resp = orch.agents.generate(PROBLEM, plan, [], [], "", "run:sc-cost", 1)
    assert resp.calls == 3
    assert abs(resp.cost_usd - 0.03) < 1e-9  # antes: 0.0 (se perdía)
    assert resp.attempts == 6  # antes: 1 (se perdía)


def test_run_suite_with_workers_completes_every_pair(tmp_path, monkeypatch):
    """`workers` > 1: mismos pares (config, problema) en results.jsonl, sin duplicados ni faltantes."""
    from ragsolver.orchestrator import run as RUN

    monkeypatch.setattr(RUN, "LLM", lambda section: FakeLLM(proofs=("simp",)))
    monkeypatch.setattr(RUN, "Retriever", lambda db_path: FakeRetriever())
    monkeypatch.setattr(RUN, "LeanChecker", lambda imports, timeout_s: None)
    monkeypatch.setattr(G, "verify", lambda stmt, tactics, name, imports, timeout_s, max_heartbeats, header="": Verdict(ok=True))
    monkeypatch.setattr(RUN, "verify", G.verify)
    problems = [Problem(problem_id=f"t{i}", informal_statement="x", formal_statement=f"theorem t{i} : True := by sorry") for i in range(5)]

    r = RUN.run_suite(problems, ["full", "no_rag", "baseline_a"], tmp_path, run_id="w", workers=4)
    assert r["status"] == "completed" and r["n_done"] == 15 and r["n_pending"] == 0
    lines = (tmp_path / "w" / "results.jsonl").read_text().splitlines()
    pairs = {(json.loads(l)["config"], json.loads(l)["problem_id"]) for l in lines}
    assert len(lines) == 15 and pairs == {(c, p.problem_id) for c in ("full", "no_rag", "baseline_a") for p in problems}


def test_agent_call_retries_once_on_truncated_json(setup):
    """JSON truncado por max_tokens: un reintento con más tokens; ambas llamadas cuentan (RNF-3)."""

    class TruncatingLLM(FakeLLM):
        def generate(self, prompt, schema=None, images=(), system=None, max_tokens=None):
            r = super().generate(prompt, schema, images, system, max_tokens)
            if "Decompose the problem" in prompt and max_tokens is None:  # primera llamada del planner: cortada
                return LLMResponse(r.text[: len(r.text) // 2], "fake", 10, 5)
            return r

    orch, cfg, _ = setup(proofs=("simp",))
    orch.agents.llm = TruncatingLLM(proofs=("simp",))
    rep = orch.solve(PROBLEM, cfg, "run:trunc")
    assert rep.status == "done" and rep.llm_calls == 4  # plan (2 llamadas) + generate + formalize


def test_persistently_invalid_json_counts_as_fail_with_calls(setup):
    """Si el JSON sigue inválido tras el reintento, el problema es `fail` (no `error`) y las
    2 llamadas se cuentan (RNF-3)."""

    class BrokenLLM(FakeLLM):
        def generate(self, prompt, schema=None, images=(), system=None, max_tokens=None):
            r = super().generate(prompt, schema, images, system, max_tokens)
            return LLMResponse('{"strategy": "loops forever', "fake", 10, 5) if "Decompose the problem" in prompt else r

    orch, cfg, _ = setup(proofs=("simp",))
    orch.agents.llm = BrokenLLM(proofs=("simp",))
    rep = orch.solve(PROBLEM, cfg, "run:broken")
    assert rep.status == "fail" and rep.llm_calls == 2 and rep.tokens_out == 10 and rep.error_types == ["llm_output_invalid:planner"]


def test_run_suite_retries_error_rows_on_resume(tmp_path, monkeypatch):
    """Filas `error` (infraestructura) no cuentan como hechas: al reanudar se reintentan y se auditan en errors.jsonl."""
    from ragsolver.orchestrator import run as RUN

    monkeypatch.setattr(RUN, "LLM", lambda section: FakeLLM(proofs=("simp",)))
    monkeypatch.setattr(RUN, "Retriever", lambda db_path: FakeRetriever())
    monkeypatch.setattr(RUN, "LeanChecker", lambda imports, timeout_s: None)
    monkeypatch.setattr(G, "verify", lambda stmt, tactics, name, imports, timeout_s, max_heartbeats, header="": Verdict(ok=True))
    monkeypatch.setattr(RUN, "verify", G.verify)
    run_dir = tmp_path / "e"
    run_dir.mkdir()
    (run_dir / "results.jsonl").write_text(SolutionReport(problem_id=PROBLEM.problem_id, config="full", status="error", error_types=["exception: HTTPStatusError 403"]).model_dump_json() + "\n")
    r = RUN.run_suite([PROBLEM], ["full"], tmp_path, run_id="e")
    assert r["n_done"] == 1 and r["summary"]["full"]["pass@1"] == 1.0
    assert [json.loads(l)["status"] for l in (run_dir / "results.jsonl").read_text().splitlines()] == ["done"]
    assert len((run_dir / "errors.jsonl").read_text().splitlines()) == 1


def test_baseline_invalid_json_row_records_which_model_produced_it(tmp_path, monkeypatch):
    """ACC-1: una línea base cuyo LLM nunca da JSON válido queda `fail` con el
    modelo que la produjo — antes `models` quedaba vacío en esta fila."""
    from ragsolver.orchestrator import run as RUN

    class BrokenLLM(FakeLLM):
        def generate(self, prompt, schema=None, images=(), system=None, max_tokens=None):
            return LLMResponse('{"proof_tactics": "unterminated', "fake-broken", 10, 5)

    monkeypatch.setattr(RUN, "LLM", lambda section: BrokenLLM(proofs=("simp",)))
    monkeypatch.setattr(RUN, "Retriever", lambda db_path: FakeRetriever())
    monkeypatch.setattr(RUN, "LeanChecker", lambda imports, timeout_s: None)
    r = RUN.run_suite([PROBLEM], ["baseline_a"], tmp_path, run_id="bm")
    rows = [json.loads(l) for l in (tmp_path / "bm" / "results.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["status"] == "fail"
    assert rows[0]["models"] == ["fake-broken"]  # antes: []


def test_invalid_json_mid_run_keeps_accumulated_calls_and_evidence(setup):
    """Generador con JSON inválido en el intento 2: el reporte conserva las llamadas previas
    (plan + gen + form + crítico = 4) más las 2 del generador roto, y la evidencia recuperada."""

    class LateBreak(FakeLLM):
        n_gen = 0

        def generate(self, prompt, schema=None, images=(), system=None, max_tokens=None):
            if "proof sketch in natural language" in prompt:
                self.n_gen += 1
                if self.n_gen >= 2:
                    self.calls += 1
                    return LLMResponse('{"sketch": "rambling', "fake", 10, 5)
            return super().generate(prompt, schema, images, system, max_tokens)

    orch, cfg, _ = setup(proofs=("bad",))
    orch.agents.llm = LateBreak(proofs=("bad",))
    rep = orch.solve(PROBLEM, cfg, "run:late")
    assert rep.status == "fail" and rep.llm_calls == 6 and rep.n_retrieved >= 1
    assert rep.error_types[-1] == "llm_output_invalid:generator"


def test_generic_exception_mid_run_becomes_status_error_and_keeps_accumulated_state(setup):
    """ACC-1: una excepción que no es `InvalidLLMOutput` (un bug, el REPL de Lean,
    la red) ya no descarta lo acumulado — antes `solve()` la atajaba por fuera del
    grafo y reemplazaba todo el estado final por `{**init, fail_reason}` (0
    llamadas, 0 evidencia). Ahora `_guard` la atrapa a nivel de nodo: el estado
    que ya fusionó LangGraph (plan + retrieve, aquí 2 llamadas y 1 cita) se
    conserva, y sólo `generate()` (el nodo que revienta) queda como `error`."""

    class BoomOnGenerate(FakeLLM):
        def generate(self, prompt, schema=None, images=(), system=None, max_tokens=None):
            if "proof sketch in natural language" in prompt:
                raise RuntimeError("boom: bug simulado, no InvalidLLMOutput")
            return super().generate(prompt, schema, images, system, max_tokens)

    orch, cfg, _ = setup(proofs=("simp",))
    orch.agents.llm = BoomOnGenerate(proofs=("simp",))
    rep = orch.solve(PROBLEM, cfg, "run:boom")
    assert rep.status == "error"
    assert rep.llm_calls == 1  # la llamada del planificador se conserva (antes: 0, `solve()` reiniciaba todo)
    assert rep.n_retrieved == 1  # la cita de `retrieve()` (0 llamadas LLM) también se conserva
    assert "RuntimeError" in rep.error_types[-1] and "boom" in rep.error_types[-1]


def test_baseline_schemas_require_the_field_each_baseline_produces():
    """Regresión #65: con campos opcionales el modelo llenaba el campo equivocado (pruebas vacías)."""
    from ragsolver.agents.schemas import BaselineAOutput, BaselineBOutput

    assert BaselineAOutput.model_json_schema()["required"] == ["proof_tactics"]
    assert sorted(BaselineBOutput.model_json_schema()["required"]) == ["natural_language_proof", "self_assessed_correct"]
    with pytest.raises(ValueError):
        BaselineAOutput.model_validate_json('{"natural_language_proof": "by AM-GM"}')
