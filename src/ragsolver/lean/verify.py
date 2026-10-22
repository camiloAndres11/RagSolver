"""Verificador (Rol B): el kernel de Lean 4 es la única fuente de verdad.

Modelo de amenazas (docs/AUDIT_2026-09-23.md SEC-1/VER-1; niveles de
https://lean-lang.org/doc/reference/latest/ValidatingProofs/). El texto de
tácticas lo escribe un LLM que puede venir de un proveedor no confiable
(DECISIONS #62 enruta a varios) o estar contaminado por un corpus de terceros
(prompt injection). Antes de mandarlo a Lean:
  1. `find_forbidden_tactic` (nivel 1: nada de comandos/metaprogramación)
     rechaza `#comando`, metaprogramación, declaraciones nuevas, cambios de
     entorno y caminos que no pasan por el kernel (`sorry`, `native_decide`,
     `decide +native`). El Autoformalizador sólo puede devolver tácticas.
  2. Tras un veredicto sin errores, se audita `#print axioms <nombre>`
     (nivel 2): sólo se acepta si usa como mucho `propext`, `Classical.choice`
     y `Quot.sound` — si aparece `Lean.trustCompiler`/`Lean.ofReduceBool` hubo
     evaluación nativa, no verificación del kernel (Kimina-Prover tuvo 15/225
     soluciones así: github.com/MoonshotAI/Kimina-Prover-Preview/issues/11).
No hay sandbox de sistema operativo todavía (Fase 10 de docs/PLAN_CORRECCIONES.md):
esto no protege contra bugs del elaborador de Lean en sí, ni limita el uso de
CPU/disco de una táctica válida pero cara (el heartbeat sólo limita pasos de
elaboración).

Dos backends con el mismo `Verdict`:
- `LeanChecker` (por defecto): REPL persistente vía `lean-interact` — `import Mathlib`
  una sola vez (~3-5 min) y después ~0.3 s por teorema (medido, docs/DECISIONS.md #40).
- `verify(...)`: un proceso `lake env lean` por verificación (sin estado; ~3 min
  cada uno por el import). Respaldo y prueba de humo.

El enunciado NUNCA lo escribe el LLM: se toma del problema y sólo se sustituye
`sorry` por las tácticas — así el LLM no puede debilitar el teorema.
"""

from __future__ import annotations

import os
import re
import textwrap
import threading
import subprocess
import time
from pathlib import Path

from ragsolver.agents.schemas import Verdict

_MSG_RE = re.compile(r"^(?P<file>[^\n:]+\.lean):(?P<line>\d+):(?P<col>\d+): (?P<sev>error|warning|info): ?(?P<msg>.*)$")

# ---------------------------------------------------------------------------
# Validación de tácticas (SEC-1). El Autoformalizador sólo debe devolver el
# bloque tras `:= by` (docs/PLAN_RAG.md §6, Fase 4 tarea 4); cualquier otra
# cosa es sospechosa.
# ---------------------------------------------------------------------------

_COMMENT_RE = re.compile(r"--[^\n]*|/-.*?-/", re.S)
# `open Foo in <táctica>` sólo trae un nombre a escala: no ejecuta código, se permite.
_SAFE_OPEN_IN_RE = re.compile(r"\bopen(?:\s+[A-Za-z_][\w'.]*)+\s+in\b")
# `#eval`/`#print`/... en CUALQUIER posición, no sólo al inicio de línea: `open X in
# #eval ...` es sintaxis válida de Lean y sería un bypass de un chequeo anclado a línea.
_HASH_CMD_RE = re.compile(r"#[A-Za-z_]")
_SORRY_RE = re.compile(r"(?<![\w'])sorry(?![\w'])")
_NATIVE_DECIDE_RE = re.compile(r"\bdecide\s*\+\s*native\b")
_IO_RE = re.compile(r"\bIO\.")
_DENIED_WORD_RE = re.compile(
    r"(?<![\w'])("
    r"sorry|admit|native_decide|axiom|opaque"
    r"|ofReduceBool|trustCompiler"  # el axioma detrás de native_decide/decide +native (VER-1); rechazo temprano por nombre, además de la auditoría de axiomas
    r"|run_tac|run_cmd|run_elab|elab_rules|elab|macro_rules|macro|notation|infix|infixl|infixr|prefix|postfix"
    r"|attribute|unsafe|implemented_by|extern"
    r"|set_option|import|open"
    r"|theorem|lemma|def|abbrev|instance|example|structure|inductive|class|namespace|section|end"
    r")(?![\w'])"
)


