"""Demo Gradio (docs/PLAN_RAG.md Fase 6, tarea 2): pestaña "buscar" (hits con citas,
firma Lean, docs_url, recorte y descripción de figura) y pestaña "resolver" (el sistema
actual: demostrador local en la GPU como etapa 0, verificación con el kernel de Lean).

Local:  bash scripts/demo_up.sh     (levanta el demostrador en la GPU y esta demo; Lean y el
        embedder se precalientan en segundo plano al arrancar, ~5 min)
Space:  el índice se descarga con `snapshot_download` si RAGSOLVER_HF_DATASET está definido.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

import gradio as gr

DB_PATH = os.environ.get("RAGSOLVER_DB", "data/index/lancedb")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_STATEMENT_RE = re.compile(r"^\s*(?:theorem|lemma)\s+[A-Za-z_][\w'.]*.*:=\s*by\s+sorry\s*$", re.S)
MINIF2F_HEADER = "open BigOperators Real Nat Topology Rat"
# Problemas que el sistema resolvió en eval-v3: sus muestras del demostrador están en la caché de disco,
# así que en una presentación salen en segundos (la verificación de Lean sí corre en vivo).
EXAMPLES = ["mathd_algebra_67", "imo_1964_p1_1", "imo_1965_p1", "amc12b_2003_p17"]
_LOCK = threading.Lock()


def _examples() -> list[list[str]]:
    path = Path("data/eval/problems/eval_v1.jsonl")
    if not path.exists():
        return []
    rows = {d["problem_id"]: d for d in (json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip())}
    return [[rows[i]["informal_statement"], rows[i]["formal_statement"], rows[i].get("header", "")] for i in EXAMPLES if i in rows]


def _ensure_index() -> str:
    repo = os.environ.get("RAGSOLVER_HF_DATASET")
    if repo and not Path(DB_PATH).exists():
        from huggingface_hub import snapshot_download

        local = snapshot_download(repo_id=repo, repo_type="dataset", allow_patterns=["index/**"])
        return str(Path(local) / "index" / "lancedb")
    return DB_PATH


@lru_cache(maxsize=1)
def _retriever():
    from ragsolver.rag.retrieve import Retriever

    r = Retriever(db_path=_ensure_index())
    r.trace_dir = tempfile.mkdtemp(prefix="ragsolver-demo-")
    return r


@lru_cache(maxsize=1)
def _base() -> dict:
    from ragsolver.agents.agents import load_agents_config

    return load_agents_config()


@lru_cache(maxsize=1)
def _new_checker():
    from ragsolver.lean.verify import LeanChecker

    return LeanChecker(imports=_base()["lean"]["imports"], timeout_s=_base()["lean"]["timeout_s"])


def _checker():
    """Un solo REPL de Lean para toda la sesión: `import Mathlib` tarda ~5 min y no se repite por problema
    (el lock evita que el precalentamiento y un primer clic creen dos)."""
    with _LOCK:
        return _new_checker()


def _prover():
    """El demostrador local si su servidor responde (se vuelve a preguntar en cada problema: puede arrancar después)."""
    from ragsolver.prover import local_prover

    return local_prover(_base().get("prover") or {})


def _warmup() -> None:
    """Lean (`import Mathlib`) y el embedder en segundo plano, para que el primer problema no espere."""
    try:
        _checker().check("theorem _warmup : True := by sorry", "trivial")
        print("[demo] Lean listo", flush=True)
        r = _retriever()
        if getattr(r, "versions", None):
            from ragsolver.rag.embed import embed_queries

            embed_queries(["warmup"], model_name=r.versions["embedding"]["model"])
            print("[demo] embedder listo", flush=True)
    except Exception as e:  # sin Lean o sin índice la pestaña Buscar/Resolver lo dirá al usarse
        print(f"[demo] precalentamiento omitido: {type(e).__name__}: {e}", flush=True)


def search(query: str, corpus: str, mode: str, k: int, rerank: bool):
    from ragsolver.rag.schema import RetrievalRequest

    if not query.strip():
        return [], [], "Escribí una consulta."
    try:
        res = _retriever().search(RetrievalRequest(queries=[query], mode=mode, corpora=[corpus], k=int(k), rerank=rerank, context={"problem_id": "demo", "agent": "demo"}))
    except (FileNotFoundError, ValueError) as e:  # p.ej. el corpus informal no se publica (licencia) y no existe en el Space
        return [], [], f"Corpus `{corpus}` no disponible aquí: {str(e)[:200]}"
    rows, images = [], []
    for h in res.hits:
        c = h.citation
        link = c.locator.get("docs_url") or ""
        sig = (h.metadata.get("signature") or "")[:200] if corpus == "mathlib" else (h.metadata.get("context_summary") or "")[:200]
        rows.append([c.citation_id, c.label, sig, round(c.scores.fused or 0, 4), round(c.scores.rerank, 3) if c.scores.rerank is not None else "", link, c.snippet[:200]])
        for f in h.figures:
            if f.get("image_path") and Path(f["image_path"]).exists():
                images.append((f["image_path"], f"{c.citation_id} · {f['figure_id']}: {(f.get('description') or '')[:150]}"))
    return rows, images, f"{len(res.hits)} hits · {res.trace.latency_ms:.0f} ms · 0 llamadas LLM"


def solve(informal: str, formal: str, header: str, config: str, k_max: int):
    from ragsolver.agents.agents import Agents
    from ragsolver.agents.schemas import Problem
    from ragsolver.llm import LLM
    from ragsolver.orchestrator.graph import Orchestrator
    from ragsolver.orchestrator.run import drafter_agents, effective_config, run_baseline, run_prover_only

    if not (shutil.which("lake") or (Path.home() / ".elan" / "bin" / "lake").exists()):
        return "El modo **Resolver** necesita Lean 4 + Mathlib y una API key del LLM: sólo está disponible en una instalación local (`docs/install.md`).", "", [], "{}"
    host = os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1")
    if host not in _LOOPBACK_HOSTS:  # SEC-1: Lean ejecuta la salida del LLM sin sandbox (docs/architecture.md "Modelo de amenazas del verificador")
        return f"El modo **Resolver** ejecuta código no confiable (Lean sin sandbox) y sólo corre si el servidor escucha en loopback; `GRADIO_SERVER_NAME={host}` lo expone a la red. Cambiá la variable para usar este modo.", "", [], "{}"
    formal_statement = formal.strip() or "theorem demo : True := by sorry"
    if not _STATEMENT_RE.match(formal_statement):  # el usuario escribe el enunciado a mano en la demo: mismo formato que exige Problem
        return "El enunciado formal debe empezar con `theorem <nombre>` o `lemma <nombre>` y terminar en `:= by sorry` (docs/user_guide.md §2).", "", [], "{}"
    base = _base()
    cfg = effective_config(base, config)
    cfg["k_max"] = int(k_max) if config not in ("no_critic",) else 1
    problem = Problem(problem_id="demo", informal_statement=informal, formal_statement=formal_statement, header=header.strip())
    trace_dir = tempfile.mkdtemp(prefix="ragsolver-demo-")
    agents = Agents(LLM(section="llm"), base, trace_dir=trace_dir)
    checker, prover = _checker(), _prover()
    if cfg["mode"] == "baseline_prover":
        rep = run_prover_only(problem, cfg, checker, prover)
    elif cfg["mode"].startswith("baseline"):
        rep = run_baseline(cfg["mode"], problem, cfg, agents, "demo", checker)
    else:
        rep = Orchestrator(agents, _retriever() if cfg["rag"] else None, checker, prover, drafter_agents(base, trace_dir)).solve(problem, cfg, "demo")
    cites = [[c.citation_id, c.label, "✔ prueba" if c.used_in_proof else ("boceto" if c.used_in_sketch else "consultada"), c.locator.get("docs_url") or ""] for c in rep.citations]
    who = {"prover_root": "demostrador local (etapa 0, 0 API)", "prover_draft": "demostrador local con el borrador de la API (DRAFT)", "agents": "agentes por API"}.get(rep.solved_by, "")
    head = f"**{rep.status.upper()}**" + (f" · resuelto por: {who}" if who else "") + f" · {rep.prover_samples} muestras del demostrador · {rep.llm_calls} llamadas LLM · {rep.wall_seconds}s · lemas verificados {rep.n_used_in_proof}/{rep.n_retrieved}"
    if prover is None and (cfg.get("prover") or {}).get("enabled"):
        head += "\n\n⚠ El demostrador local no responde (`bash scripts/demo_up.sh`): se usó el sistema sin él."
    if rep.verdict and not rep.verdict.ok:
        head += "\n\nErrores de Lean:\n```\n" + "\n".join(e["message"][:300] for e in rep.verdict.errors[:3]) + "\n```"
    return head, rep.lean_code or "(sin código)", cites, json.dumps(rep.model_dump(exclude={"citations", "lean_code", "verdict"}), indent=2, ensure_ascii=False)


with gr.Blocks(title="RagSolver") as app:
    gr.Markdown("# RagSolver — recuperación y resolución verificada (Lean 4 + Mathlib v4.28.0-rc1)")
    with gr.Tab("Buscar"):
        with gr.Row():
            q = gr.Textbox(label="Consulta", placeholder="prove that 2^2001 + 3^2001 is divisible by 7", scale=4)
            corpus = gr.Dropdown(["mathlib", "informal"], value="mathlib", label="Corpus")
            mode = gr.Dropdown(["nl", "formal", "error", "exemplar"], value="nl", label="Modo")
            k = gr.Slider(1, 20, value=8, step=1, label="k")
            rerank = gr.Checkbox(value=False, label="rerank (cross-encoder, +2.4 GB RAM)")
        btn = gr.Button("Buscar")
        status = gr.Markdown()
        table = gr.Dataframe(headers=["id", "cita", "firma / resumen", "fused", "rerank", "docs_url", "snippet"], wrap=True)
        gallery = gr.Gallery(label="Figuras adjuntas", columns=4, height=260)
        btn.click(search, [q, corpus, mode, k, rerank], [table, gallery, status])
    with gr.Tab("Resolver"):
        informal = gr.Textbox(label="Enunciado (lenguaje natural)", lines=3, value="For every natural number n, n + 0 = n.")
        formal = gr.Textbox(label="Enunciado formal (Lean 4, termina en := by sorry)", lines=3, value="theorem demo (n : ℕ) : n + 0 = n := by sorry")
        header = gr.Textbox(label="Cabecera (open …)", value=MINIF2F_HEADER)
        with gr.Row():
            config = gr.Dropdown(["full", "dsp", "baseline_prover", "no_rag", "baseline_a"], value="full", label="Configuración")
            kmax = gr.Slider(1, 5, value=3, step=1, label="k_max (reintentos de los agentes por API)")
        if _examples():
            gr.Examples(_examples(), [informal, formal, header], label="Problemas de eval-v3 (muestras en caché: salen en segundos)")
        run_btn = gr.Button("Resolver (demostrador local + kernel de Lean)")
        out_head = gr.Markdown()
        out_code = gr.Code(language=None, label="Lean 4 (aceptado por el kernel)")
        out_cites = gr.Dataframe(headers=["id", "cita", "uso", "docs_url"], wrap=True)
        out_json = gr.Code(language="json", label="SolutionReport")
        run_btn.click(solve, [informal, formal, header, config, kmax], [out_head, out_code, out_cites, out_json])

if __name__ == "__main__":
    if shutil.which("lake") or (Path.home() / ".elan" / "bin" / "lake").exists():
        threading.Thread(target=_warmup, daemon=True).start()
    app.launch(server_name=os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1"), share=False)
