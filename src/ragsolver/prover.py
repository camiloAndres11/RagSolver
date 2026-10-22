"""Demostrador especializado en Lean servido en la GPU local (docs/PLAN_REDISENO_PROVER.md).

Goedel-Prover-V2-8B (Apache-2.0; 84.6 % pass@32 en miniF2F según su paper) vía `llama-server`
(OpenAI-compatible, Vulkan). Genera k pruebas en paralelo; de cada una se toma SÓLO el bloque de
tácticas del teorema pedido y se empalma en el enunciado del problema (el modelo nunca puede debilitar
el enunciado); cada candidato pasa por `LeanChecker.check` (validador + kernel + axiomas).

Autocorrección: el modelo se entrenó con Lean 4.9 y su Mathlib; el verificador usa v4.28, así que usa
nombres renombrados o borrados. Ante `unknown identifier/constant`, el RAG busca declaraciones parecidas
QUE EXISTEN en el entorno (índice + `#check`) y se las da al modelo junto con los errores de Lean.
Costo de API: 0 (todo local).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

PROMPT = """Complete the following Lean 4 code:

```lean4
{}```

Before producing the Lean 4 code to formally prove the given theorem, provide a detailed proof plan outlining the main proof steps and strategies.
The plan should highlight key ideas, intermediate lemmas, and proof structures that will guide the construction of the final formal proof."""

_FENCE_RE = re.compile(r"```(?:lean4|lean)?[ \t]*\n(.*?)```", re.S)
_UNKNOWN_RE = re.compile(r"[Uu]nknown (?:identifier|constant) [`']([^`']+)[`']")


def prover_url() -> str:
    """`RAGSOLVER_PROVER_URL` o el `llama-server` de Windows en el gateway de WSL, puerto 8082."""
    if os.environ.get("RAGSOLVER_PROVER_URL"):
        return os.environ["RAGSOLVER_PROVER_URL"].rstrip("/")
    gw = "127.0.0.1"
    try:
        for line in open("/proc/net/route"):
            f = line.split()
            if f[1] == "00000000":
                gw = ".".join(str(int(f[2][i : i + 2], 16)) for i in (6, 4, 2, 0))
                break
    except OSError:
        pass
    return f"http://{gw}:8082"


def lean_input(formal_statement: str, header: str) -> str:
    """El archivo que espera el modelo: imports, opens y el teorema con `sorry`."""
    stmt = re.sub(r":=\s*by\s+sorry\s*$|:=\s*sorry\s*$", "", formal_statement.rstrip()).rstrip()
    opens = "\n".join(l for l in header.splitlines() if l.strip() and not l.strip().startswith("import"))
    return f"import Mathlib\nimport Aesop\n\nset_option maxHeartbeats 400000\n\n{opens}\n\n{stmt} := by\n  sorry"


def extract_tactics(output: str, theorem_name: str) -> str | None:
    """Tácticas del teorema `theorem_name` en el último bloque de código de la salida, o None.
    Se descarta todo lo demás (enunciado, lemas auxiliares, comandos): el enunciado lo pone el problema."""
    for block in reversed(_FENCE_RE.findall(output)):
        m = re.search(rf"(?:theorem|lemma)\s+{re.escape(theorem_name)}\b.*?:=\s*by[ \t]*\n(.*)", block, re.S)
        if not m:
            continue
        lines = m.group(1).splitlines()
        body = []
        for line in lines:  # el cuerpo termina en la primera línea sin sangría (otra declaración, comando)
            if line.strip() and not line[0].isspace():
                break
            body.append(line)
        tactics = "\n".join(body).strip("\n")
        if tactics.strip():
            from textwrap import dedent

            return dedent(tactics)
    return None


def unknown_names(errors: list[dict]) -> list[str]:
    return list(dict.fromkeys(n for e in errors for n in _UNKNOWN_RE.findall(e.get("message", ""))))


class ProverUnavailable(RuntimeError):
    """El servidor del demostrador dejó de responder: el runner corta la corrida (como con la cuota diaria)."""


def local_prover(pcfg: dict) -> LocalProver | None:
    """El demostrador si `prover.enabled` y el servidor responde; si no, None (el sistema sigue sin él)."""
    if not pcfg.get("enabled"):
        return None
    p = LocalProver(max_tokens=pcfg.get("max_tokens", 12000), temperature=pcfg.get("temperature", 0.8))
    if p.alive():
        print(f"[prover] demostrador local en {p.url}", flush=True)
        return p
    print(f"[prover] AVISO: {p.url} no responde; se corre sin demostrador local", flush=True)
    return None


class LocalProver:
    def __init__(self, url: str | None = None, max_tokens: int = 12000, temperature: float = 0.8, timeout_s: float = 1800, cache_path: str | None = "data/eval/prover_cache.jsonl"):
        self.url = url or prover_url()
        self.max_tokens, self.temperature, self.timeout_s = max_tokens, temperature, timeout_s
        self.client = httpx.Client(timeout=timeout_s)
        self.tokens_out = 0
        self.calls = 0
        # caché en disco por (mensajes, semilla, parámetros): dos configuraciones que piden la misma muestra
        # (p.ej. `baseline_prover` y la etapa 0 de `full`) la generan una sola vez, y quedan pareadas
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, str] = {}
        self._lock = threading.Lock()
        if self.cache_path and self.cache_path.exists():
            for line in self.cache_path.open(encoding="utf-8"):
                if line.strip():
                    d = json.loads(line)
                    self._cache[d["key"]] = d["out"]

    def alive(self) -> bool:
        try:
            return self.client.get(f"{self.url}/health", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False

    def _chat(self, messages: list[dict], seed: int) -> str:
        key = hashlib.sha256(json.dumps([messages, seed, self.max_tokens, self.temperature], ensure_ascii=False).encode()).hexdigest()
        if key in self._cache:
            return self._cache[key]
        r = self.client.post(f"{self.url}/v1/chat/completions", json={"messages": messages, "max_tokens": self.max_tokens, "temperature": self.temperature, "top_p": 0.95, "seed": seed})
        r.raise_for_status()
        d = r.json()
        self.calls += 1
        self.tokens_out += (d.get("usage") or {}).get("completion_tokens", 0)
        msg = d["choices"][0]["message"]
        # el razonamiento (si el servidor lo separa) va ANTES: la extracción toma el último bloque de código
        out = ((msg.get("reasoning_content") or "") + "\n" + (msg.get("content") or "")).strip()
        with self._lock:
            self._cache[key] = out
            if self.cache_path:
                with self.cache_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"key": key, "out": out}, ensure_ascii=False) + "\n")
        return out

    def sample(self, messages: list[dict], k: int, seed0: int = 13) -> list[str]:
        with ThreadPoolExecutor(max_workers=k) as pool:
            outs = list(pool.map(lambda i: self._safe_chat(messages, seed0 + i), range(k)))
        # una muestra que falla se tolera; todas fallando con el servidor caído no son "muestras sin prueba":
        # la corrida se detiene y se reanuda (eval-v4: el servidor murió y 22 problemas quedaron como fallos falsos)
        if outs and all(o.startswith("(prover error") for o in outs) and not self.alive():
            raise ProverUnavailable(f"{self.url} no responde")
        return outs

    def _safe_chat(self, messages: list[dict], seed: int) -> str:
        try:
            return self._chat(messages, seed)
        except httpx.HTTPError as e:
            return f"(prover error: {type(e).__name__})"


def name_suggestions(names: list[str], checker, retriever=None, header: str = "", k: int = 4) -> tuple[str, dict[str, str]]:
    """RAG para la autocorrección: por cada nombre desconocido, declaraciones parecidas que SÍ existen en
    el entorno de Lean del verificador (búsqueda en el índice por los tokens del nombre + `#check`)."""
    lines, suggested = [], {}
    for n in names[:4]:
        cands: list[str] = []
        if retriever is not None:
            from ragsolver.rag.schema import RetrievalRequest

            try:
                res = retriever.search(RetrievalRequest(queries=[f"unknown identifier '{n}'"], mode="error", corpora=["mathlib"], k=k * 2, n_candidates=50, rerank=False, include_figures=False, context={"problem_id": "", "agent": "prover"}))
                cands = [h.metadata.get("decl_name") for h in res.hits if h.metadata.get("decl_name")]
            except Exception:
                cands = []
        sigs = checker.signatures(cands[: k * 2], header=header) if cands else {}
        suggested.update(list(sigs.items())[:k])
        if sigs:
            lines.append(f"`{n}` does not exist in this Mathlib version. Existing declarations with similar names:\n" + "\n".join(f"  {c} : {t[:200]}" for c, t in list(sigs.items())[:k]))
        else:
            lines.append(f"`{n}` does not exist in this Mathlib version.")
    return "\n".join(lines), suggested


def draft_hint(strategy: str, steps: list[str], sketch: str, lemmas: dict[str, str], max_sketch: int = 4000) -> str:
    """Draft-Sketch-Prove: el plan y la prueba informal del modelo de la API, más los lemas que recuperó el
    RAG (que existen en el Mathlib fijado), como guía dentro del prompt del demostrador."""
    parts = ["Here is an informal proof written by another model. It may contain mistakes: use it as guidance, not as ground truth.", f"Strategy: {strategy}"]
    parts += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    parts.append(f"Informal proof:\n{sketch[:max_sketch]}")
    if lemmas:
        parts.append("Mathlib lemmas that may be useful (they exist in the installed Mathlib version):\n" + "\n".join(f"  {n} : {t[:200]}" for n, t in lemmas.items()))
    return "\n\n".join(parts)


def examples_hint(hits: list[dict], max_proof: int = 1500) -> str:
    """RAG de pruebas (`rag/proofs.py`): pruebas verificadas de problemas parecidos como ejemplos en el prompt."""
    blocks = [f"```lean4\n{h['statement']} := by\n{textwrap.indent(h['proof'][:max_proof], '  ')}\n```" for h in hits]
    return "Verified Lean 4 proofs of similar problems (older Mathlib: some names may have changed). Reuse their techniques and hints where they fit:\n\n" + "\n\n".join(blocks)


def prove(prover: LocalProver, checker, formal_statement: str, header: str, theorem_name: str, k: int = 4, correction_rounds: int = 1, retriever=None, max_heartbeats: int = 400000, batch: int = 4, hint: str = "") -> tuple[str | None, dict]:
    """k muestras en lotes de `batch` en paralelo, con salida temprana: la primera que pasa `check` gana
    (los problemas fáciles no pagan las k). Si ninguna, una ronda de autocorrección sobre las (hasta) 2
    muestras con tácticas extraíbles: errores de Lean + sugerencias de nombres (RAG, si `retriever`).
    `hint` (Draft-Sketch-Prove, `draft_hint`) se agrega al prompt; el prompt cambia, así que sus muestras
    no comparten caché con las de la etapa 0."""
    t0 = time.time()
    user = PROMPT.format(lean_input(formal_statement, header)) + (f"\n\n{hint}" if hint else "")
    log = {"samples": 0, "extracted": 0, "rounds": 0, "corrections_with_rag": 0, "suggested": {}}
    pending, failed = [], []  # failed: todo candidato extraíble que Lean rechazó, para REPAIR (graph.py)
    for start in range(0, k, batch):
        outs = prover.sample([{"role": "user", "content": user}], min(batch, k - start), seed0=13 + start)
        log["samples"] += len(outs)  # generadas (trabajo de GPU), no sólo las revisadas antes del éxito
        for out in outs:
            tac = extract_tactics(out, theorem_name)
            if not tac:
                continue
            log["extracted"] += 1
            v = checker.check(formal_statement, tac, header, max_heartbeats)
            if v.ok:
                log["seconds"] = round(time.time() - t0, 1)
                return tac, {**log, "solved_in": "sample"}
            pending.append((out, tac, v))
            failed.append((tac, v))
    for rnd in range(correction_rounds):
        if not pending:
            break
        log["rounds"] += 1
        nxt = []
        for out, tac, v in pending[:2]:
            err = "\n".join(f"line {e['line']}: {e['message'][:400]}" for e in v.errors[:6])
            names = unknown_names(v.errors)
            hint, sugg = name_suggestions(names, checker, retriever, header) if names else ("", {})
            log["suggested"].update(sugg)
            log["corrections_with_rag"] += bool(sugg)
            fix = f"The proof failed to compile in Lean 4 (recent Mathlib). Errors:\n{err}\n" + (f"\n{hint}\n" if hint else "") + "\nFix the proof and output the complete corrected Lean 4 code."
            answer = out.rsplit("</think>", 1)[-1][-6000:]  # la respuesta final, sin el razonamiento
            outs2 = prover.sample([{"role": "user", "content": user}, {"role": "assistant", "content": answer}, {"role": "user", "content": fix}], 1, seed0=100 + rnd)
            tac2 = extract_tactics(outs2[0], theorem_name)
            if not tac2:
                continue
            v2 = checker.check(formal_statement, tac2, header, max_heartbeats)
            if v2.ok:
                log["seconds"] = round(time.time() - t0, 1)
                return tac2, {**log, "solved_in": f"correction{rnd + 1}"}
            nxt.append((outs2[0], tac2, v2))
            failed.append((tac2, v2))
        pending = nxt
    log["seconds"] = round(time.time() - t0, 1)
    # casi-pruebas para REPAIR, menos errores primero: Lean sigue elaborando tras un `have` que falla, así que
    # ≤ 2 errores ≈ ≤ 2 pasos malos; con más, reparar paso a paso cuesta horas de GPU (smoke 2026-10-21)
    log["candidates"] = [t for t, v in sorted(failed, key=lambda x: len(x[1].errors)) if len(v.errors) <= 2]
    return None, log


_DAGGER_RE = re.compile(r"([A-Za-z_][\w']*)✝(\d*)")


def goal_to_theorem(goal: str, name: str) -> str | None:
    """Un goal pretty-printed del REPL ("a b : ℝ\\nh : 0 < a\\n⊢ T") como teorema autónomo, para pedírselo
    al demostrador (receta de DeepSeek-Prover-V2: cada subobjetivo como lema). Los nombres inaccesibles
    (`x✝`) se renombran; las instancias (`inst✝ : Fact p`) pasan a `[Fact p]`. None si no se puede."""
    lines = [l for l in goal.splitlines() if l.strip() and not l.startswith("case ")]
    if not lines or "⊢" not in goal:
        return None
    hyps, target = [], None
    for i, l in enumerate(lines):
        if l.startswith("⊢"):
            target = " ".join([l[1:].strip()] + [x.strip() for x in lines[i + 1 :]])
            break
        if l[0].isspace() and hyps:  # continuación de la hipótesis anterior
            hyps[-1] += " " + l.strip()
        else:
            hyps.append(l.strip())
    if target is None:
        return None
    binders = []
    for h in hyps:
        if " : " not in h:
            return None
        names, typ = h.split(" : ", 1)
        if all(n.startswith("inst✝") for n in names.split()):
            binders.append(f"[{typ}]")
        else:
            binders.append(f"({names} : {typ})")
    stmt = f"theorem {name} " + " ".join(binders) + f" : {target} := by sorry"
    return _DAGGER_RE.sub(lambda m: f"{m.group(1)}_{m.group(2) or 0}", stmt)


def renamed_inaccessible(goal: str) -> list[str]:
    """Nombres que `goal_to_theorem` dio a las hipótesis inaccesibles (no instancias), en orden: un
    bloque probado para el lema autónomo funciona en el contexto original con `rename_i <estos>` delante."""
    names = []
    for l in goal.split("⊢", 1)[0].splitlines():
        if " : " in l and not l[0].isspace():
            for n in l.split(" : ", 1)[0].split():
                m = _DAGGER_RE.fullmatch(n)
                if m and not n.startswith("inst✝"):
                    names.append(f"{m.group(1)}_{m.group(2) or 0}")
    return names
