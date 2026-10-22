"""Orquestador (docs/PLAN_RAG.md §5.3, Fase 4; propuesta §6.1-6.2; docs/PLAN_MEJORA_RAG.md §4).

PROVE (demostrador local, 0 API) -> [DRAFT: plan + prueba informal de la API en el prompt del demostrador]
-> (si `prover.api_fallback` o no hay demostrador) PLAN -> RETRIEVE -> GENERATE -> FORMALIZE -> [CLOSE -> FILL]
-> VERIFY -> (DONE | CRITIQUE -> retry/replan/fail)

Con `goal_mode` (por defecto) el Autoformalizador escribe un esqueleto `have … := by sorry`; CLOSE
obtiene del REPL el goal de cada `sorry`, recupera premisas PARA ESE GOAL (rag/premises.py) y prueba
una batería de tácticas con ellas (0 LLM); FILL (1 llamada) sólo ve los goals que quedaron abiertos.
Los goals cerrados quedan en `proved` y se reutilizan en reintentos y replans (D5 del plan).

LangGraph `StateGraph` sobre un dict. El estado lleva `evidence` (citas
consultadas, §6.4), la recuperación por subobjetivo y contadores de presupuesto
(RNF-3: `llm_budget` llamadas por problema). El retriever cuesta 0 llamadas.
Sin checkpointer persistente (ponytail: MemorySaver/SQLite cuando haga falta
reanudar corridas largas).
"""

from __future__ import annotations

import re
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ragsolver.agents.agents import Agents, InvalidLLMOutput
from ragsolver.agents.schemas import CriticOutput, FillOutput, Problem, SolutionReport, Verdict
from ragsolver.lean.verify import LeanChecker, build_file, count_sorries, fill_sorries, identifiers_used, skeletonize, verify
from ragsolver.llm import DailyQuotaExhausted
from ragsolver.prover import ProverUnavailable, draft_hint, examples_hint, extract_tactics, goal_to_theorem, prove as prover_prove, renamed_inaccessible
from ragsolver.rag import premises as P
from ragsolver.rag.proofs import similar as similar_proofs
from ragsolver.rag.retrieve import Retriever
from ragsolver.rag.schema import Citation, Hit, RetrievalRequest


class State(TypedDict, total=False):
    problem: Problem
    cfg: dict
    run_id: str
    exemplars: list[Hit]
    plan: Any
    lemmas: list[Hit]
    evidence: dict[str, Citation]
    citation_ids: dict[str, str]
    sketch: str
    used_citations: list[str]
    proof_tactics: str
    lean_code: str
    verdict: Verdict | None
    lean_error: str
    feedback: str
    missing_identifiers: list[str]
    attempt: int
    replans: int
    history: list[dict]
    error_types: list[str]
    re_retrievals: int
    re_retrievals_ok: int
    llm_calls: int
    retrieval_calls: int
    tokens_in: int
    tokens_out: int
    models: list[str]
    status: str
    fail_reason: str
    t0: float
    skeleton: str
    candidates: list  # bloque del LLM para cada `sorry` del esqueleto (None = sin candidato)
    goals: list[dict]  # [{goal, proof_state, block, premises}] del esqueleto actual
    proved: dict[str, str]  # goal normalizado -> bloque que lo cerró (se conserva entre intentos y replans)
    prover_samples: int
    solved_by: str


def _count(state: State, resp) -> dict:
    return {
        "llm_calls": state.get("llm_calls", 0) + getattr(resp, "calls", 1),
        "tokens_in": state.get("tokens_in", 0) + resp.tokens_in,
        "tokens_out": state.get("tokens_out", 0) + resp.tokens_out,
        "models": list(dict.fromkeys(state.get("models", []) + [resp.model])),
    }


# lo que REPAIR y SKETCH conservan de un CLOSE que no terminó la prueba: pasos probados y contabilidad
_CLOSE_KEEP = ("proved", "prover_samples", "evidence", "citation_ids", "lemmas", "retrieval_calls")


def _over_budget(state: State) -> bool:
    return state.get("llm_calls", 0) >= state["cfg"]["llm_budget"]


def _format_error(verdict: Verdict) -> str:
    return "\n".join(f"line {e['line']}:{e['col']}: {e['message']}" for e in verdict.errors)[:3000] or ("uses sorry" if verdict.uses_sorry else "unknown error")


def _assign_citation_ids(state: State, hits: list[Hit], prefix: str) -> dict[str, str]:
    """BUG-1: mapa `chunk_id -> citation_id` estable durante todo el problema —
    reutiliza el id ya asignado a un chunk que vuelve a aparecer (tras un replan
    o una re-recuperación) y sólo asigna el siguiente número libre del prefijo a
    los chunks nuevos. Antes `retrieve()` renumeraba `M1..Mn` en cada llamada:
    un chunk que ya estaba en `evidence` con `M3` podía volver a verse como `M1`
    en la siguiente búsqueda, y el `M1` real quedaba duplicado (34/300 reportes
    de `eval_v1` con `citation_id` repetidos, afectando `used_in_sketch`)."""
    ids = dict(state.get("citation_ids", {}))
    next_n = max((int(v[len(prefix):]) for v in ids.values() if v.startswith(prefix) and v[len(prefix):].isdigit()), default=0) + 1
    for h in hits:
        if h.chunk_id not in ids:
            ids[h.chunk_id] = f"{prefix}{next_n}"
            next_n += 1
        h.citation.citation_id = ids[h.chunk_id]
    return ids


def _goal_key(goal: str) -> str:
    return " ".join(goal.split())


def render_tactic(entry: dict, use_rag: bool, premises: list[str], hints: list[str]) -> str | None:
    """Táctica de la batería CLOSE para una entrada de `close.tactics` (None = se omite)."""
    if not use_rag:
        return entry.get("plain")
    tac = entry["rag"]
    if ("{premises}" in tac and not premises) or ("{hints}" in tac and not hints):
        return entry.get("plain")
    return tac.replace("{premises}", ", ".join(premises)).replace("{hints}", ", ".join(hints))


