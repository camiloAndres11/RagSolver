"""Corrida de problemas × configuraciones de ablación (propuesta §8.2) -> SolutionReport
por (problema, config) en data/eval/runs/<run_id>/results.jsonl + resumen.

Uso: python -m ragsolver.orchestrator.run --problems data/eval/problems/dev_trivial.jsonl --configs full,no_rag
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import time
import uuid
from pathlib import Path

from ragsolver.agents.agents import Agents, InvalidLLMOutput, load_agents_config
from ragsolver.agents.schemas import Problem, SolutionReport
from ragsolver.lean.verify import LeanChecker, build_file, verify
from ragsolver.llm import LLM, DailyQuotaExhausted
from ragsolver.logging.jsonl_writer import trace_path
from ragsolver.orchestrator.graph import Orchestrator, close_goal
from ragsolver.prover import ProverUnavailable, local_prover
from ragsolver.rag.embed import embed_queries
from ragsolver.rag.retrieve import Retriever


def load_problems(path: str | Path) -> list[Problem]:
    return [Problem.model_validate_json(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def check_index_consistency(checker: LeanChecker, retriever: Retriever, n: int = 20, seed: int = 13) -> None:
    """`PLAN_RAG.md §12`: `decl_name` al azar de la tabla que sirve el corpus
    `mathlib`, comprobados con `#check` contra el Lean real del verificador — si
    el índice tiene nombres que no existen en esta versión de Mathlib, el RAG
    cita lemas fantasma. No aborta la corrida, sólo avisa en el log; cualquier
    fallo (tabla ausente, REPL caído) se ignora igual."""
    import random

    try:
        table = retriever._table("mathlib")
        rows = table.search().select(["decl_name"]).limit(table.count_rows()).to_list()
        names = [r["decl_name"] for r in rows if r.get("decl_name")]
        if not names:
            return
        sample = random.Random(seed).sample(names, min(n, len(names)))
        found, total = checker.check_identifiers_exist(sample)
        print(f"[index-check] {found}/{total} decl_name de la tabla '{retriever.tables.get('mathlib', 'mathlib')}' existen en Lean", flush=True)
        if found < total - 1:  # tolera 1 falso positivo del `#check` aproximado
            print(f"[index-check] AVISO: {total - found} nombres del índice no se resolvieron en Lean — posibles lemas fantasma", flush=True)
    except Exception as e:
        print(f"[index-check] omitido: {type(e).__name__}: {e}", flush=True)


def effective_config(base: dict, name: str) -> dict:
    """Config efectiva de una ablación: claves del yaml + sobreescrituras de `configs[name]`."""
    over = dict(base["configs"][name])
    cfg = {
        "name": name,
        "mode": over.pop("mode", "graph"),
        "rag": over.pop("rag", True),
        "corpora": ["mathlib", "informal"],
        "k_max": over.pop("k_max", base["k_max"]),
        "max_replans": base["max_replans"],
        "llm_budget": base["llm_budget"],
        "retrieval": dict(base["retrieval"]),
        "lean": dict(base["lean"]),
        "goal_mode": over.pop("goal_mode", base.get("goal_mode", False)),
        "close": dict(base.get("close") or {}),
        "prover": dict(base.get("prover") or {}),
    }
    for k, v in over.items():  # un dict (p.ej. `prover: {k_root: 32}`) se fusiona con el de base, no lo reemplaza
        cfg[k] = {**cfg[k], **v} if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
    return cfg


def drafter_agents(base: dict, trace_dir: str) -> Agents | None:
    """Agentes con el LLM de `llm_draft` (mismo modelo, CON razonamiento) para el borrador de DRAFT; None si
    la sección no existe en `configs/versions.yaml` (DRAFT usa entonces los agentes normales)."""
    import yaml

    if "llm_draft" not in (yaml.safe_load(open("configs/versions.yaml", encoding="utf-8")) or {}):
        return None
    return Agents(LLM(section="llm_draft"), base, trace_dir=trace_dir)


def run_auto(problem: Problem, cfg: dict, checker: LeanChecker | None, agents: Agents | None = None, run_id: str = "") -> SolutionReport:
    """Línea base `baseline_auto` (docs/PLAN_MEJORA_RAG.md Fase 6): 0 llamadas LLM, sólo la batería
    CLOSE sin premisas sobre el goal raíz. Es lo que Lean resuelve solo; el sistema con agentes tiene
    que superarla para justificar sus llamadas."""
    t0 = time.time()
    rep = SolutionReport(problem_id=problem.problem_id, config=cfg["name"], status="fail", attempts=1, error_types=["unknown"])
    if checker is None:
        rep.error_types = ["unsupported: baseline_auto requiere el REPL de Lean"]
        return rep
    lean = cfg["lean"]
    goals, errors = checker.goals(problem.formal_statement, "sorry", problem.header, lean["max_heartbeats"])
    block, log = (close_goal(checker, goals[0], cfg["close"]["tactics"], False, [], [], cfg["close"]["timeout_s"]) if len(goals) == 1 and not errors else (None, []))
    if block:
        rep.verdict = checker.check(problem.formal_statement, block, problem.header, lean["max_heartbeats"])
        rep.lean_code = build_file(problem.formal_statement, block, lean["imports"] + problem.imports, lean["max_heartbeats"], header=problem.header)
        if rep.verdict.ok:
            rep.status, rep.error_types = "done", []
    if agents is not None:
        agents.trace("close", run_id, problem.problem_id, 1, (time.time() - t0) * 1000, {"goal": 0, "closed": bool(block), "block": (block or "")[:200], "tried": log})
    rep.wall_seconds = round(time.time() - t0, 1)
    return rep


def run_prover_only(problem: Problem, cfg: dict, checker: LeanChecker | None, prover, agents: Agents | None = None, run_id: str = "") -> SolutionReport:
    """Línea base `baseline_prover` (docs/PLAN_REDISENO_PROVER.md): 0 API, sólo el demostrador local sobre
    el teorema completo (k_root muestras + autocorrección con los errores de Lean, SIN sugerencias del RAG)."""
    import re as _re

    from ragsolver.prover import prove

    t0 = time.time()
    rep = SolutionReport(problem_id=problem.problem_id, config=cfg["name"], status="fail", attempts=1, error_types=["unknown"])
    if checker is None or prover is None:
        rep.error_types = ["unsupported: baseline_prover requiere el REPL y el demostrador local"]
        return rep
    pcfg, lean = cfg["prover"], cfg["lean"]
    name = _re.match(r"\s*(?:theorem|lemma)\s+(\S+)", problem.formal_statement).group(1)
    tac, log = prove(prover, checker, problem.formal_statement, problem.header, name, k=pcfg["k_root"], correction_rounds=pcfg.get("correction_rounds", 1), retriever=None, max_heartbeats=lean["max_heartbeats"])
    rep.prover_samples = log["samples"] + log["rounds"] * 2
    if tac:
        rep.verdict = checker.check(problem.formal_statement, tac, problem.header, lean["max_heartbeats"])
        rep.lean_code = build_file(problem.formal_statement, tac, lean["imports"] + problem.imports, lean["max_heartbeats"], header=problem.header)
        if rep.verdict.ok:
            rep.status, rep.error_types, rep.solved_by = "done", [], "prover_root"
    if agents is not None:
        agents.trace("prover", run_id, problem.problem_id, 1, (time.time() - t0) * 1000, {"stage": "baseline_prover", "solved": bool(tac), **{k: v for k, v in log.items() if k not in ("suggested", "candidates")}})
    rep.wall_seconds = round(time.time() - t0, 1)
    return rep


def run_baseline(mode: str, problem: Problem, cfg: dict, agents: Agents, run_id: str, checker: LeanChecker | None = None) -> SolutionReport:
    if problem.imports:  # mismo guard que Orchestrator.solve (BUG-4): nadie usa imports adicionales por problema hoy
        return SolutionReport(problem_id=problem.problem_id, config=cfg["name"], status="fail", error_types=[f"unsupported: Problem.imports no soportado: {problem.imports}"])
    t0 = time.time()
    out, resp = agents.baseline(mode, problem, run_id)
    rep = SolutionReport(problem_id=problem.problem_id, config=cfg["name"], status="fail", attempts=1, llm_calls=1, tokens_in=resp.tokens_in, tokens_out=resp.tokens_out, models=[resp.model])
    if mode == "baseline_a":
        lean = cfg["lean"]
        if checker is not None:
            v = checker.check(problem.formal_statement, out.proof_tactics, problem.header, lean["max_heartbeats"])
        else:
            v = verify(problem.formal_statement, out.proof_tactics, f"{problem.problem_id}_{mode}", lean["imports"] + problem.imports, lean["timeout_s"], lean["max_heartbeats"], header=problem.header)
        rep.verdict, rep.status = v, ("done" if v.ok else "fail")
        # BUG-2: mismo `header` que se verificó, para que `lean_code` sea fiel.
        rep.lean_code = build_file(problem.formal_statement, out.proof_tactics, lean["imports"] + problem.imports, lean["max_heartbeats"], header=problem.header)
        if not v.ok:
            rep.error_types = ["unknown"]
    else:  # baseline_b: sin verificación formal; se reporta su autoevaluación (métrica "falsos positivos evitados")
        rep.self_assessed_correct = out.self_assessed_correct
        rep.status = "fail"
    rep.wall_seconds = round(time.time() - t0, 1)
    return rep


def run_suite(problems: list[Problem], config_names: list[str], out_dir: str | Path, agents_yaml: str = "configs/agents.yaml", db_path: str = "data/index/lancedb", run_id: str | None = None, workers: int = 1) -> dict:
    """Corre problemas × configs. **Reanudable**: con el mismo `run_id`, los pares
    (config, problema) ya presentes en results.jsonl se saltan; si se agota la cuota
    diaria de todos los modelos, corta limpio (status "quota_exhausted") para reanudar
    al día siguiente con el mismo comando. `workers` > 1 resuelve varios pares a la vez
    (hilos: el cuello de botella es la latencia del LLM; el REPL y las trazas van con lock)."""
    base = load_agents_config(agents_yaml)
    run_id = run_id or f"run-{time.strftime('%Y%m%d-%H%M')}-{uuid.uuid4().hex[:6]}"
    out = Path(out_dir) / run_id
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / "results.jsonl"
    results: list[SolutionReport] = [SolutionReport.model_validate_json(l) for l in results_path.read_text(encoding="utf-8").splitlines() if l.strip()] if results_path.exists() else []
    errors = [r for r in results if r.status == "error"]
    if errors:  # fallo de infraestructura (excepción, red, key): se reintentan; quedan auditados en errors.jsonl
        with (out / "errors.jsonl").open("a", encoding="utf-8") as f:
            f.writelines(r.model_dump_json() + "\n" for r in errors)
        results = [r for r in results if r.status != "error"]
        results_path.write_text("".join(r.model_dump_json() + "\n" for r in results), encoding="utf-8")
        print(f"[resume] {len(errors)} pares con status=error se reintentan (movidos a errors.jsonl)", flush=True)
    done = {(r.config, r.problem_id) for r in results}
    if done:
        print(f"[resume] {len(done)} resultados previos en {results_path}", flush=True)

    llm = LLM(section="llm")
    agents = Agents(llm, base, trace_dir=str(out / "traces"))
    retriever = Retriever(db_path=db_path)
    retriever.trace_dir = str(out / "traces")
    checker = LeanChecker(imports=base["lean"]["imports"], timeout_s=base["lean"]["timeout_s"]) if base["lean"].get("backend", "repl") == "repl" else None
    prover = local_prover(base.get("prover") or {})
    needs = [n for n in config_names if (c := effective_config(base, n))["mode"] == "baseline_prover" or any(c["prover"].get(x) for x in ("dsp", "repair", "examples"))]
    if needs and prover is None:  # sin demostrador estas configuraciones degradarían en silencio (fallos falsos o el bucle por API)
        raise SystemExit(f"[prover] {needs} necesitan el demostrador local y no responde: `bash scripts/prover_server.sh start`")
    drafter = drafter_agents(base, str(out / "traces"))
    orchestrator = Orchestrator(agents, retriever, checker, prover, drafter)
    if checker is not None:  # arranca el REPL (`import Mathlib`, minutos) fuera del reloj de los problemas
        checker.check("theorem _warmup : True := by sorry", "trivial")
    if getattr(retriever, "versions", None):  # carga el embedder antes de los hilos (los fakes de los tests no lo tienen)
        embed_queries(["warmup"], model_name=retriever.versions["embedding"]["model"])
    if checker is not None and getattr(retriever, "versions", None):
        check_index_consistency(checker, retriever)

    def solve_pair(name: str, problem: Problem) -> SolutionReport:
        cfg = effective_config(base, name)
        pid = f"{run_id}:{name}:{problem.problem_id}"
        tp = trace_path(pid, out / "traces")
        if tp.exists():  # intento anterior interrumpido (sin fila en results.jsonl): se aparta para no mezclar eventos
            (out / "traces" / "interrupted").mkdir(exist_ok=True)
            tp.rename(out / "traces" / "interrupted" / f"{tp.stem}.{int(time.time())}.jsonl")
        if cfg["mode"] == "baseline_auto":
            return run_auto(problem, cfg, checker, agents, pid)
        if cfg["mode"] == "baseline_prover":
            return run_prover_only(problem, cfg, checker, prover, agents, pid)
        if cfg["mode"].startswith("baseline"):
            try:
                return run_baseline(cfg["mode"], problem, cfg, agents, pid, checker)
            except DailyQuotaExhausted:
                raise
            except Exception as e:  # una línea base que explota no puede tumbar la corrida entera
                # ACC-1: si es InvalidLLMOutput, .model existe (el modelo que produjo la salida rota);
                # antes esta fila quedaba con `models=[]`, sin poder saber qué modelo falló.
                models = [e.model] if isinstance(e, InvalidLLMOutput) and getattr(e, "model", None) else []
                return SolutionReport(problem_id=problem.problem_id, config=cfg["name"], status="fail" if isinstance(e, InvalidLLMOutput) else "error", attempts=1, llm_calls=getattr(e, "calls", 0), tokens_in=getattr(e, "tokens_in", 0), tokens_out=getattr(e, "tokens_out", 0), models=models, error_types=[f"llm_output_invalid:{e.agent}" if isinstance(e, InvalidLLMOutput) else f"exception: {type(e).__name__}: {str(e)[:300]}"])
        return orchestrator.solve(problem, cfg, pid)

    pairs = [(n, p) for n in config_names for p in problems if (n, p.problem_id) not in done]
    status = "completed"
    with results_path.open("a", encoding="utf-8") as f, ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(solve_pair, n, p): (n, p) for n, p in pairs}
        try:
            for fut in as_completed(futures):
                name, problem = futures[fut]
                rep = fut.result()
                results.append(rep)
                f.write(rep.model_dump_json() + "\n")
                f.flush()
                print(f"[{name}] {problem.problem_id}: {rep.status} attempts={rep.attempts} llm_calls={rep.llm_calls} used_in_proof={rep.n_used_in_proof}/{rep.n_retrieved} {rep.wall_seconds}s", flush=True)
        except DailyQuotaExhausted as e:
            status = "quota_exhausted"
            print(f"[stop] cuota diaria agotada en todos los modelos ({e}); reanudar mañana con --run-id {run_id}", flush=True)
            for fu in futures:
                fu.cancel()
        except ProverUnavailable as e:
            status = "prover_down"
            print(f"[stop] el demostrador local dejó de responder ({e}); `bash scripts/prover_server.sh start` y reanudar con --run-id {run_id}", flush=True)
            for fu in futures:
                fu.cancel()
    if checker is not None:
        checker.close()
    llms = [llm] + ([drafter.llm] if drafter is not None else [])
    calls, cost = sum(x.calls for x in llms), sum(getattr(x, "cost_usd", 0.0) for x in llms)
    print(f"[cost] {calls} llamadas, {sum(x.tokens_in for x in llms)} tokens de entrada, {sum(x.tokens_out for x in llms)} de salida, US${cost:.4f} (OpenRouter usage.cost)", flush=True)
    summary = summarize(results)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    pending = [(n, p.problem_id) for n in config_names for p in problems if (n, p.problem_id) not in {(r.config, r.problem_id) for r in results}]
    return {"run_id": run_id, "out": str(out), "status": status, "n_done": len(results), "n_pending": len(pending), "summary": summary, "llm_calls_total": calls, "cost_usd_total": round(cost, 4), "models_exhausted": llm.exhausted}


def summarize(results: list[SolutionReport]) -> dict:
    by_cfg: dict[str, list[SolutionReport]] = {}
    for r in results:
        by_cfg.setdefault(r.config, []).append(r)
    summary = {}
    for name, rs in by_cfg.items():
        n = len(rs)
        solved = sum(r.status == "done" for r in rs)
        retrieved = sum(r.n_retrieved for r in rs)
        summary[name] = {
            "n": n,
            "pass@1": round(solved / n, 3) if n else None,
            "pass@1_wilson95": wilson(solved, n),
            "llm_calls_mean": round(sum(r.llm_calls for r in rs) / n, 2) if n else None,
            "tokens_mean": round(sum(r.tokens_in + r.tokens_out for r in rs) / n) if n else None,
            "wall_s_median": sorted(r.wall_seconds for r in rs)[n // 2] if n else None,
            "retrieval_calls_mean": round(sum(r.retrieval_calls for r in rs) / n, 2) if n else None,
            "llm_calls_mean_ci95_bootstrap": bootstrap_mean_ci([r.llm_calls for r in rs]),
            "tokens_mean_ci95_bootstrap": bootstrap_mean_ci([r.tokens_in + r.tokens_out for r in rs]),
            "wall_s_mean_ci95_bootstrap": bootstrap_mean_ci([r.wall_seconds for r in rs]),
            "pass_by_attempt": pass_by_attempt(rs),  # §8.3 robustez del refinamiento: acumulado por intento
            "lemma_use_precision": round(sum(r.n_used_in_proof for r in rs) / retrieved, 3) if retrieved else None,
            "re_retrieval_success": round(sum(r.re_retrievals_ok for r in rs) / sum(r.re_retrievals for r in rs), 3) if sum(r.re_retrievals for r in rs) else None,
            "error_taxonomy": _count_errors(rs),
            "self_assessed_correct": sum(1 for r in rs if r.self_assessed_correct) if any(r.self_assessed_correct is not None for r in rs) else None,
        }
    return summary


def _count_errors(rs: list[SolutionReport]) -> dict:
    counts: dict[str, int] = {}
    for r in rs:
        for e in r.error_types:
            counts[e] = counts.get(e, 0) + 1
    return counts


def pass_by_attempt(rs: list[SolutionReport]) -> dict[str, float]:
    """Fracción resuelta con ≤ i intentos (la ganancia marginal es la diferencia entre columnas)."""
    n = len(rs)
    k_max = max((r.attempts for r in rs), default=1)
    return {str(i): round(sum(1 for r in rs if r.status == "done" and r.attempts <= i) / n, 3) for i in range(1, k_max + 1)} if n else {}


def bootstrap_mean_ci(xs: list[float], n_boot: int = 1000, seed: int = 13) -> list[float] | None:
    """IC 95 % bootstrap de la media (propuesta §8.4: costo y latencia)."""
    import random

    if not xs:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(n_boot))
    return [round(means[int(0.025 * n_boot)], 2), round(means[int(0.975 * n_boot) - 1], 2)]


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--problems", required=True)
    ap.add_argument("--configs", default="full")
    ap.add_argument("--out", default="data/eval/runs")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()
    res = run_suite(load_problems(args.problems), args.configs.split(","), args.out, run_id=args.run_id, workers=args.workers)
    print(json.dumps(res, indent=2, ensure_ascii=False))
