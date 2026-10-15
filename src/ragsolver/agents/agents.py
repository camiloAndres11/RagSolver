"""Agentes (Rol A) — Planificador, Generador, Autoformalizador, Crítico y las dos
líneas base de la propuesta §8.2. Cada uno: arma un prompt (configs/agents.yaml),
hace UNA llamada LLM con salida JSON validada por Pydantic y devuelve el modelo.

El contexto de recuperación llega ya formateado (docs/PLAN_RAG.md Fase 4, tarea 4):
lemas como `[M1] name : signature — description (module)`; ejemplares como
`[I1] statement — key idea — source, page`.
"""

from __future__ import annotations

import json
import re
import textwrap
import time

import yaml

from ragsolver.agents.schemas import BaselineAOutput, BaselineBOutput, CriticOutput, FillOutput, FormalizerOutput, GeneratorOutput, PlannerOutput, Problem
from ragsolver.llm import LLM, LLMResponse
from ragsolver.logging.jsonl_writer import append_event
from ragsolver.rag.retrieve import mathlib_table_name
from ragsolver.rag.schema import Hit, TraceEvent, VersionsPin


def load_agents_config(path: str = "configs/agents.yaml") -> dict:
    return yaml.safe_load(open(path, encoding="utf-8"))


def format_lemmas(hits: list[Hit]) -> str:
    lines = []
    for h in hits:
        m = h.metadata
        desc = (m.get("informal_description") or "")[:300].replace("\n", " ")
        line = f"[{h.citation.citation_id}] {m.get('decl_name')} : {(m.get('signature') or '')[:400]}"
        lines.append(line + (f" — {desc}" if desc else "") + (f" ({m.get('module_path')})" if m.get("module_path") else ""))
    return "\n".join(lines) or "(none)"


def format_exemplars(hits: list[Hit]) -> str:
    lines = []
    for h in hits:
        m = h.metadata
        stmt = (h.content_md or h.text).split("## Solución")[0].strip().replace("\n", " ")[:500]
        idea = (m.get("context_summary") or "")[:400].replace("\n", " ")
        figs = "; ".join((f.get("description") or "")[:200] for f in h.figures) if h.figures else ""
        lines.append(f"[{h.citation.citation_id}] {stmt} — {idea} — {h.citation.label}" + (f" — figure: {figs}" if figs else ""))
    return "\n".join(lines) or "(none)"


class InvalidLLMOutput(Exception):
    """El modelo no devolvió JSON válido ni en el reintento (p.ej. generación degenerada hasta
    `max_tokens`): el problema cuenta como fallo del sistema, no como error de infraestructura."""

    def __init__(self, agent: str, tokens_in: int, tokens_out: int, calls: int, model: str):
        super().__init__(f"{agent}: salida JSON inválida tras {calls} llamadas")
        self.agent, self.tokens_in, self.tokens_out, self.calls, self.model = agent, tokens_in, tokens_out, calls, model