def concrete_block(tactic: str, infos: list[str]) -> str:
    """`try?` cierra el goal y sugiere el script concreto ("Try this: [apply] exact …"): ese script es
    el que va al archivo final, para que el kernel nunca acepte una táctica con `?`."""
    if not tactic.rstrip().endswith("?"):
        return tactic
    for info in infos:
        for line in info.splitlines():
            line = line.strip()
            if line.startswith("[apply] "):
                return line[len("[apply] "):]
    return tactic


def close_goal(checker: LeanChecker, goal: dict, tactics: list[dict], use_rag: bool, premises: list[str], hints: list[str], timeout_s: float = 60, heartbeats: int | None = 200000) -> tuple[str | None, list[dict]]:
    """Batería CLOSE sobre un goal (docs/PLAN_MEJORA_RAG.md Fase 4): la primera táctica que lo cierra
    gana. Devuelve (bloque concreto o None, [{tactic, closed, error}])."""
    log = []
    for entry in tactics:
        tac = render_tactic(entry, use_rag, premises, hints)
        if not tac:
            continue
        closed, msg, infos = checker.step(goal["proof_state"], tac, timeout=timeout_s, heartbeats=heartbeats, gen=goal.get("gen"))
        log.append({"tactic": tac[:200], "closed": closed, "error": "" if closed else msg[:300]})
        if closed:
            return concrete_block(tac, infos), log
        if "stale proof state" in msg or "lean repl failure" in msg:  # el REPL se reinició: el goal queda abierto
            break
    return None, log


def _premise_hit(name: str, sig: str, dense: dict[str, Hit], ctx: dict) -> Hit:
    if name in dense:
        return dense[name]
    cit = Citation(citation_id="M0", chunk_id=f"mathlib:{name}:0", corpus="mathlib", unit_type="mathlib_decl", label=name, locator={"decl_name": name, "module_path": "", "docs_url": ""}, retrieved_for=ctx)
    return Hit(chunk_id=cit.chunk_id, corpus="mathlib", unit_type="mathlib_decl", rank=1, text=f"{name} : {sig}", metadata={"decl_name": name, "signature": sig}, citation=cit)


def select_premises(retriever: Retriever | None, goal: str, k: int = 16, n_candidates: int = 50, rerank: bool = False, ctx: dict | None = None, dense_channel: bool = False) -> tuple[list[str], dict[str, Hit]]:
    """Premisas para un goal de Lean (docs/PLAN_MEJORA_RAG.md Fase 3): canal A (prior filtrado por los
    símbolos del goal) y, si `dense_channel`, canal C (índice denso con la meta del goal) fusionado con
    RRF. Devuelve (nombres, hits densos por nombre). 0 llamadas LLM.

    El canal C está apagado por defecto (`close.dense_channel`, DECISIONS #98): recall@16 por goal 0.028
    frente a 0.327 del prior, y fusionado con RRF baja el total a 0.097."""
    a = P.prior_channel(goal, k=2 * k)
    dense: dict[str, Hit] = {}
    if retriever is not None and dense_channel:
        try:
            req = RetrievalRequest(queries=[P.goal_target(goal)[:2000] or goal[:2000]], mode="formal", corpora=["mathlib"], k=k, n_candidates=n_candidates, rerank=rerank, include_figures=False, context=ctx or {"problem_id": "", "agent": "close"})
            dense = {h.metadata.get("decl_name"): h for h in retriever.search(req).hits if h.metadata.get("decl_name")}
        except Exception:  # sin índice denso (tests, demo sin tabla): sólo el prior
            pass
    return P.fuse(a, list(dense), k=k), dense


