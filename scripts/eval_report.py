"""Fase 5, tarea 6 — informe de evaluación (docs/eval_report.md) a partir de los JSON
de data/eval/results/ y data/eval/runs/<run_id>/summary.json.
Uso: python scripts/eval_report.py [--run-id run-...]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

R = Path("data/eval/results")


def _load(name: str) -> dict | None:
    p = R / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _metrics_table(res: dict, variants: list[str], metrics: list[str]) -> list[str]:
    head = "| Variante | " + " | ".join(metrics) + " |"
    lines = [head, "|---|" + "---|" * len(metrics)]
    for v in variants:
        if v in res:
            lines.append(f"| {v} | " + " | ".join(f"{res[v][m]:.3f}" for m in metrics) + " |")
    return lines


def _ablation_details(run_dir: Path) -> list[str]:
    """Matriz problema×config, McNemar exacto pareado, falsos positivos evitados, uso de lemas."""
    from itertools import combinations
    from math import comb

    res = [json.loads(l) for l in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    cfgs = list(dict.fromkeys(r["config"] for r in res))
    probs = list(dict.fromkeys(r["problem_id"] for r in res))
    by = {(r["config"], r["problem_id"]): r for r in res}
    ok = lambda c, p: by.get((c, p), {}).get("status") == "done"
    L = ["### Resultado por problema (✔ = prueba aceptada por el kernel)", "", "| Problema | " + " | ".join(cfgs) + " |", "|---|" + "---|" * len(cfgs)]
    for p in probs:
        L.append(f"| `{p}` | " + " | ".join("✔" if ok(c, p) else "–" for c in cfgs) + " |")
    L += ["", "### Comparación pareada (McNemar exacto, α = 0.05; n pequeño → poca potencia)", "", "| A | B | sólo A | sólo B | p |", "|---|---|---|---|---|"]
    for a, b in combinations([c for c in cfgs if c != "baseline_b"], 2):
        n01 = sum(1 for p in probs if ok(a, p) and not ok(b, p))
        n10 = sum(1 for p in probs if ok(b, p) and not ok(a, p))
        n = n01 + n10
        pval = min(1.0, 2 * sum(comb(n, k) for k in range(0, min(n01, n10) + 1)) / 2**n) if n else 1.0
        L.append(f"| {a} | {b} | {n01} | {n10} | {pval:.3f} |")
    if "baseline_b" in cfgs and "baseline_a" in cfgs:
        self_ok = [p for p in probs if by[("baseline_b", p)].get("self_assessed_correct")]
        fp = sum(1 for p in self_ok if not ok("baseline_a", p))
        L += ["", f"**Falsos positivos evitados** (propuesta §8.3): la línea base B se autoevaluó como correcta en {len(self_ok)}/{len(probs)} problemas; en {fp} de ellos la formalización directa (línea base A) **no** pasa el kernel."]
    full = [r for r in res if r["config"] == "full"]
    if full:
        used = [(r["problem_id"], r["n_used_in_proof"], r["n_used_in_sketch"], r["n_retrieved"]) for r in full]
        L += ["", "### Uso de lemas recuperados (config `full`)", "", "| Problema | verificados en la prueba | usados en el boceto | recuperados |", "|---|---|---|---|"]
        L += [f"| `{p}` | {a} | {b} | {c} |" for p, a, b, c in used]
        L += ["", "Pruebas aceptadas (`full`):", ""]
        for r in full:
            if r["status"] == "done" and ":= by" in r["lean_code"]:
                body = r["lean_code"].split(":= by", 1)[1].strip().replace("\n", " · ")[:120]
                L.append(f"- `{r['problem_id']}`: `{body}`")
        models = sorted({m for r in res for m in r["models"]})
        L += ["", f"Modelos LLM usados en la corrida (cadena por cuota diaria): {models}. Re-recuperaciones tras `lema faltante` en `full`: {sum(r['re_retrievals'] for r in full)} (exitosas: {sum(r['re_retrievals_ok'] for r in full)})."]
    return L


def _strata(run_dir: Path, problems_path: Path) -> list[str]:
    """pass@1 por configuración desglosado por split (dev/test), área y nivel de dificultad (§8.1)."""
    from ragsolver.orchestrator.run import wilson

    meta = {json.loads(l)["problem_id"]: json.loads(l) for l in problems_path.read_text(encoding="utf-8").splitlines() if l.strip()}
    res = [json.loads(l) for l in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    res = [r for r in res if r["problem_id"] in meta]
    cfgs = list(dict.fromkeys(r["config"] for r in res))
    L = ["### Desglose por estrato (pass@1, n) — `" + problems_path.name + "`", ""]
    for key, title in (("split", "partición"), ("area", "área"), ("difficulty", "nivel de dificultad")):
        values = sorted({meta[r["problem_id"]].get(key) for r in res}, key=str)
        L += [f"**Por {title}**", "", "| " + title + " | " + " | ".join(cfgs) + " |", "|---|" + "---|" * len(cfgs)]
        for v in values:
            cells = []
            for c in cfgs:
                rs = [r for r in res if r["config"] == c and meta[r["problem_id"]].get(key) == v]
                k = sum(r["status"] == "done" for r in rs)
                cells.append(f"{k}/{len(rs)} ({k / len(rs):.2f}) {wilson(k, len(rs))}" if rs else "–")
            L.append(f"| {v} | " + " | ".join(cells) + " |")
        L.append("")
    return L


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None, help="carpeta en data/eval/runs con summary.json")
    ap.add_argument("--out", default="docs/eval_report.md")
    ap.add_argument("--problems", default=None, help="JSONL de problemas para desglosar por split/área/nivel")
    args = ap.parse_args()

    L = ["# Evaluación del RAG y del pipeline — RagSolver", "", "Generado por `scripts/eval_report.py`. Metodología: `docs/PLAN_RAG.md` §10-§11; decisiones y correcciones: `docs/DECISIONS.md`.", ""]

    full = _load("fase1_redo_mathlibqr_sample3903_norerank.json")
    s200 = _load("fase1_redo_mathlibqr_sample3903_100q.json")
    if full:
        L += ["## 1. Retriever sobre Mathlib (MathlibQR, LeanSearch v2)", "", f"Tabla de muestra: gold de MathlibQR/MPR + premisas de miniF2F + 3000 distractores. Cobertura del gold en la tabla: **{full['n_gold_covered_in_table']}/{full['n_queries']} ({full['coverage_ratio']:.1%})**. Métricas crudas (el recall condicionado a cobertura = crudo ÷ cobertura).", "", f"### 1a. {full['n_queries']} consultas, sin rerank", ""]
        L += _metrics_table(full, ["denso", "bm25", "hibrido"], ["recall@10", "ndcg@10", "mrr"])
        if s200:
            L += ["", f"### 1b. Muestra aleatoria de {s200['n_queries']} consultas (semilla fija), 4 variantes", ""]
            L += _metrics_table(s200, ["denso", "bm25", "hibrido", "hibrido_rerank"], ["recall@10", "ndcg@10", "mrr"])
        fidx = _load("fase1_full_mathlibqr_100q.json")
        if fidx:
            L += ["", f"### 1c. Misma muestra de {fidx['n_queries']} consultas sobre el **índice completo de Mathlib** (`mathlib_full`, 278 160 declaraciones; DECISIONS #48, #56)", ""]
            L += _metrics_table(fidx, ["denso", "bm25", "hibrido", "hibrido_rerank"], ["recall@10", "ndcg@10", "mrr"])
            L += ["", "Los números de la tabla de muestra (1a-1b) están inflados por el tamaño del pool (3 903 candidatos); 1c es la medida realista del retriever en producción."]
        ann = _load("ann_mathlib_full.json")
        if ann:
            L += ["", f"**Índice ANN** (`{ann['index_type']}`, {ann['num_partitions']} particiones, nprobes {ann['nprobes']}, refine ×{ann['refine_factor']}; DECISIONS #60): recall@10 respecto de la búsqueda exacta **{ann['ann_recall@10_vs_exact']}**, recall@50 {ann['ann_recall@50_vs_exact']} ({ann['n_queries']} consultas sintéticas); latencia densa p50 {ann['latency_exact_s']['p50']} s → **{ann['latency_ann_s']['p50']} s** (p95 {ann['latency_exact_s']['p95']} → {ann['latency_ann_s']['p95']} s)."]
        fann = _load("fase1_full_ann_mathlibqr_100q.json")
        if fann:
            L += ["", f"### 1d. Misma muestra sobre el índice completo **con el índice ANN** (el que usan la demo y los agentes con `RAGSOLVER_MATHLIB_TABLE=mathlib_full`)", ""]
            L += _metrics_table(fann, ["denso", "bm25", "hibrido", "hibrido_rerank"], ["recall@10", "ndcg@10", "mrr"])
        L.append("")

    prem = _load("fase5_premises.json")
    if prem:
        L += ["## 2. Premisas gold de miniF2F (pruebas verificadas de DeepSeek-Prover-V2)", "", f"Problemas: {prem['n_problems']} (con ≥1 premisa en la tabla: {prem['n_problems_with_covered_premises']}); cobertura de premisas en la tabla: {prem['premise_coverage']:.1%}; rerank: {prem['rerank']}.", "", "| Consulta | recall@8 | recall@20 | ndcg@10 | mrr |", "|---|---|---|---|---|"]
        pfull = _load("fase5_premises_full.json")
        for label, res in (("tabla de muestra", prem), ("índice completo", pfull)):
            for mode in ("nl", "formal"):
                m = (res or {}).get(f"query_{mode}")
                if m:
                    L.append(f"| {'enunciado informal' if mode == 'nl' else 'enunciado formal (Lean)'} ({label}) | {m['recall@8']:.3f} | {m['recall@20']:.3f} | {m['ndcg@10']:.3f} | {m['mrr']:.3f} |")
        L.append("")

    inf = _load("fase5_informal_gold.json")
    if inf:
        fd = inf["figure_dependent"]
        L += ["## 3. Gold informal (30 consultas sobre el PDF de ejemplo)", "", f"- Recall@5: **{inf['recall@5']:.3f}** (Wilson 95 %: {inf['recall@5_wilson95']}); MRR: {inf['mrr']:.3f}", f"- Consultas dependientes de figura (n={fd['n']}): Recall@5 = {fd['recall@5']:.3f}; cobertura de entidades en las descripciones de figura = {fd['figure_coverage_mean']}", ""]

    cont = _load("fase5_contamination.json")
    if cont:
        L += ["## 4. Contaminación corpus informal ↔ miniF2F-v2", "", f"- Pares con similitud > {cont['threshold']}: **{cont['n_pairs_over_threshold']}** de {cont['n_cards']}×{cont['n_minif2f']}; similitud máxima observada: {cont['max_similarity']:.3f}", f"- Top-3 más parecidos (card, problema, sim): {cont['top10_most_similar'][:3]}", ""]

    if args.run_id:
        run_dir = Path("data/eval/runs") / args.run_id
        summ = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        L += [f"## 5. Ablaciones end-to-end (`{args.run_id}`)", "", "| Config | n | pass@1 | IC95 Wilson | llamadas LLM (media) | tokens (media) | wall s (mediana) | precisión uso de lemas | re-recuperación OK | taxonomía de errores |", "|---|---|---|---|---|---|---|---|---|---|"]
        for name, s in summ.items():
            L.append(f"| {name} | {s['n']} | {s['pass@1']} | {s['pass@1_wilson95']} | {s['llm_calls_mean']} | {s['tokens_mean']} | {s['wall_s_median']} | {s['lemma_use_precision']} | {s['re_retrieval_success']} | {s['error_taxonomy']} |")
        L += ["", *_ablation_details(run_dir), ""]
        if args.problems:
            L += _strata(run_dir, Path(args.problems)) + [""]
        g = lambda c, m: summ.get(c, {}).get(m)
        if all(c in summ for c in ("full", "no_rag", "no_critic", "baseline_a")):
            res = [json.loads(l) for l in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
            ok = {(r["config"], r["problem_id"]) for r in res if r["status"] == "done"}
            probs = {r["problem_id"] for r in res}
            disc = lambda a, b: (sum((a, p) in ok and (b, p) not in ok for p in probs), sum((b, p) in ok and (a, p) not in ok for p in probs))
            pba = g("full", "pass_by_attempt") or {}
            first = pba.get("1", pba.get(1))
            claims = [r["problem_id"] for r in res if r["config"] == "baseline_b" and r.get("self_assessed_correct")]
            confirmed = sum(any((c, p) in ok for c in ("full", "no_rag", "no_critic", "baseline_a")) for p in claims)
            L += [
                "### Lectura (métricas de la propuesta §8.3)",
                "",
                f"- **Contribución del RAG** = pass@1(full) − pass@1(sin RAG) = {g('full','pass@1')} − {g('no_rag','pass@1')} = **{round(g('full','pass@1') - g('no_rag','pass@1'), 3)}** (pares discordantes {disc('full', 'no_rag')}); el contexto recuperado multiplica los tokens por {round(g('full','tokens_mean') / g('no_rag','tokens_mean'), 1)}×; precisión de uso de lemas = {g('full','lemma_use_precision')}.",
                f"- **Robustez del refinamiento** = pass@1(full) − pass@1(sin Crítico) = **{round(g('full','pass@1') - g('no_critic','pass@1'), 3)}** (pares discordantes {disc('full', 'no_critic')}); dentro de `full`, pass@1 tras el 1.er intento = {first} y final = {g('full','pass@1')}: la ganancia atribuible a los reintentos es la diferencia entre ambos; el resto de la brecha con `sin Crítico` (mismo primer intento) es variación entre corridas.",
                f"- **Eficiencia**: {g('full','llm_calls_mean')} llamadas LLM por problema en `full` (tope RNF-3: 20), {g('baseline_a','llm_calls_mean')} en la línea base A.",
                f"- **Verificación formal vs. autoevaluación**: la línea base B se declaró correcta en {len(claims)}/{len(probs)} problemas; el kernel confirma {confirmed} de esos en alguna configuración verificada.",
                "",
            ]

    Path(args.out).write_text("\n".join(L), encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
