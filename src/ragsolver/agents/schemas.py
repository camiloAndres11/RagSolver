"""Contratos JSON Schema de los agentes (RNF-4; docs/PLAN_RAG.md §8.3, propuesta §6.3).

Cada agente es una función pura `state -> delta` cuya salida LLM se valida contra
uno de estos modelos (`model_json_schema()` va como `response_format` al LLM).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ragsolver.rag.schema import Citation

ErrorType = Literal["lean_syntax", "missing_lemma", "misformalization", "timeout", "planning", "unknown"]
CriticAction = Literal["retry", "replan", "abandon"]


class Problem(BaseModel):
    problem_id: str
    source: str = "trivial"  # trivial | minif2f_v2 | putnambench
    split: str = "dev"
    informal_statement: str
    formal_statement: str  # `theorem foo ... := by sorry`
    area: str = ""
    difficulty: int | None = None
    imports: list[str] = Field(default_factory=list)  # imports adicionales al de configs/agents.yaml
    header: str = ""  # p.ej. `open BigOperators Real Nat Topology` (miniF2F); sin imports


class Subgoal(BaseModel):
    subgoal_id: str
    statement_nl: str
    formal_hint: str = ""
    search_queries: list[str] = Field(default_factory=list, max_length=3)
    math_area: str = ""


class PlannerOutput(BaseModel):
    strategy: str
    subgoals: list[Subgoal] = Field(min_length=1, max_length=6)


class GeneratorOutput(BaseModel):
    sketch: str
    used_citations: list[str] = Field(default_factory=list)  # ids M*/I*


class FormalizerOutput(BaseModel):
    proof_tactics: str  # sólo el cuerpo tras `:= by` — el enunciado no lo toca el LLM
    notes: str = ""


class GoalProof(BaseModel):
    goal_id: str  # "G1", "G2", … en el orden en que se le mostraron los goals
    tactics: str  # bloque que reemplaza el `sorry` de ese goal


class FillOutput(BaseModel):
    """FILL (docs/PLAN_MEJORA_RAG.md §4.1): pruebas de los goals que la batería CLOSE no cerró."""

    proofs: list[GoalProof] = Field(default_factory=list)


class CriticOutput(BaseModel):
    error_type: ErrorType
    action: CriticAction
    missing_identifiers: list[str] = Field(default_factory=list)
    feedback: str


class BaselineAOutput(BaseModel):
    """Línea base A: un solo LLM escribe la prueba Lean. Sólo el campo que la línea base produce, y
    obligatorio: con un esquema común de campos opcionales DeepSeek dejaba `proof_tactics` vacío y
    escribía la prueba en lenguaje natural (58/60 pruebas vacías, DECISIONS #65)."""

    proof_tactics: str  # como FormalizerOutput: sólo el cuerpo tras `:= by`
    notes: str = ""


class BaselineBOutput(BaseModel):
    """Línea base B: prueba en lenguaje natural + autoevaluación (sin verificación)."""

    natural_language_proof: str
    self_assessed_correct: bool


class Verdict(BaseModel):
    ok: bool
    errors: list[dict] = Field(default_factory=list)  # {line, col, message}
    uses_sorry: bool = False
    axioms: list[str] = Field(default_factory=list)  # VER-1: `#print axioms`; ok=True exige subconjunto de STANDARD_AXIOMS
    used_constants: list[str] = Field(default_factory=list)  # constantes del término aceptado (REPL `all_tactics`): `used_in_proof` real
    seconds: float = 0.0
    lean_file: str = ""
    raw: str = ""


class SolutionReport(BaseModel):
    problem_id: str
    config: str
    status: Literal["done", "fail", "error"]
    attempts: int = 0
    replans: int = 0
    llm_calls: int = 0
    retrieval_calls: int = 0  # búsquedas del retriever (0 llamadas LLM cada una)
    tokens_in: int = 0
    tokens_out: int = 0
    wall_seconds: float = 0.0
    lean_code: str = ""
    verdict: Verdict | None = None
    citations: list[Citation] = Field(default_factory=list)  # con used_in_proof / used_in_sketch resueltos
    n_retrieved: int = 0
    n_used_in_sketch: int = 0
    n_used_in_proof: int = 0
    error_types: list[str] = Field(default_factory=list)  # taxonomía por intento
    re_retrievals: int = 0
    re_retrievals_ok: int = 0
    models: list[str] = Field(default_factory=list)
    self_assessed_correct: bool | None = None  # sólo línea base B
    solved_by: str = ""  # prover_root | prover_goal | agents | auto (docs/PLAN_REDISENO_PROVER.md)
    prover_samples: int = 0  # muestras del demostrador local (0 API)