class Agents:
    def __init__(self, llm: LLM, config: dict | None = None, trace_dir: str = "data/eval/traces", versions: dict | None = None):
        self.llm = llm
        self.cfg = config or load_agents_config()
        self.trace_dir = trace_dir
        self.versions = versions or yaml.safe_load(open("configs/versions.yaml", encoding="utf-8"))

    def _versions_pin(self) -> VersionsPin:
        return VersionsPin(
            embedding_model=self.versions["embedding"]["model"],
            reranker_model=self.versions["reranker"]["model"],
            index_version=self.versions["index"]["version"],
            mathlib_tag=self.versions["lean"]["mathlib_tag"],
            mathlib_table=mathlib_table_name(),
        )

    def _call(self, agent: str, prompt: str, schema_model, run_id: str, problem_id: str, attempt: int, images=()):
        t0 = time.time()
        resp = self.llm.generate(prompt, schema=schema_model.model_json_schema(), images=images, system=self.cfg["prompts"]["system"])
        try:
            out = schema_model.model_validate_json(resp.text)
        except ValueError:  # JSON truncado por `max_tokens` o inválido: un reintento con el doble de tokens (cuenta en el presupuesto)
            resp2 = self.llm.generate(prompt, schema=schema_model.model_json_schema(), images=images, system=self.cfg["prompts"]["system"], max_tokens=2 * int(getattr(self.llm, "max_tokens", None) or 4000))
            resp = LLMResponse(text=resp2.text, model=resp2.model, tokens_in=resp.tokens_in + resp2.tokens_in, tokens_out=resp.tokens_out + resp2.tokens_out, calls=resp.calls + resp2.calls, cost_usd=resp.cost_usd + resp2.cost_usd, attempts=resp.attempts + resp2.attempts)
            try:
                out = schema_model.model_validate_json(resp2.text)
            except ValueError as e:
                # ACC-1: el evento del intento roto lleva sus propios llm_calls/tokens/costo (antes
                # `self.trace()` los ponía siempre en 0, aunque sí se habían gastado).
                append_event(
                    TraceEvent(
                        run_id=run_id,
                        problem_id=problem_id,
                        attempt=attempt,
                        agent=agent,
                        latency_ms=(time.time() - t0) * 1000,
                        llm_calls=resp.calls,
                        tokens=resp.tokens_in + resp.tokens_out,
                        request={"model": resp.model, "tokens_in": resp.tokens_in, "tokens_out": resp.tokens_out, "cost_usd": round(resp.cost_usd, 6), "attempts": resp.attempts, "decision": {"invalid_json": str(e)[:200]}},
                        versions=self._versions_pin(),
                    ),
                    base_dir=self.trace_dir,
                )
                raise InvalidLLMOutput(agent, resp.tokens_in, resp.tokens_out, resp.calls, resp.model) from e
        # RF-7: decisión tomada por el agente (salida validada, truncada) junto a tokens y latencia
        decision = {k: (v[:600] if isinstance(v, str) else v) for k, v in out.model_dump().items()}
        append_event(
            TraceEvent(
                run_id=run_id,
                problem_id=problem_id,
                attempt=attempt,
                agent=agent,
                latency_ms=(time.time() - t0) * 1000,
                llm_calls=1,
                tokens=resp.tokens_in + resp.tokens_out,
                request={"model": resp.model, "prompt_chars": len(prompt), "prompt_version": str(self.cfg["prompt_version"]), "tokens_in": resp.tokens_in, "tokens_out": resp.tokens_out, "cost_usd": round(resp.cost_usd, 6), "attempts": resp.attempts, "decision": decision},
                versions=self._versions_pin(),
            ),
            base_dir=self.trace_dir,
        )
        return out, resp

    def trace(self, agent: str, run_id: str, problem_id: str, attempt: int, latency_ms: float, decision: dict) -> None:
        """Evento de traza para pasos sin LLM (verificador): veredicto + latencia (RF-5, RF-7)."""
        append_event(TraceEvent(run_id=run_id, problem_id=problem_id, attempt=attempt, agent=agent, latency_ms=latency_ms, llm_calls=0, tokens=0, request={"decision": decision}), base_dir=self.trace_dir)

    def plan(self, problem: Problem, exemplars: list[Hit], run_id: str, attempt: int, feedback: str = "") -> tuple[PlannerOutput, object]:
        prompt = self.cfg["prompts"]["planner"].format(informal=problem.informal_statement, formal=problem.formal_statement, exemplars=format_exemplars(exemplars), feedback=feedback or "(none)")
        return self._call("planner", prompt, PlannerOutput, run_id, problem.problem_id, attempt)

    def generate(self, problem: Problem, plan: PlannerOutput, lemmas: list[Hit], exemplars: list[Hit], feedback: str, run_id: str, attempt: int) -> tuple[GeneratorOutput, object]:
        plan_txt = f"Strategy: {plan.strategy}\n" + "\n".join(f"{s.subgoal_id}. {s.statement_nl} (hint: {s.formal_hint})" for s in plan.subgoals)
        images = [f["image_path"] for h in exemplars[:2] for f in h.figures[:1] if f.get("image_path")] if self.cfg.get("llm_supports_vision") else []
        prompt = self.cfg["prompts"]["generator"].format(informal=problem.informal_statement, formal=problem.formal_statement, plan=plan_txt, lemmas=format_lemmas(lemmas), exemplars=format_exemplars(exemplars), feedback=feedback or "(none)")
        n = int(self.cfg.get("generator_samples", 1) or 1)
        if n <= 1:
            return self._call("generator", prompt, GeneratorOutput, run_id, problem.problem_id, attempt, images=images)
        samples = [self._call("generator", prompt, GeneratorOutput, run_id, problem.problem_id, attempt, images=images) for _ in range(n)]
        best = max(range(n), key=lambda i: sum(_jaccard(samples[i][0].sketch, samples[j][0].sketch) for j in range(n) if j != i))
        out, resp = samples[best]
        # BUG-5: cost_usd y attempts de las n muestras también se suman (antes se perdían: costaba
        # como si cada muestra hubiese costado 0 y 1 intento, aunque alguna hubiese reintentado).
        resp = type(resp)(resp.text, resp.model, sum(r.tokens_in for _, r in samples), sum(r.tokens_out for _, r in samples), calls=n, cost_usd=sum(r.cost_usd for _, r in samples), attempts=sum(r.attempts for _, r in samples))
        return out, resp

    def formalize(self, problem: Problem, sketch: str, lemmas: list[Hit], lean_error: str, run_id: str, attempt: int, skeleton: bool = False, proved: list[str] = ()) -> tuple[FormalizerOutput, object]:
        if skeleton:  # docs/PLAN_MEJORA_RAG.md Fase 2: `have … := by sorry` por afirmación; los goals se cierran después
            prompt = self.cfg["prompts"]["formalizer_skeleton"].format(formal=problem.formal_statement, sketch=sketch, lemmas=format_lemmas(lemmas), proved="\n".join(proved) or "(none)", lean_error=lean_error or "(none)")
        else:
            prompt = self.cfg["prompts"]["formalizer"].format(formal=problem.formal_statement, sketch=sketch, lemmas=format_lemmas(lemmas), lean_error=lean_error or "(none)")
        out, resp = self._call("formalizer", prompt, FormalizerOutput, run_id, problem.problem_id, attempt)
        out.proof_tactics = strip_code_fences(out.proof_tactics)
        return out, resp

    def fill(self, problem: Problem, goals_txt: str, run_id: str, attempt: int) -> tuple[FillOutput, object]:
        prompt = self.cfg["prompts"]["fill"].format(formal=problem.formal_statement, goals=goals_txt)
        out, resp = self._call("fill", prompt, FillOutput, run_id, problem.problem_id, attempt)
        for p in out.proofs:
            p.tactics = strip_code_fences(p.tactics)
        return out, resp

    def critique(self, problem: Problem, sketch: str, lean_code: str, lean_error: str, history: list[dict], run_id: str, attempt: int) -> tuple[CriticOutput, object]:
        hist = "\n".join(f"attempt {h['attempt']}: {h.get('error_type', '?')} — {h.get('summary', '')[:200]}" for h in history) or "(first attempt)"
        prompt = self.cfg["prompts"]["critic"].format(formal=problem.formal_statement, sketch=sketch, lean_code=lean_code, lean_error=lean_error, history=hist)
        return self._call("critic", prompt, CriticOutput, run_id, problem.problem_id, attempt)

    def baseline(self, mode: str, problem: Problem, run_id: str) -> tuple[BaselineAOutput | BaselineBOutput, object]:
        prompt = self.cfg["prompts"][mode].format(formal=problem.formal_statement, informal=problem.informal_statement)
        out, resp = self._call(mode, prompt, BaselineAOutput if mode == "baseline_a" else BaselineBOutput, run_id, problem.problem_id, 1)
        if mode == "baseline_a":
            out.proof_tactics = strip_code_fences(out.proof_tactics)
        return out, resp