class Orchestrator:
    def __init__(self, agents: Agents, retriever: Retriever | None, checker: LeanChecker | None = None, prover=None, drafter: Agents | None = None):
        self.agents = agents
        self.retriever = retriever
        self.checker = checker  # None -> un proceso `lake env lean` por verificación
        self.prover = prover  # demostrador local (ragsolver.prover.LocalProver) o None
        self.drafter = drafter  # Agents con el LLM que escribe el borrador de DRAFT (`llm_draft`, con razonamiento); None = `agents`
        self.graph = self._build()

    # -- nodos ---------------------------------------------------------------------

    def _prover_cfg(self, state: State) -> dict | None:
        pcfg = state["cfg"].get("prover") or {}
        return pcfg if pcfg.get("enabled") and self.prover is not None and self.checker is not None else None

    def _api_loop(self, state: State) -> bool:
        """¿Corre el bucle de agentes por API (PLAN → … → CRITIQUE) cuando el demostrador no resuelve? Sin
        demostrador, siempre; con él, sólo si `prover.api_fallback` (en eval-v3 costó US$0.06 en los 9
        problemas que el demostrador no resolvió y no resolvió ninguno)."""
        pcfg = self._prover_cfg(state)
        return pcfg is None or bool(pcfg.get("api_fallback", True))

    def prove(self, state: State) -> dict:
        """Etapa 0 (docs/PLAN_REDISENO_PROVER.md): el demostrador local intenta el teorema completo, 0 API.
        Con RAG, la autocorrección recibe nombres que existen en el Mathlib fijado (se citan)."""
        pcfg = self._prover_cfg(state)
        if pcfg is None:
            return {}
        cfg, problem = state["cfg"], state["problem"]
        name = re.match(r"\s*(?:theorem|lemma)\s+(\S+)", problem.formal_statement)
        if not name:
            return {}
        retr = self.retriever if cfg["rag"] and pcfg.get("rag_names") else None
        tac, log = prover_prove(self.prover, self.checker, problem.formal_statement, problem.header, name.group(1), k=pcfg["k_root"], correction_rounds=pcfg.get("correction_rounds", 1), retriever=retr, max_heartbeats=cfg["lean"]["max_heartbeats"])
        self.agents.trace("prover", state["run_id"], problem.problem_id, 1, log.get("seconds", 0) * 1000, {"stage": "root", "solved": bool(tac), **{k: v for k, v in log.items() if k not in ("suggested", "candidates")}, "suggested": list(log.get("suggested", {}))})
        delta = self._cite(state, log.get("suggested", {}), "prover")
        delta["prover_samples"] = state.get("prover_samples", 0) + log["samples"] + log["rounds"] * 2
        if tac:
            delta.update(proof_tactics=tac, solved_by="prover_root")
            return delta
        if pcfg.get("examples") and cfg["rag"]:
            delta.update(self.examples({**state, **delta}, name.group(1), pcfg))
        if pcfg.get("repair") and not delta.get("solved_by"):
            delta.update(self.repair({**state, **delta}, log.get("candidates", []), pcfg["repair"]))
        if pcfg.get("sketch") and not delta.get("solved_by"):
            delta.update(self.sketch({**state, **delta}, name.group(1), pcfg["sketch"]))
        if not delta.get("solved_by") and not pcfg.get("dsp") and not self._api_loop(state):
            delta.update(status="fail", fail_reason="prover: sin prueba (respaldo por API apagado: prover.api_fallback)")
        return delta

    def examples(self, state: State, name: str, pcfg: dict) -> dict:
        """EXAMPLES (`prover.examples`, RAG de pruebas, `rag/proofs.py`): si la etapa 0 falla, las pruebas
        verificadas de los `examples` problemas más parecidos (embeddings sobre Lean Workbook) (1) se prueban tal
        cual sobre el enunciado (0 GPU: la misma táctica a veces cierra un problema análogo) y (2) van como
        ejemplos en el prompt del demostrador (`k_examples` muestras, como DRAFT; su control es
        `baseline_prover16`)."""
        cfg, problem = state["cfg"], state["problem"]
        t0 = time.time()
        hits = similar_proofs(f"{problem.informal_statement}\n{problem.formal_statement}", k=pcfg["examples"], max_sim=pcfg.get("max_sim", 0.95))
        info = {"stage": "examples", "retrieved": [(h["problem_id"], h["sim"]) for h in hits]}
        delta = {"retrieval_calls": state.get("retrieval_calls", 0) + 1}
        for h in hits:
            if self.checker.check(problem.formal_statement, h["proof"], problem.header, cfg["lean"]["max_heartbeats"]).ok:
                self.agents.trace("prover", state["run_id"], problem.problem_id, 1, (time.time() - t0) * 1000, {**info, "solved": True, "solved_in": f"direct:{h['problem_id']}"})
                return {**delta, "proof_tactics": h["proof"], "solved_by": "prover_examples"}
        if not hits:
            return delta
        tac, log = prover_prove(self.prover, self.checker, problem.formal_statement, problem.header, name, k=pcfg.get("k_examples", 8), correction_rounds=0, max_heartbeats=cfg["lean"]["max_heartbeats"], hint=examples_hint(hits))
        self.agents.trace("prover", state["run_id"], problem.problem_id, 1, (time.time() - t0) * 1000, {**info, "solved": bool(tac), **{k: v for k, v in log.items() if k not in ("suggested", "candidates")}})
        delta["prover_samples"] = state.get("prover_samples", 0) + log["samples"]
        if tac:
            delta.update(proof_tactics=tac, solved_by="prover_examples")
        return delta

    def repair(self, state: State, candidates: list[str], n: int) -> dict:
        """REPAIR (0 API, `prover.repair`): una prueba fallida del demostrador suele tener casi todos los pasos
        bien. Se parte en sus `have` como la del Formalizador (`skeletonize`) y pasa por CLOSE: el bloque del
        propio demostrador primero, la batería y el demostrador sobre el lema corto sólo en los pasos que fallan.
        Se prueban las `n` mejores (menos errores de Lean) cuyo esqueleto elabora; lo probado queda en `proved`."""
        keep: dict = {}
        tried = 0
        for tac in candidates:
            if tried >= n:
                break
            skeleton, cands = skeletonize(tac)
            if count_sorries(skeleton) < 2:  # sin `have` no hay nada que partir: es la misma prueba que ya falló
                continue
            d = self.close({**state, **keep, "skeleton": skeleton, "candidates": cands})
            keep.update({k: d[k] for k in _CLOSE_KEEP if k in d})
            if not d.get("goals"):  # el esqueleto no elabora (falla el cierre o la estructura): cuesta una llamada al REPL
                continue
            tried += 1
            if "proof_tactics" in d:
                return {**keep, "proof_tactics": d["proof_tactics"], "solved_by": "prover_repair"}
        return keep

    def sketch(self, state: State, name: str, rounds: int) -> dict:
        """SKETCH (`prover.sketch` rondas; receta de DeepSeek-Prover-V2): el modelo con razonamiento (`llm_draft`)
        escribe el esqueleto en Lean, pasos `have … := by sorry`; CLOSE cierra cada paso con la batería y el
        demostrador sobre el lema corto. Si quedan pasos abiertos, la ronda siguiente le muestra cuáles para que
        los parta más; si el esqueleto no elabora, el error de Lean."""
        delta: dict = {}
        feedback = ""
        for rnd in range(rounds):
            if _over_budget({**state, **delta}):
                break
            text, resp = (self.drafter or self.agents).sketch(state["problem"], feedback, state["run_id"], rnd + 1)
            delta.update(_count({**state, **delta}, resp))
            tac = extract_tactics(text, name)
            if not tac:
                feedback = f"Your answer had no ```lean4 block with `theorem {name}`."
                continue
            skeleton, cands = skeletonize(tac)
            d = self.close({**state, **delta, "skeleton": skeleton, "candidates": cands})
            delta.update({k: d[k] for k in _CLOSE_KEEP if k in d})
            if "proof_tactics" in d:
                return {**delta, "proof_tactics": d["proof_tactics"], "solved_by": "prover_sketch"}
            if not d.get("goals"):
                feedback = f"Previous skeleton:\n{tac[:3000]}\nIt does not compile in Lean:\n{d.get('lean_error', '')[:1500]}"
            else:
                open_ = "\n\n".join(g["goal"] for g in d["goals"] if not g.get("block"))
                feedback = f"Previous skeleton:\n{tac[:3000]}\nNo automation could prove these steps (Lean goals). Split them into smaller `have` steps or use another argument:\n{open_[:3000]}"
        return delta

    def draft(self, state: State) -> dict:
        """DRAFT (Draft-Sketch-Prove, `prover.dsp`): si la etapa 0 no resolvió, el modelo de la API escribe el
        plan y la prueba informal (Planificador + Generador, 2 llamadas) y el demostrador local recibe eso,
        junto con los lemas del RAG para el goal raíz, DENTRO de su prompt (`k_draft` muestras). Es el lugar
        donde la API y el RAG pueden mover la prueba: el demostrador sabe Lean y el modelo general razona en
        lenguaje natural."""
        if _over_budget(state):
            return {"status": "fail", "fail_reason": "budget"}
        pcfg, cfg, problem = self._prover_cfg(state), state["cfg"], state["problem"]
        agents = self.drafter or self.agents
        exemplars, delta = self._exemplars(state)
        plan, r1 = agents.plan(problem, exemplars, state["run_id"], 1)
        delta.update(plan=plan, exemplars=exemplars, **_count(state, r1))
        out, r2 = agents.generate(problem, plan, [], exemplars, "", state["run_id"], 1)
        delta.update(sketch=out.sketch, used_citations=out.used_citations, **_count({**state, **delta}, r2))
        lemmas: dict[str, str] = {}
        if cfg["rag"]:  # canal A (prior de competición) sobre el goal raíz, con su firma
            goals, errors = self.checker.goals(problem.formal_statement, "sorry", problem.header, cfg["lean"]["max_heartbeats"])
            if len(goals) == 1 and not errors:
                prior = {r["decl_name"]: r.get("type", "") for r in P.load_prior()}
                lemmas = {n: prior.get(n, "") for n in P.prior_channel(goals[0]["goal"], k=pcfg.get("k_lemmas", 10))}
        hint = draft_hint(plan.strategy, [s.statement_nl for s in plan.subgoals], out.sketch, lemmas)
        name = re.match(r"\s*(?:theorem|lemma)\s+(\S+)", problem.formal_statement).group(1)
        tac, log = prover_prove(self.prover, self.checker, problem.formal_statement, problem.header, name, k=pcfg.get("k_draft", 8), correction_rounds=0, max_heartbeats=cfg["lean"]["max_heartbeats"], hint=hint)
        self.agents.trace("prover", state["run_id"], problem.problem_id, 1, log.get("seconds", 0) * 1000, {"stage": "draft", "solved": bool(tac), "lemmas": list(lemmas), **{k: v for k, v in log.items() if k not in ("suggested", "candidates")}})
        delta.update(self._cite({**state, **delta}, lemmas, "prover"))
        delta["prover_samples"] = state.get("prover_samples", 0) + log["samples"]
        if tac:
            delta.update(proof_tactics=tac, solved_by="prover_draft")
        elif not self._api_loop(state):
            delta.update(status="fail", fail_reason="prover: sin prueba tras DRAFT (respaldo por API apagado: prover.api_fallback)")
        return delta

    def _exemplars(self, state: State) -> tuple[list[Hit], dict]:
        """Ejemplares del corpus informal para el Planificador (0 LLM) y su delta de citas/evidencia."""
        cfg, problem = state["cfg"], state["problem"]
        exemplars: list[Hit] = []
        searched = cfg["rag"] and "informal" in cfg["corpora"] and self.retriever is not None
        if searched:
            req = RetrievalRequest(queries=[problem.informal_statement[:2000]], mode="exemplar", corpora=["informal"], k=cfg["retrieval"]["k_informal"], n_candidates=cfg["retrieval"]["n_candidates"], rerank=cfg["retrieval"]["rerank"], context={"problem_id": problem.problem_id, "subgoal_id": "", "attempt": state.get("attempt", 1), "agent": "planner", "run_id": state["run_id"]})
            exemplars = self.retriever.search(req).hits
        citation_ids = _assign_citation_ids(state, exemplars, "I")
        evidence = dict(state.get("evidence", {}))
        for h in exemplars:
            evidence.setdefault(h.chunk_id, h.citation)
        return exemplars, {"evidence": evidence, "citation_ids": citation_ids, "retrieval_calls": state.get("retrieval_calls", 0) + int(searched)}

    def _cite(self, state: State, suggested: dict[str, str], agent: str) -> dict:
        """Nombres que propuso el RAG (sugerencias de la autocorrección) como citas `M*`."""
        if not suggested:
            return {}
        ctx = {"problem_id": state["problem"].problem_id, "subgoal_id": "", "attempt": state.get("attempt", 1), "agent": agent, "run_id": state["run_id"]}
        hits = [_premise_hit(n, sig, {}, ctx) for n, sig in suggested.items()]
        evidence = dict(state.get("evidence", {}))
        ids = _assign_citation_ids(state, hits, "M")
        for h in hits:
            evidence.setdefault(h.chunk_id, h.citation)
        return {"evidence": evidence, "citation_ids": ids}

    def plan(self, state: State) -> dict:
        if _over_budget(state):
            return {"status": "fail", "fail_reason": "budget"}
        problem = state["problem"]
        exemplars, ex_delta = self._exemplars(state)
        feedback = ""
        if state.get("plan") is not None:  # replan (DES-1): razón del Crítico + plan anterior + lo ya probado
            proved = [P.goal_target(k) for k in state.get("proved", {})]
            feedback = f"Critic: {state.get('feedback', '')}\nPrevious strategy: {state['plan'].strategy}" + ("\nAlready proved (reuse as facts):\n" + "\n".join(f"- {t}" for t in proved) if proved else "")
        plan, resp = self.agents.plan(problem, exemplars, state["run_id"], state.get("attempt", 1), feedback=feedback)
        return {"exemplars": exemplars, "plan": plan, "feedback": "", "solved_by": "", **ex_delta, **_count(state, resp)}

    def retrieve(self, state: State) -> dict:
        cfg, problem = state["cfg"], state["problem"]
        if not cfg["rag"] or self.retriever is None:
            return {"lemmas": [], "missing_identifiers": []}
        hits: dict[str, Hit] = {}
        k = cfg["retrieval"]["k_mathlib"]
        ctx = lambda sg, agent: {"problem_id": problem.problem_id, "subgoal_id": sg, "attempt": state.get("attempt", 1), "agent": agent, "run_id": state["run_id"]}
        re_ok, re_n = state.get("re_retrievals_ok", 0), state.get("re_retrievals", 0)
        n_calls = state.get("retrieval_calls", 0)
        missing = state.get("missing_identifiers", [])
        if missing:  # re-recuperación tras `lema faltante` (0 llamadas LLM)
            re_n += 1
            queries = [f"unknown identifier '{m}'" for m in missing[:3]] + [state.get("lean_error", "")[:500] or problem.formal_statement[:500]]
            res = self.retriever.search(RetrievalRequest(queries=queries[:4], mode="error", corpora=["mathlib"], k=k, n_candidates=cfg["retrieval"]["n_candidates"], rerank=cfg["retrieval"]["rerank"], context=ctx("", "critic")))
            n_calls += 1
            if any(h.metadata.get("decl_name") in missing for h in res.hits):
                re_ok += 1
            for h in res.hits:
                hits.setdefault(h.chunk_id, h)
        for sg in [] if cfg.get("goal_mode") and self.checker is not None else state["plan"].subgoals:  # goal_mode: las premisas se recuperan por goal en CLOSE
            queries = ([sg.statement_nl] + list(sg.search_queries))[:4]
            res = self.retriever.search(RetrievalRequest(queries=queries, mode="nl", corpora=["mathlib"], k=k, n_candidates=cfg["retrieval"]["n_candidates"], rerank=cfg["retrieval"]["rerank"], rerank_against=sg.statement_nl, context=ctx(sg.subgoal_id, "retriever")))
            n_calls += 1
            for h in res.hits:
                hits.setdefault(h.chunk_id, h)
        lemmas = list(hits.values())
        citation_ids = _assign_citation_ids(state, lemmas, "M")
        evidence = dict(state.get("evidence", {}))
        for h in lemmas:
            evidence.setdefault(h.chunk_id, h.citation)
        return {"lemmas": lemmas, "evidence": evidence, "citation_ids": citation_ids, "missing_identifiers": [], "re_retrievals": re_n, "re_retrievals_ok": re_ok, "retrieval_calls": n_calls}

    def generate(self, state: State) -> dict:
        if _over_budget(state):
            return {"status": "fail", "fail_reason": "budget"}
        out, resp = self.agents.generate(state["problem"], state["plan"], state.get("lemmas", []), state.get("exemplars", []), state.get("feedback", ""), state["run_id"], state.get("attempt", 1))
        return {"sketch": out.sketch, "used_citations": out.used_citations, **_count(state, resp)}

    def formalize(self, state: State) -> dict:
        if _over_budget(state):
            return {"status": "fail", "fail_reason": "budget"}
        goal_mode = bool(state["cfg"].get("goal_mode")) and self.checker is not None
        proved = [f"have : {P.goal_target(k)} := by {b}" for k, b in state.get("proved", {}).items()]
        out, resp = self.agents.formalize(state["problem"], state["sketch"], state.get("lemmas", []), state.get("lean_error", ""), state["run_id"], state.get("attempt", 1), skeleton=goal_mode, proved=proved)
        if goal_mode:  # la prueba del modelo se parte en goals; sus bloques son el primer candidato de cada uno
            skeleton, candidates = skeletonize(out.proof_tactics)
            return {"skeleton": skeleton, "candidates": candidates, "proof_tactics": skeleton, **_count(state, resp)}
        lean = state["cfg"]["lean"]
        problem = state["problem"]
        # BUG-2: `lean_code` reportado == lo que verifica `verify()` (mismo `theorem_block`, mismos `open`).
        code = build_file(problem.formal_statement, out.proof_tactics, lean["imports"] + problem.imports, lean["max_heartbeats"], header=problem.header)
        return {"proof_tactics": out.proof_tactics, "lean_code": code, **_count(state, resp)}

    def close(self, state: State) -> dict:
        """CLOSE (0 LLM): goals del esqueleto -> premisas por goal -> batería de tácticas."""
        cfg, problem = state["cfg"], state["problem"]
        lean, ccfg = cfg["lean"], cfg["close"]
        skeleton = state["skeleton"]
        goals, errors = self.checker.goals(problem.formal_statement, skeleton, problem.header, lean["max_heartbeats"])
        n_tokens = count_sorries(skeleton)
        if errors or len(goals) != n_tokens:
            msg = "\n".join(f"line {e['line']}:{e['col']}: {e['message']}" for e in errors) or f"the skeleton has {n_tokens} `sorry` but Lean found {len(goals)} goals: use exactly one `sorry` per goal"
            self.agents.trace("close", state["run_id"], problem.problem_id, state.get("attempt", 1), 0, {"skeleton_ok": False, "error": msg[:300]})
            code = build_file(problem.formal_statement, skeleton, lean["imports"] + problem.imports, lean["max_heartbeats"], header=problem.header)
            return {"goals": [], "lean_error": msg[:3000], "lean_code": code, "verdict": Verdict(ok=False, errors=errors or [{"line": 0, "col": 0, "message": msg}])}
        use_rag = bool(cfg["rag"]) and self.retriever is not None
        proved = dict(state.get("proved", {}))
        evidence = dict(state.get("evidence", {}))
        new_hits: list[Hit] = []
        n_calls = state.get("retrieval_calls", 0)
        candidates = state.get("candidates") or [None] * len(goals)
        n_prover_goals, prover_samples = 0, state.get("prover_samples", 0)
        pcfg = self._prover_cfg(state)

        def refresh() -> None:  # el REPL se reinició (timeout de este hilo o de otro): proof_state nuevos
            fresh, errs = self.checker.goals(problem.formal_statement, skeleton, problem.header, lean["max_heartbeats"])
            if not errs and len(fresh) == len(goals):
                for h, f in zip(goals, fresh):
                    h.update(proof_state=f["proof_state"], gen=f["gen"])

        for i, g in enumerate(goals):
            g["candidate"] = candidates[i] if i < len(candidates) else None
            for _ in range(2):  # 2.º intento sólo si el REPL se reinició a mitad del goal (eval-v4: pasos "stale" sin probar)
                if getattr(self.checker, "gen", g.get("gen")) != g.get("gen"):
                    refresh()
                prover_ok = pcfg is not None and n_prover_goals < pcfg.get("max_goals", 3)
                block, names, hits, log, n = self._close_one(state, g, i, proved, use_rag, prover_ok)
                n_calls += hits is not None
                new_hits += hits or []
                prover_samples += n
                n_prover_goals += any(t["tactic"] == "prover" for t in log)
                if block or not any("stale proof state" in t["error"] for t in log):
                    break
            g.update(block=block, premises=names, log=log)
            if block:
                proved[_goal_key(g["goal"])] = block
            self.agents.trace("close", state["run_id"], problem.problem_id, state.get("attempt", 1), 0, {"goal": i, "closed": bool(block), "block": (block or "")[:200], "premises": names, "tried": log})
        citation_ids = _assign_citation_ids(state, new_hits, "M")
        for h in new_hits:
            evidence.setdefault(h.chunk_id, h.citation)
        delta = {"goals": goals, "prover_samples": prover_samples, "lean_error": "", "proved": proved, "evidence": evidence, "citation_ids": citation_ids, "lemmas": list({h.chunk_id: h for h in state.get("lemmas", []) + new_hits}.values()), "retrieval_calls": n_calls}
        if all(g["block"] for g in goals):
            delta["proof_tactics"] = fill_sorries(skeleton, [g["block"] for g in goals])
        return delta

    def _close_one(self, state: State, g: dict, i: int, proved: dict, use_rag: bool, prover_ok: bool) -> tuple[str | None, list[str], list[Hit] | None, list[dict], int]:
        """Un goal de CLOSE: lo ya probado en un intento anterior, el bloque del modelo para ese paso, la batería
        con las premisas del goal y, si `prover_ok`, el demostrador sobre el lema corto. Devuelve (bloque o None,
        premisas, hits del RAG o None si no se buscó, log, muestras del demostrador)."""
        ccfg, hb = state["cfg"]["close"], state["cfg"]["lean"]["max_heartbeats"]
        key = _goal_key(g["goal"])
        if key in proved:
            closed, _msg, _ = self.checker.step(g["proof_state"], proved[key], timeout=ccfg["timeout_s"], heartbeats=hb, gen=g.get("gen"))
            if closed:
                return proved[key], [], None, [{"tactic": "reused", "closed": True, "error": ""}], 0
        cand_log = []
        if g.get("candidate"):
            closed, msg, _ = self.checker.step(g["proof_state"], g["candidate"], timeout=ccfg["timeout_s"], heartbeats=hb, gen=g.get("gen"))
            cand_log = [{"tactic": "llm", "closed": closed, "error": "" if closed else msg[:300]}]
            if closed:
                return g["candidate"], [], None, cand_log, 0
        names, hints, hits = [], [], None
        if use_rag:
            names, hits = self._premises(state, g["goal"], ccfg["k_premises"], i)
            hints = P.nlinarith_hints(g["goal"])
        block, log = close_goal(self.checker, g, ccfg["tactics"], use_rag, names, hints, ccfg["timeout_s"])
        n = 0
        if block is None and prover_ok:
            block, plog, n = self._prove_goal(state, g, i, self._prover_cfg(state))
            log = log + plog
        return block, names, hits, cand_log + log, n

    def _prove_goal(self, state: State, g: dict, i: int, pcfg: dict) -> tuple[str | None, list[dict], int]:
        """Un goal abierto como lema autónomo para el demostrador (receta DeepSeek-Prover-V2); el bloque se
        acepta sólo si cierra el goal EN SU CONTEXTO (`step`), con `rename_i` para los nombres inaccesibles."""
        name = f"rs_goal_{i}"
        stmt = goal_to_theorem(g["goal"], name)
        if stmt is None:
            return None, [{"tactic": "prover", "closed": False, "error": "goal no convertible a lema"}], 0
        problem = state["problem"]
        tac, plog = prover_prove(self.prover, self.checker, stmt, problem.header, name, k=pcfg.get("k_goal", 2), correction_rounds=0, max_heartbeats=state["cfg"]["lean"]["max_heartbeats"])
        n = plog["samples"]
        if not tac:
            return None, [{"tactic": "prover", "closed": False, "error": f"{plog['extracted']}/{n} muestras extraídas, ninguna verifica"}], n
        renamed = renamed_inaccessible(g["goal"])
        block = (f"rename_i {' '.join(renamed)}\n" if renamed else "") + tac
        closed, msg, _ = self.checker.step(g["proof_state"], block, timeout=state["cfg"]["close"]["timeout_s"], heartbeats=state["cfg"]["lean"]["max_heartbeats"], gen=g.get("gen"))
        return (block if closed else None), [{"tactic": "prover", "closed": closed, "error": "" if closed else msg[:300]}], n

    def _premises(self, state: State, goal: str, k: int, i: int) -> tuple[list[str], list[Hit]]:
        problem = state["problem"]
        ctx = {"problem_id": problem.problem_id, "subgoal_id": f"G{i + 1}", "attempt": state.get("attempt", 1), "agent": "close", "run_id": state["run_id"]}
        ccfg = state["cfg"]["close"]
        names, dense = select_premises(self.retriever, goal, k, state["cfg"]["retrieval"]["n_candidates"], state["cfg"]["retrieval"]["rerank"], ctx, dense_channel=bool(ccfg.get("dense_channel")))
        prior = {r["decl_name"]: r for r in P.load_prior()}
        return names, [_premise_hit(n, prior.get(n, {}).get("type", ""), dense, ctx) for n in names]

    def fill(self, state: State) -> dict:
        """FILL (1 llamada): los goals que CLOSE no cerró, cada uno con su goal exacto y sus premisas."""
        if _over_budget(state):
            return {"status": "fail", "fail_reason": "budget"}
        problem, goals = state["problem"], [dict(g) for g in state["goals"]]
        open_ = [i for i, g in enumerate(goals) if not g.get("block")]
        sigs = {h.metadata.get("decl_name"): h.metadata.get("signature", "") for h in state.get("lemmas", [])}
        parts = []
        for j, i in enumerate(open_):
            g = goals[i]
            lem = "\n".join(f"  {n} : {sigs.get(n, '')[:300]}" for n in g.get("premises", [])) or "  (none)"
            err = next((t["error"] for t in reversed(g.get("log", [])) if t["error"]), "")
            prev = ""
            if g.get("candidate"):
                cand_err = next((t["error"] for t in g.get("log", []) if t["tactic"] == "llm"), "")
                prev = f"\nYour previous attempt for this goal (rejected by Lean):\n{g['candidate'][:1500]}\nLean error: {cand_err[:400]}"
            parts.append(f"[G{j + 1}]\n{g['goal']}\nLemmas:\n{lem}{prev}\nAutomation error: {err[:300] or '(none)'}")
        try:
            out, resp = self.agents.fill(problem, "\n\n".join(parts), state["run_id"], state.get("attempt", 1))
        except InvalidLLMOutput as e:  # #110: sin propuestas válidas los goals siguen abiertos; no se tira el problema
            out, resp = FillOutput(proofs=[]), e
        by_id = {p.goal_id.strip().upper(): p.tactics for p in out.proofs}
        proved = dict(state.get("proved", {}))
        for j, i in enumerate(open_):
            tac = by_id.get(f"G{j + 1}", "").strip()
            if not tac:
                goals[i]["block"] = "done"  # sin respuesta: el goal queda abierto y Lean lo reporta
                continue
            closed, msg, _ = self.checker.step(goals[i]["proof_state"], tac, timeout=state["cfg"]["close"]["timeout_s"], gen=goals[i].get("gen"))
            goals[i]["block"] = tac
            if closed:
                proved[_goal_key(goals[i]["goal"])] = tac
        return {"goals": goals, "proved": proved, "proof_tactics": fill_sorries(state["skeleton"], [g["block"] for g in goals]), **_count(state, resp)}

    def verify(self, state: State) -> dict:
        lean = state["cfg"]["lean"]
        problem = state["problem"]
        if self.checker is not None:
            v = self.checker.check(problem.formal_statement, state["proof_tactics"], problem.header, lean["max_heartbeats"])
        else:
            v = verify(problem.formal_statement, state["proof_tactics"], f"{problem.problem_id}_a{state.get('attempt', 1)}", lean["imports"] + problem.imports, lean["timeout_s"], lean["max_heartbeats"], header=problem.header)
        self.agents.trace("verifier", state["run_id"], problem.problem_id, state.get("attempt", 1), v.seconds * 1000, {"ok": v.ok, "uses_sorry": v.uses_sorry, "n_errors": len(v.errors), "first_error": (v.errors[0]["message"][:300] if v.errors else "")})
        # BUG-2: `lean_code` reportado == lo verificado (en goal_mode el archivo se arma recién aquí)
        code = build_file(problem.formal_statement, state["proof_tactics"], lean["imports"] + problem.imports, lean["max_heartbeats"], header=problem.header)
        if v.ok:
            return {"verdict": v, "status": "done", "lean_error": "", "lean_code": code, "solved_by": state.get("solved_by") or "agents"}
        return {"verdict": v, "lean_error": _format_error(v), "lean_code": code}

    def critique(self, state: State) -> dict:
        cfg = state["cfg"]
        attempt = state.get("attempt", 1)
        history = list(state.get("history", []))
        if cfg["k_max"] <= 1:  # ablación "sin Crítico": un solo intento, sin llamada
            history.append({"attempt": attempt, "error_type": "unknown", "summary": state["lean_error"][:200]})
            return {"status": "fail", "fail_reason": "exhausted", "history": history, "error_types": state.get("error_types", []) + ["unknown"]}
        if _over_budget(state):
            return {"status": "fail", "fail_reason": "budget", "history": history}
        try:
            out, resp = self.agents.critique(state["problem"], state["sketch"], state["lean_code"], state["lean_error"], history, state["run_id"], attempt)
        except InvalidLLMOutput as e:  # #110: crítica ilegible -> reintento con los errores de Lean como feedback
            out, resp = CriticOutput(error_type="unknown", action="retry", feedback=f"Fix the Lean errors:\n{state['lean_error'][:1500]}"), e
        history.append({"attempt": attempt, "error_type": out.error_type, "action": out.action, "summary": out.feedback[:300]})
        error_types = state.get("error_types", []) + [out.error_type]
        delta = {"history": history, "error_types": error_types, "feedback": out.feedback, "missing_identifiers": out.missing_identifiers, "attempt": attempt + 1, **_count(state, resp)}
        if out.action == "abandon":
            return {**delta, "status": "fail", "fail_reason": "abandon"}
        if attempt + 1 > cfg["k_max"]:
            return {**delta, "status": "fail", "fail_reason": "exhausted"}
        if out.action == "replan":
            if state.get("replans", 0) >= cfg["max_replans"]:
                return {**delta, "status": "fail", "fail_reason": "exhausted"}
            return {**delta, "replans": state.get("replans", 0) + 1, "status": "replan"}
        return {**delta, "status": "retry"}

    # -- grafo -------------------------------------------------------------------

    @staticmethod
    def _guard(node):
        """JSON inválido tras el reintento (`InvalidLLMOutput`) = el intento termina en fallo dentro del
        grafo, conservando lo acumulado (llamadas, tokens, evidencia, historial). Antes la excepción
        salía de `invoke` y el reporte perdía todo lo anterior (DECISIONS #61).

        ACC-1: cualquier OTRA excepción (un bug, el REPL de Lean, la red) se captura igual, a nivel de
        nodo — el estado acumulado hasta ese punto ya está fusionado por LangGraph y no se pierde,
        a diferencia del `except Exception` que tenía `solve()` antes, que reemplazaba todo el estado
        final por `{**init, "fail_reason": ...}` y descartaba llamadas/tokens/evidencia ya generados."""

        def guarded(state: State) -> dict:
            try:
                return node(state)
            except InvalidLLMOutput as e:  # e lleva tokens_in/tokens_out/calls/model, como una respuesta
                return {"status": "fail", "fail_reason": f"llm_output_invalid:{e.agent}", **_count(state, e)}  # `report` lo agrega a error_types
            except (DailyQuotaExhausted, ProverUnavailable):
                raise  # sin modelos disponibles hoy o sin demostrador: debe salir para que el runner corte la corrida y reanude mañana
            except Exception as e:
                return {"status": "error", "fail_reason": f"exception: {type(e).__name__}: {str(e)[:300]}"}

        return guarded

    def _build(self):
        g = StateGraph(State)
        for name in ("prove", "draft", "plan", "retrieve", "generate", "formalize", "close", "fill", "verify", "critique"):
            g.add_node(name, self._guard(getattr(self, name)))
        g.add_edge(START, "prove")
        g.add_conditional_edges("prove", lambda s: "end" if s.get("status") in ("fail", "error") else ("verify" if s.get("solved_by") in ("prover_root", "prover_examples", "prover_repair", "prover_sketch") else ("draft" if (self._prover_cfg(s) or {}).get("dsp") else "plan")), {"verify": "verify", "draft": "draft", "plan": "plan", "end": END})
        g.add_conditional_edges("draft", lambda s: "end" if s.get("status") in ("fail", "error") else ("verify" if s.get("solved_by") == "prover_draft" else "plan"), {"verify": "verify", "plan": "plan", "end": END})
        g.add_conditional_edges("plan", lambda s: "end" if s.get("status") in ("fail", "error") else "retrieve", {"retrieve": "retrieve", "end": END})
        g.add_conditional_edges("retrieve", lambda s: "end" if s.get("status") == "error" else "generate", {"generate": "generate", "end": END})
        g.add_conditional_edges("generate", lambda s: "end" if s.get("status") in ("fail", "error") else "formalize", {"formalize": "formalize", "end": END})
        g.add_conditional_edges("formalize", lambda s: "end" if s.get("status") in ("fail", "error") else ("close" if s.get("skeleton") is not None and s["cfg"].get("goal_mode") and self.checker is not None else "verify"), {"close": "close", "verify": "verify", "end": END})
        # CLOSE: esqueleto que no elabora -> Crítico; todo cerrado -> VERIFY; quedan goals -> FILL
        g.add_conditional_edges("close", lambda s: "end" if s.get("status") == "error" else ("critique" if not s.get("goals") and s.get("lean_error") else ("verify" if all(x.get("block") for x in s.get("goals", [])) else "fill")), {"critique": "critique", "verify": "verify", "fill": "fill", "end": END})
        g.add_conditional_edges("fill", lambda s: "end" if s.get("status") in ("fail", "error") else "verify", {"verify": "verify", "end": END})
        # si la etapa 0 dio una prueba que VERIFY no acepta (no debería: ya pasó `check`), siguen los agentes
        g.add_conditional_edges("verify", lambda s: "end" if s.get("status") in ("done", "error") or not self._api_loop(s) else ("plan" if s.get("solved_by") in ("prover_root", "prover_examples", "prover_repair", "prover_sketch", "prover_draft") and not s.get("history") else "critique"), {"critique": "critique", "plan": "plan", "end": END})
        g.add_conditional_edges(
            "critique",
            lambda s: {"retry": ("retrieve" if s.get("missing_identifiers") else "generate"), "replan": "plan"}.get(s.get("status"), "end"),
            {"retrieve": "retrieve", "generate": "generate", "plan": "plan", "end": END},
        )
        return g.compile()

    def solve(self, problem: Problem, cfg: dict, run_id: str) -> SolutionReport:
        t0 = time.time()
        if problem.imports:  # nadie usa imports adicionales por problema hoy (auditoría BUG-4); error explícito en vez de ignorarlos en silencio
            return self.report(problem, cfg, {"fail_reason": f"unsupported: Problem.imports no soportado: {problem.imports}"}, "fail", 0.0)
        init: State = {"problem": problem, "cfg": cfg, "run_id": run_id, "attempt": 1, "replans": 0, "evidence": {}, "history": [], "error_types": [], "llm_calls": 0, "retrieval_calls": 0, "tokens_in": 0, "tokens_out": 0, "models": [], "status": "running", "lean_error": "", "feedback": "", "missing_identifiers": []}
        try:
            final = self.graph.invoke(init, config={"recursion_limit": 60})
            # ACC-1: "error" (excepción capturada dentro de un nodo, ver `_guard`) es un estado
            # terminal distinto de "fail" (el grafo llegó a FAIL de forma normal); cualquier otro
            # valor (no debería darse aquí) cae a "fail" por seguridad.
            status = final.get("status") if final.get("status") in ("done", "error") else "fail"
        except (DailyQuotaExhausted, ProverUnavailable):
            raise  # sin modelos disponibles hoy o sin demostrador: el runner corta la corrida y se reanuda mañana
        except Exception as e:  # red de seguridad final: no debería alcanzarse con _guard ya en cada nodo
            final, status = {**init, "fail_reason": f"exception: {type(e).__name__}: {str(e)[:300]}"}, "error"
        return self.report(problem, cfg, final, status, time.time() - t0)

    @staticmethod
    def report(problem: Problem, cfg: dict, final: dict, status: str, wall: float) -> SolutionReport:
        used_ids = set(final.get("used_citations", []))
        proof = final.get("proof_tactics", "") if status == "done" else ""
        # `used_in_proof`: la constante está en el término que aceptó el kernel (REPL). Nombrarla en el texto no
        # basta: `grind +lax [16 lemas]` nombra 16 y usa uno o dos (smoke 2026-10-14, DECISIONS #101). Sin esa
        # información (backend `lake`), el criterio textual de antes.
        verdict = final.get("verdict")
        used_consts = set(verdict.used_constants) if status == "done" and verdict is not None and verdict.used_constants else None
        citations = []
        for c in final.get("evidence", {}).values():
            c = c.model_copy()
            c.used_in_sketch = c.citation_id in used_ids
            name = c.locator.get("decl_name") or ""
            c.used_in_proof = bool(proof) and c.corpus == "mathlib" and (name in used_consts if used_consts is not None else bool(identifiers_used(proof, [name])))
            citations.append(c)
        return SolutionReport(
            problem_id=problem.problem_id,
            config=cfg["name"],
            status=status,
            attempts=final.get("attempt", 1) if status == "done" else (len(final.get("history", [])) or 1),
            replans=final.get("replans", 0),
            llm_calls=final.get("llm_calls", 0),
            retrieval_calls=final.get("retrieval_calls", 0),
            tokens_in=final.get("tokens_in", 0),
            tokens_out=final.get("tokens_out", 0),
            wall_seconds=round(wall, 1),
            lean_code=final.get("lean_code", ""),
            verdict=final.get("verdict"),
            citations=citations,
            n_retrieved=len(citations),
            n_used_in_sketch=sum(c.used_in_sketch for c in citations),
            n_used_in_proof=sum(c.used_in_proof for c in citations),
            error_types=final.get("error_types", []) + ([final["fail_reason"]] if final.get("fail_reason") else []),
            re_retrievals=final.get("re_retrievals", 0),
            re_retrievals_ok=final.get("re_retrievals_ok", 0),
            models=final.get("models", []),
            solved_by=final.get("solved_by", "") if status == "done" else "",
            prover_samples=final.get("prover_samples", 0),
        )
