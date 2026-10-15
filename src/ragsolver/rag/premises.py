"""Premisas por goal de Lean (docs/PLAN_MEJORA_RAG.md Fases 1, 3 y 4).

El input del retrieval es el goal pretty-printed que devuelve el REPL en cada `sorry`, no el
enunciado en lenguaje natural:
- canal A, prior de competición: lemas por frecuencia en pruebas de Lean Workbook
  (`scripts/build_premise_prior.py`), filtrados por los símbolos del goal;
- canal B, selección nativa de Lean: se consume dentro de CLOSE (`grind +suggestions`), no como
  lista de nombres (su lista para goals de competición es ruido: `not_fermat_42`, `BitVec.*`);
- canal C, índice denso actual con el goal como consulta (`Retriever`, modo `formal`).
A y C se fusionan con RRF. Todo cuesta 0 llamadas LLM.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from itertools import combinations
from pathlib import Path

from ragsolver.rag.index import rrf_fuse

PRIOR_PATH = "data/corpus/premise_prior.jsonl"

IDENT_RE = re.compile(r"(?<![\w.'])([A-Za-z_][\w']*(?:\.[A-Za-z_][\w']*)*)")
# palabras de tácticas y sintaxis que el regex de identificadores confunde con premisas
TACTIC_WORDS = {
    "by", "have", "exact", "apply", "intro", "intros", "rcases", "obtain", "refine", "refine'", "constructor", "simp", "simp_all", "norm_num", "linarith", "nlinarith",
    "omega", "ring", "ring_nf", "field_simp", "positivity", "decide", "rfl", "use", "cases", "cases'", "induction", "induction'", "rw", "calc", "show", "fun", "at", "with",
    "this", "norm_cast", "push_cast", "exact_mod_cast", "interval_cases", "by_contra", "by_cases", "push_neg", "split_ifs", "gcongr", "aesop", "tauto", "zify", "qify",
    "simp_arith", "rw_mod_cast", "apply_fun", "native_decide", "nlinarith!", "linarith!", "contradiction", "exfalso", "assumption", "trivial", "specialize", "subst",
}
# `foo.mpr`, `foo.symm.le`: el lema es `foo`
FIELD_SUFFIXES = {"mp", "mpr", "symm", "le", "lt", "ne", "ne'", "trans", "elim", "1", "2", "resolve_left", "resolve_right", "out", "mono", "ge", "gt", "not_lt", "not_le", "trans_le", "trans_lt"}
# constantes de plomería que aparecen en `used_constants` de cualquier `rw`/`simp`/`linarith` y no son premisas
PLUMBING_PREFIXES = ("Mathlib.Tactic.", "Mathlib.Meta.", "Lean.", "Std.Tactic.", "Aesop.", "Linarith.", "Nat.Linear.", "Int.Linear.", "Mathlib.Algebra.Order.Field.Basic.")
PLUMBING = {"Eq.mpr", "Eq.mp", "congrArg", "congrFun", "congr", "id", "Eq.refl", "rfl", "of_eq_true", "eq_self", "propext", "Eq.trans", "Iff.refl", "trivial", "True.intro", "Eq.rec", "Eq.ndrec", "absurd", "Classical.byContradiction", "Decidable.byContradiction", "Not.intro", "eq_true", "eq_false", "False.elim", "id_eq", "Iff.intro", "And.intro"}
_RELATION_RE = re.compile(r"[=≤<≥>≠↔∣∈∉¬∧∨]|\bFalse\b|\bTrue\b")
# símbolos que "anclan" un lema a un dominio: si el lema los menciona y el goal no, no aplica
SALIENT_RE = re.compile(r"[A-Z][\w']*(?:\.[\w']+)+|√|∑|∏|∣|%|!|⌊|⌈|ℕ|ℤ|ℝ|ℚ|ℂ|π|\blogb\b|\bchoose\b|\bgcd\b|\blcm\b|\babs\b|\|")
_GENERIC_SALIENT = {"Nat.cast", "Int.cast", "OfNat.ofNat", "HPow.hPow", "HMul.hMul", "HAdd.hAdd", "HSub.hSub", "HDiv.hDiv"}


def strip_field(name: str, known: set[str] | None = None) -> str:
    """`sub_ne_zero.mpr` -> `sub_ne_zero` (si `known` se da, sólo cuando la base está en `known`)."""
    parts = name.split(".")
    while len(parts) > 1 and parts[-1] in FIELD_SUFFIXES:
        parts.pop()
        if known is not None and ".".join(parts) in known:
            return ".".join(parts)
    return ".".join(parts) if known is None else name


def is_lemma_type(type_str: str) -> bool:
    """Heurística: el tipo de un lema es una proposición (relación, conectivo); el de una definición
    (`ℝ → ℝ`, `ℕ → ℕ → Prop`) no. ponytail: textual; `#print` exacto si aparecen falsos positivos."""
    t = type_str.strip()
    return bool(t) and not t.endswith("Prop") and not t.endswith("Type") and bool(_RELATION_RE.search(t))


def is_premise_constant(name: str) -> bool:
    return bool(name) and name not in PLUMBING and not name.startswith(PLUMBING_PREFIXES) and "._" not in name and ".match_" not in name and ".proof_" not in name and not name.split(".")[-1].startswith("inst")


def proof_tokens(proof: str) -> set[str]:
    """Identificadores candidatos a premisa en el cuerpo de una prueba (tras `:= by`), sin
    tácticas ni accesos a campo; la validación contra Lean la hace quien llama."""
    body = proof.split(":= by", 1)[1] if ":= by" in proof else proof
    return {strip_field(t) for t in IDENT_RE.findall(body) if t not in TACTIC_WORDS and ("_" in t or "." in t) and len(t) > 3}


def goal_target(goal: str) -> str:
    """La meta de un goal pretty-printed (lo que sigue al último `⊢`)."""
    return goal.rsplit("⊢", 1)[-1].strip()


def salient(text: str) -> set[str]:
    return {s for s in SALIENT_RE.findall(text) if s not in _GENERIC_SALIENT}


@lru_cache(maxsize=2)
def load_prior(path: str = PRIOR_PATH) -> tuple[dict, ...]:
    p = Path(path)
    if not p.exists():
        return ()
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in rows:
        r["_salient"] = frozenset(salient(r.get("type", "")))
    return tuple(rows)


def prior_channel(goal: str, k: int = 32, prior: tuple[dict, ...] | None = None, filtered: bool = True) -> list[str]:
    """Canal A: top-k del prior. `filtered`: descarta los lemas anclados a símbolos que el goal no
    tiene (un lema de `√` no sirve para un goal de `%`); los genéricos (`sq_nonneg`) pasan siempre."""
    prior = load_prior() if prior is None else prior
    if not filtered:
        return [r["decl_name"] for r in prior[:k]]
    g = salient(goal)
    return [r["decl_name"] for r in prior if r["_salient"] <= g][:k]


def nlinarith_hints(goal: str, max_hints: int = 12) -> list[str]:
    """Instancias de los lemas más frecuentes del prior (`sq_nonneg`, `mul_pos`) con las variables
    del goal — el patrón `nlinarith [sq_nonneg (a - b), …]` de las pruebas de competición."""
    hyps = [l for l in goal.split("⊢", 1)[0].splitlines() if " : " in l]
    num_vars, pos_hyps = [], []
    for l in hyps:
        names, typ = l.split(" : ", 1)
        typ = typ.strip()
        if typ in ("ℝ", "ℚ", "ℤ", "ℕ"):
            num_vars += names.split()
        elif re.fullmatch(r"0 < [\w']+", typ) or re.fullmatch(r"[\w']+ > 0", typ):
            pos_hyps += names.split()
    hints = [f"sq_nonneg ({a} - {b})" for a, b in combinations(num_vars, 2)]
    hints += [f"sq_nonneg ({a} + {b})" for a, b in combinations(num_vars, 2)]
    hints += [f"mul_pos {a} {b}" for a, b in combinations(pos_hyps, 2)]
    hints += [f"sq_nonneg {a}" for a in num_vars]
    return hints[:max_hints]


def fuse(*ranked: list[str], k: int = 16) -> list[str]:
    """RRF de los canales (nombres de declaración), sin duplicados."""
    return [n for n, _ in rrf_fuse([r for r in ranked if r], k=60)][:k]


def log_prior(freq: int) -> float:
    return math.log(max(freq, 1))