def find_forbidden_tactic(proof_tactics: str) -> str | None:
    """Primer patrón prohibido en el texto de tácticas, o None si es seguro: sin
    `#comando`, sin metaprogramación, sin declaraciones nuevas, sin cambios de
    entorno (salvo `open X in`) y sin caminos que evitan el kernel (`sorry`,
    `native_decide`, `decide +native`, `IO.*`)."""
    stripped = _COMMENT_RE.sub(" ", proof_tactics)
    if _HASH_CMD_RE.search(stripped):
        return "#-command"
    if _NATIVE_DECIDE_RE.search(stripped):
        return "decide +native"
    if _IO_RE.search(stripped):
        return "IO."
    stripped = _SAFE_OPEN_IN_RE.sub(" ", stripped)
    m = _DENIED_WORD_RE.search(stripped)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Auditoría de axiomas (VER-1). "Si aparece Lean.trustCompiler, la evaluación
# nativa se usó" en vez del kernel (guía oficial de Lean, enlace arriba).
# ---------------------------------------------------------------------------

_AXIOMS_RE = re.compile(r"depends on axioms:\s*\[([^\]]*)\]")
STANDARD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})


def parse_axioms(text: str) -> list[str]:
    """Extrae la lista de `#print axioms <name>` ("depends on axioms: [...]";
    sin coincidencia -> "no depende de ningún axioma" -> lista vacía)."""
    m = _AXIOMS_RE.search(text)
    return [a.strip() for a in m.group(1).split(",") if a.strip()] if m else []


def non_standard_axioms(axioms: list[str]) -> list[str]:
    return [a for a in axioms if a not in STANDARD_AXIOMS]


def _axiom_check(errors: list[dict], uses_sorry: bool, axioms: list[str]) -> tuple[bool, list[dict]]:
    """ok sólo si no hay errores, no usa `sorry` y los axiomas son un subconjunto
    de los estándar; si no, agrega un error legible (para el Crítico) sin perder
    los que ya había."""
    if errors or uses_sorry:
        return False, errors
    bad = non_standard_axioms(axioms)
    if bad:
        return False, errors + [{"line": 0, "col": 0, "message": f"non-standard axioms: {', '.join(bad)}"}]
    return True, errors


# ---------------------------------------------------------------------------
# Fuente única REPL <-> reporte (BUG-2): una sola función construye el bloque
# que se verifica Y el que se reporta, para que no puedan divergir.
# ---------------------------------------------------------------------------

_THEOREM_NAME_RE = re.compile(r"^\s*(?:theorem|lemma)\s+([A-Za-z_][\w'.]*)")


def _lean_project() -> Path:
    """`RAGSOLVER_LEAN_PROJECT` > copia en ext4 (~/ragsolver_cache/lean_project, misma tag,
    10x más rápida que /mnt/c para cargar los .olean de Mathlib) > lean_project/ del repo."""
    env = os.environ.get("RAGSOLVER_LEAN_PROJECT")
    if env:
        return Path(env)
    cache = Path.home() / "ragsolver_cache" / "lean_project"
    if (cache / ".lake" / "packages" / "mathlib" / ".lake" / "build").exists():
        return cache
    return Path(__file__).resolve().parents[3] / "lean_project"


LEAN_PROJECT = _lean_project()