def _jaccard(a: str, b: str) -> float:
    ta, tb = set(re.findall(r"[\w^+\-*/=<>≤≥]+", a.lower())), set(re.findall(r"[\w^+\-*/=<>≤≥]+", b.lower()))
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def strip_code_fences(code: str) -> str:
    """Quita vallas ```lean, un enunciado repetido, y la sangría común (el LLM suele traer las
    tácticas ya indentadas a 2 espacios; `splice_proof` las vuelve a indentar, y si sólo la
    primera línea perdía la sangría el bloque quedaba mal anidado y Lean daba `unsolved goals`)."""
    code = re.sub(r"^\s*```[a-zA-Z0-9]*\n?", "", code.rstrip())
    code = re.sub(r"\n?```$", "", code)
    # si el modelo repitió el enunciado, quedarse con lo que sigue a `:= by`
    m = re.search(r":=\s*by\s*\n", code)
    if code.lstrip().startswith(("theorem", "lemma", "example")) and m:
        code = code[m.end():]
    code = re.sub(r"^\s*(?::=\s*)?by[ \t]*\n", "", code)  # el modelo a veces empieza con un `by` suelto (8/30 intentos en eval_v1): sobra, el empalme ya lo pone
    code = textwrap.dedent(code.strip("\n")).strip("\n")
    # la primera línea llega sin sangría y las demás con la del editor del modelo ("rcases h1 …\n  rcases h2 …",
    # smoke 2026-10-14: Lean daba `unexpected identifier`): si la primera línea no abre un bloque, el resto
    # baja a su nivel
    lines = code.splitlines()
    rest = [l for l in lines[1:] if l.strip()]
    if rest and not lines[0].startswith("calc") and not re.search(r"(\bby|:=|=>|\bwith|\bdo|\bthen|\belse|\bfrom|[,(⟨\[])\s*$", lines[0]):
        k = min(len(l) - len(l.lstrip()) for l in rest)
        if k:
            code = "\n".join([lines[0]] + [l[k:] if l.strip() else l for l in lines[1:]])
    return code