def splice_proof(formal_statement: str, proof_tactics: str) -> str:
    """Reemplaza el `sorry` final del enunciado por el bloque de tácticas (indentado)."""
    stmt = formal_statement.rstrip()
    tactics = textwrap.dedent(proof_tactics.strip("\n"))  # sangría común fuera; la relativa (bloques `have ... := by`) se conserva
    body = "\n".join("  " + line if line.strip() else "" for line in tactics.splitlines())
    for pat in (r":=\s*by\s+sorry\s*$", r":=\s*sorry\s*$"):
        if re.search(pat, stmt):
            return re.sub(pat, lambda _m: ":= by\n" + body, stmt)
    return stmt + " := by\n" + body


_HAVE_BY_RE = re.compile(r"^have\b.*:=\s*by\b(.*)$")


def skeletonize(proof: str) -> tuple[str, list[str | None]]:
    """Prueba del LLM -> (esqueleto, candidatos), docs/PLAN_MEJORA_RAG.md Fase 2.

    El modelo casi nunca escribe el esqueleto con `sorry` que se le pide: escribe la prueba entera
    (smoke del 2026-10-14). En vez de depender de que obedezca, cada `have … := by <bloque>` de nivel
    superior pasa a `have … := by sorry` y su bloque queda como **candidato** para ese goal; las líneas
    tras el último `have` (el cierre) pasan a un `sorry` con ese cierre como candidato. Sin `have`, la
    prueba entera es el candidato del goal raíz. El resto (`rcases`, `intro`, `have … := término`) queda
    tal cual. `candidatos[k]` corresponde al k-ésimo `sorry` del esqueleto (None = no hay candidato).
    """
    lines = textwrap.dedent(proof.strip("\n")).splitlines()
    top = lambda l: bool(l.strip()) and not l[0].isspace()
    blocks = []  # (inicio, fin exclusivo, cabecera, candidato)
    i = 0
    while i < len(lines):
        m = _HAVE_BY_RE.match(lines[i]) if top(lines[i]) else None
        if not m:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not top(lines[j]):
            j += 1
        inline = m.group(1).strip()
        body = textwrap.dedent("\n".join(lines[i + 1 : j])).strip("\n")
        block = "\n".join(x for x in (inline, body) if x)
        blocks.append((i, j, lines[i][: m.start(1)].rstrip(), block))
        i = j
    if not blocks:
        body = "\n".join(lines).strip("\n")
        return "sorry", [None if not body or body == "sorry" else body]
    out: list[str] = []
    cands: list[str | None] = []
    pos = 0
    for start, end, head, block in blocks:
        for l in lines[pos:start]:
            out.append(l)
            cands += [None] * count_sorries(l)
        out.append(f"{head} sorry")
        cands.append(None if not block or block == "sorry" else block)
        pos = end
    tail = "\n".join(lines[pos:]).strip("\n")
    if tail:
        out.append("sorry")
        cands.append(None if tail == "sorry" else tail)
    return "\n".join(out), cands


def count_sorries(skeleton: str) -> int:
    return len(_SORRY_RE.findall(skeleton))


def fill_sorries(skeleton: str, blocks: list[str]) -> str:
    """Reemplaza el k-ésimo `sorry` del esqueleto por `blocks[k]` (docs/PLAN_MEJORA_RAG.md Fase 2).
    Un bloque de una línea va en el lugar del `sorry`; uno de varias líneas va en líneas nuevas con
    la sangría de la línea del `sorry` + 2, salvo que al `sorry` le siga texto en la misma línea
    (`exact foo (by sorry)`): entonces va entre paréntesis y separado por `;`."""
    it = iter(blocks)
    out = []
    for line in skeleton.splitlines():
        parts = _SORRY_RE.split(line)
        if len(parts) == 1:
            out.append(line)
            continue
        pad = " " * (len(line) - len(line.lstrip()) + 2)
        new = parts[0]
        for rest in parts[1:]:
            block = textwrap.dedent(next(it, "sorry").strip("\n")).strip("\n")
            if "\n" not in block:
                new += block
            elif rest.strip():
                new += "(" + "; ".join(l.strip() for l in block.splitlines() if l.strip()) + ")"
            else:
                new = new.rstrip() + "\n" + "\n".join(pad + l if l.strip() else "" for l in block.splitlines())
            new += rest
        out.append(new)
    return "\n".join(out)


def theorem_block(formal_statement: str, proof_tactics: str, header: str = "", max_heartbeats: int = 400000) -> tuple[str, str]:
    """Bloque que se envía al REPL (sin `import`: ya está en el entorno) y que
    también se reporta como `lean_code` vía `build_file` (BUG-2): `open`s del
    header + `set_option maxHeartbeats N in` + el enunciado con las tácticas
    empalmadas + `#print axioms <nombre>` para la auditoría de VER-1. Devuelve
    (código, nombre del teorema).

    `formal_statement` debe empezar con `theorem <nombre>` o `lemma <nombre>`
    (el formato que exige `Problem.formal_statement`); un `example` anónimo se
    renombra de forma determinista y mecánica (no lo hace el LLM) para poder
    auditar sus axiomas."""
    stmt = formal_statement.rstrip()
    m = _THEOREM_NAME_RE.match(stmt)
    if m:
        name = m.group(1)
    elif stmt.lstrip().startswith("example"):
        name = "_ragsolver_goal"
        stmt = re.sub(r"^\s*example\b", f"theorem {name}", stmt, count=1)
    else:
        raise ValueError(f"formal_statement debe empezar con 'theorem', 'lemma' o 'example': {stmt[:60]!r}")
    opens = "\n".join(l for l in header.splitlines() if l.strip() and not l.strip().startswith("import"))
    code = (opens + "\n" if opens else "") + f"set_option maxHeartbeats {max_heartbeats} in\n" + splice_proof(stmt, proof_tactics) + f"\n#print axioms {name}"
    return code, name


def build_file(formal_statement: str, proof_tactics: str, imports: list[str], max_heartbeats: int = 400000, header: str = "") -> str:
    """Archivo Lean completo y autocontenido: el mismo texto que se le pide al
    REPL, más el `import` inicial (que ahí ya viene del entorno). Es el
    `lean_code` que se reporta (BUG-2): reproduce exactamente lo verificado,
    incluidos los `open` del header."""
    block, _ = theorem_block(formal_statement, proof_tactics, header, max_heartbeats)
    hdr = "\n".join(f"import {m}" for m in dict.fromkeys(imports)) + "\n\n"
    return hdr + block + "\n"


def parse_messages(output: str) -> list[dict]:
    msgs: list[dict] = []
    for line in output.splitlines():
        m = _MSG_RE.match(line)
        if m:
            msgs.append({"line": int(m["line"]), "col": int(m["col"]), "severity": m["sev"], "message": m["msg"]})
        elif msgs:
            msgs[-1]["message"] += "\n" + line
    return msgs


def run_lean(source: str, name: str, project: Path = LEAN_PROJECT, timeout_s: int = 900) -> tuple[int | None, str, float, Path]:
    scratch = project / "scratch"
    scratch.mkdir(exist_ok=True)
    path = scratch / f"{re.sub(r'[^A-Za-z0-9_]+', '_', name)}.lean"
    path.write_text(source, encoding="utf-8")
    env = {**os.environ, "PATH": f"{Path.home() / '.elan' / 'bin'}:{os.environ.get('PATH', '')}"}
    t0 = time.time()
    try:
        proc = subprocess.run(["lake", "env", "lean", str(path.relative_to(project))], cwd=project, env=env, capture_output=True, text=True, timeout=timeout_s)
        return proc.returncode, proc.stdout + proc.stderr, time.time() - t0, path
    except subprocess.TimeoutExpired as e:
        return None, (e.stdout or "") + (e.stderr or "") if isinstance(e.stdout, str) else "", time.time() - t0, path


def verify(formal_statement: str, proof_tactics: str, name: str, imports: list[str], timeout_s: int = 900, max_heartbeats: int = 400000, project: Path = LEAN_PROJECT, header: str = "") -> Verdict:
    forbidden = find_forbidden_tactic(proof_tactics)
    if forbidden:
        return Verdict(ok=False, errors=[{"line": 0, "col": 0, "message": f"forbidden token in proof: {forbidden}"}])
    source = build_file(formal_statement, proof_tactics, imports, max_heartbeats, header)
    code, out, secs, path = run_lean(source, name, project, timeout_s)
    msgs = parse_messages(out)
    errors = [m for m in msgs if m["severity"] == "error"]
    uses_sorry = any("sorry" in m["message"] for m in msgs)
    if code is None:
        errors.append({"line": 0, "col": 0, "message": f"timeout after {timeout_s}s"})
        return Verdict(ok=False, errors=errors, uses_sorry=uses_sorry, seconds=round(secs, 1), lean_file=str(path), raw=out[-4000:])
    axioms = parse_axioms("\n".join(m["message"] for m in msgs if m["severity"] == "info"))
    ok, errors = _axiom_check(errors, uses_sorry, axioms)
    return Verdict(ok=ok, errors=errors, uses_sorry=uses_sorry, axioms=axioms, seconds=round(secs, 1), lean_file=str(path), raw=out[-4000:])


def limit_heartbeats(tactic: str, heartbeats: int) -> str:
    """`set_option maxHeartbeats N in` sobre una táctica. Un bloque de varias líneas (el de un paso que escribió
    el modelo o el demostrador) va dentro de `·`, que enfoca el goal y debe cerrarlo como el bloque en su sitio:
    sin eso el límite no lo cubría y un bloque lento agotaba el timeout de reloj, reiniciaba el REPL y dejaba
    "stale" los pasos de todos los hilos (eval-v4, DECISIONS #128)."""
    body = tactic.strip()
    if "\n" not in body:
        return f"set_option maxHeartbeats {int(heartbeats)} in {body}"
    return f"set_option maxHeartbeats {int(heartbeats)} in\n· " + body.replace("\n", "\n  ")


class LeanChecker:
    """REPL persistente: el entorno con los imports se crea una vez y se reutiliza."""

    def __init__(self, imports: list[str] = ("Mathlib",), project: Path | None = None, timeout_s: int = 900):
        self.imports = list(imports)
        self.project = project or LEAN_PROJECT
        self.timeout_s = timeout_s
        self._server = None
        self._env = None
        self._gen = 0  # sube en cada arranque del REPL: un `proof_state` sólo vale en la generación que lo creó
        self._lock = threading.Lock()  # un solo REPL; ~0.3 s por comprobación, no vale la pena más

    @property
    def gen(self) -> int:
        """Generación del REPL vivo (-1 si cayó): un `proof_state` de otra generación ya no vale."""
        return self._gen if self._server is not None else -1

    def _start(self) -> None:
        from lean_interact import Command, LeanREPLConfig, LeanServer
        from lean_interact.project import LocalProject

        os.environ["PATH"] = f"{Path.home() / '.elan' / 'bin'}:{os.environ.get('PATH', '')}"
        self._server = LeanServer(LeanREPLConfig(project=LocalProject(directory=str(self.project), auto_build=False)))
        self._gen += 1
        r = self._server.run(Command(cmd="\n".join(f"import {m}" for m in dict.fromkeys(self.imports))), timeout=max(self.timeout_s, 1800))
        errors = [m.data for m in r.messages if m.severity == "error"]
        if errors:
            raise RuntimeError(f"imports fallaron en el REPL: {errors[:2]}")
        self._env = r.env

    def check(self, formal_statement: str, proof_tactics: str, header: str = "", max_heartbeats: int = 400000) -> Verdict:
        forbidden = find_forbidden_tactic(proof_tactics)
        if forbidden:
            return Verdict(ok=False, errors=[{"line": 0, "col": 0, "message": f"forbidden token in proof: {forbidden}"}])
        with self._lock:
            return self._check_locked(formal_statement, proof_tactics, header, max_heartbeats)

    def _check_locked(self, formal_statement: str, proof_tactics: str, header: str, max_heartbeats: int) -> Verdict:
        from lean_interact import Command

        if self._server is None or not self._server.is_alive():
            self._start()
        code, _name = theorem_block(formal_statement, proof_tactics, header, max_heartbeats)
        t0 = time.time()
        try:
            # all_tactics: constantes que usa cada táctica -> qué lemas citados usa DE VERDAD la prueba
            r = self._server.run(Command(cmd=code, env=self._env, all_tactics=True), timeout=self.timeout_s)
        except Exception as e:  # timeout o REPL caído: se reinicia en la próxima llamada
            self._server = None
            return Verdict(ok=False, errors=[{"line": 0, "col": 0, "message": f"lean repl failure: {type(e).__name__}: {str(e)[:200]}"}], seconds=round(time.time() - t0, 1), raw=code[-2000:])
        msgs = [{"line": m.start_pos.line if m.start_pos else 0, "col": m.start_pos.column if m.start_pos else 0, "severity": m.severity, "message": m.data} for m in r.messages]
        errors = [m for m in msgs if m["severity"] == "error"]
        uses_sorry = any("sorry" in m["message"] for m in msgs)
        axioms = parse_axioms("\n".join(m["message"] for m in msgs if m["severity"] == "info"))
        ok, errors = _axiom_check(errors, uses_sorry, axioms)
        used = sorted({c for t in getattr(r, "tactics", None) or [] for c in t.used_constants})
        return Verdict(ok=ok, errors=errors, uses_sorry=uses_sorry, axioms=axioms, used_constants=used, seconds=round(time.time() - t0, 1), raw=code[-2000:])

    def _run(self, query, timeout: float | None = None):
        """Una consulta al REPL con el lock tomado por el llamador; timeout o REPL caído -> None
        (se reinicia en la próxima llamada, y los `proof_state` anteriores dejan de valer)."""
        if self._server is None or not self._server.is_alive():
            self._start()
        try:
            return self._server.run(query, timeout=timeout or self.timeout_s)
        except Exception:
            self._server = None
            return None

    def goals(self, formal_statement: str, skeleton: str, header: str = "", max_heartbeats: int = 400000) -> tuple[list[dict], list[dict]]:
        """Modo sonda (docs/PLAN_MEJORA_RAG.md Fase 2): elabora un esqueleto con `sorry` y devuelve
        ([{goal, proof_state}] en el orden en que aparecen los `sorry`, errores de elaboración). Nunca
        produce un `Verdict`: el único camino a `ok` sigue siendo `check` sobre el archivo final, que
        rechaza `sorry`. Fuera de `sorry`, el esqueleto pasa por el mismo validador que una prueba."""
        from lean_interact import Command

        forbidden = find_forbidden_tactic(_SORRY_RE.sub(" skip ", skeleton))
        if forbidden:
            return [], [{"line": 0, "col": 0, "message": f"forbidden token in proof: {forbidden}"}]
        code, _name = theorem_block(formal_statement, skeleton, header, max_heartbeats)
        with self._lock:
            r = self._run(Command(cmd=code, env=self._env))
        if r is None or not hasattr(r, "sorries"):
            return [], [{"line": 0, "col": 0, "message": f"lean repl failure: {str(getattr(r, 'message', r))[:200]}"}]
        errors = [{"line": m.start_pos.line if m.start_pos else 0, "col": m.start_pos.column if m.start_pos else 0, "message": m.data} for m in r.messages if m.severity == "error"]
        found = sorted(r.sorries, key=lambda s: (s.start_pos.line, s.start_pos.column) if s.start_pos else (0, 0))
        return [{"goal": s.goal, "proof_state": s.proof_state, "gen": self._gen} for s in found], errors

    def step(self, proof_state: int, tactic: str, timeout: float = 120, heartbeats: int | None = None, gen: int | None = None) -> tuple[bool, str, list[str]]:
        """Aplica `tactic` a un `proof_state` de `goals` sin re-elaborar el teorema (`ProofStep`).
        Devuelve (cerrado, error o estado, mensajes info — p.ej. el "Try this" de `try?`). La táctica
        pasa por `find_forbidden_tactic` antes de tocar Lean, igual que en `check`."""
        from lean_interact import ProofStep

        forbidden = find_forbidden_tactic(tactic)
        if forbidden:
            return False, f"forbidden token in proof: {forbidden}", []
        if heartbeats:
            # el límite lo pone el sistema DESPUÉS del validador (que prohíbe `set_option` al LLM): Lean
            # corta por heartbeats antes que el timeout de reloj, que reinicia el REPL (~5 min) e invalida
            # los `proof_state` de los demás hilos
            tactic = limit_heartbeats(tactic, heartbeats)
        with self._lock:
            if gen is not None and (gen != self._gen or self._server is None):
                # el REPL se reinició (timeout de otra táctica): ese número de estado ya no existe o, peor,
                # es otro goal del REPL nuevo
                return False, "stale proof state (lean repl restarted)", []
            r = self._run(ProofStep(tactic=tactic, proof_state=proof_state), timeout=timeout)
        if r is None or not hasattr(r, "proof_status"):  # LeanError (la táctica falló) o REPL caído
            return False, str(getattr(r, "message", "lean repl failure"))[:2000], []
        errs = [m.data for m in r.messages if m.severity == "error"]
        infos = [m.data for m in r.messages if m.severity == "info"]
        return r.proof_status == "Completed" and not errs, "\n".join(errs)[:2000] or r.proof_status, infos

    def signatures(self, names: list[str], header: str = "") -> dict[str, str]:
        """{nombre: tipo} de los `names` que resuelve `#check @nombre` en el entorno cargado, uno por
        comando (exacto, a diferencia de `check_identifiers_exist`). `header` aporta los `open` con
        los que esos nombres se usan en las pruebas (p.ej. `open Nat` resuelve `succ_le_iff`)."""
        from lean_interact import Command

        opens = " ".join(l.strip()[len("open"):].strip() for l in header.splitlines() if l.strip().startswith("open"))
        prefix = f"open {opens} in\n" if opens else ""
        out: dict[str, str] = {}
        with self._lock:
            for n in names:
                # con los `open`, un nombre de la raíz puede quedar ambiguo (`sq_nonneg` vs `Nat.sq_nonneg`):
                # la prueba lo desambigua por tipo, `#check` no -> se reintenta sin los `open`
                for pre in dict.fromkeys((prefix, "")):
                    r = self._run(Command(cmd=f"{pre}#check @{n}", env=self._env), timeout=60)
                    msgs = getattr(r, "messages", None)
                    if msgs is not None and not any(m.severity == "error" for m in msgs):
                        info = next((m.data for m in msgs if m.severity == "info"), "")
                        out[n] = info.split(" : ", 1)[1].strip() if " : " in info else info
                        break
        return out

    def check_identifiers_exist(self, decl_names: list[str]) -> tuple[int, int]:
        """(existen, total): cuántos de `decl_names` resuelve el entorno cargado,
        vía `#check @nombre`. Consistencia índice <-> Lean (`docs/PLAN_RAG.md
        §12`): si el índice tiene `decl_name` que no existen en esta versión de
        Mathlib, el RAG cita lemas fantasma. No es una prueba de un LLM: no pasa
        por `find_forbidden_tactic`. Aproximado (cuenta errores totales, no los
        mapea 1 a 1 con cada nombre) — suficiente para un aviso, no aborta nada."""
        from lean_interact import Command

        if not decl_names:
            return 0, 0
        with self._lock:
            if self._server is None or not self._server.is_alive():
                self._start()
            code = "\n".join(f"#check @{n}" for n in decl_names)
            try:
                r = self._server.run(Command(cmd=code, env=self._env), timeout=self.timeout_s)
            except Exception:
                return 0, len(decl_names)
        n_errors = sum(1 for m in r.messages if m.severity == "error")
        return max(0, len(decl_names) - n_errors), len(decl_names)

    def close(self) -> None:
        if self._server is not None:
            self._server.kill()
            self._server = None


def identifiers_used(proof_tactics: str, decl_names: list[str]) -> list[str]:
    """Nombres de Mathlib citados que aparecen en el código aceptado (used_in_proof, §6.4)."""
    return [d for d in decl_names if re.search(r"(?<![\w.'])" + re.escape(d) + r"(?![\w'.])", proof_tactics)]
