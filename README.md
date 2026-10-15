# RagSolver

Sistema multiagente de razonamiento verificable para problemas matemáticos de
nivel olimpiada: planificador → recuperador (RAG) → generador → autoformalizador
→ verificador (Lean 4 + Mathlib) → crítico. Proyecto del curso de IA, UPTC.

- Plan de acción completo (arquitectura, fases, esquema de metadatos, stack): [`docs/PLAN_RAG.md`](docs/PLAN_RAG.md)
- Decisiones y su justificación: [`docs/DECISIONS.md`](docs/DECISIONS.md)
- Contratos JSON Schema del módulo RAG: [`docs/interfaces/`](docs/interfaces/)
- Versiones fijadas (Lean, Mathlib, modelos, índice): [`configs/versions.yaml`](configs/versions.yaml)

## Estado

Ver [`docs/PROGRESS.md`](docs/PROGRESS.md). Resumen: Fases 0-6 implementadas (índice de
Mathlib completo, 278 160 declaraciones embebidas en GPU; ingesta del PDF de ejemplo con
100/100 problem cards y 53 figuras; retriever híbrido con citas; agentes LangGraph + verificador
Lean 4 vía REPL; ablaciones; demo Gradio; scripts de publicación en HF). **Experimento principal
(`eval_v1`: 60 problemas miniF2F-v2 × 5 configuraciones, DeepSeek V4 Flash) terminado**: el sistema
completo prueba 9/60 (15 %), frente a 5/60 de un LLM que escribe Lean directamente y 4/60 sin el
Crítico; el RAG no cambia la tasa de éxito (9/60 con y sin RAG); un LLM sin verificación dice haber
resuelto 55/60 y el kernel confirma 11. Detalle en [`docs/eval_report.md`](docs/eval_report.md) y en el
informe final en inglés [`docs/final_report.md`](docs/final_report.md) (§6-§7).
QA de la ingesta: [`docs/qa_ingest_edd3436ca148.md`](docs/qa_ingest_edd3436ca148.md).

## Arrancar

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv sync --extra dev --extra ingest --extra demo   # `uv sync` a secas deja fuera pytest/MinerU/Gradio (docs/DECISIONS.md #29)
python -m pytest tests/ -q
```

Lean 4 + Mathlib (pinneado a `v4.28.0-rc1`, ver `configs/versions.yaml`):

```bash
source "$HOME/.elan/env"     # instala elan primero: ver lean_project/lakefile.toml
cd lean_project && lake update && lake exe cache get && lake build
```

## Corpus informal (Fase 2)

Requiere la salida de MinerU en `data/parsed/<doc_id>/mineru/` y `GEMINI_API_KEY`
en `.env` (tier gratis; ver `configs/versions.yaml: vlm_enrichment`).

```bash
python -m ragsolver.rag.ingest_informal --pdf "problemas resueltos olimpiadas.pdf"   # --skip-llm: sin enriquecimiento
python -m ragsolver.rag.embed_corpus --in data/corpus/informal.jsonl --out data/corpus/informal_embedded.jsonl
python -m ragsolver.rag.index --in data/corpus/informal_embedded.jsonl --table informal
python scripts/qa_ingest_report.py --doc-id edd3436ca148
```

## Búsqueda (Fase 3)

```bash
ragsolver search "prove that 2^2001 + 3^2001 is divisible by 7" --corpus informal --mode exemplar --k 3
ragsolver search "gcd is commutative" --corpus mathlib --k 8            # híbrido denso+BM25, RRF, rerank
ragsolver search "unknown identifier 'Nat.Prime.two_le'" --mode error   # identificador exacto en el top-1
python -m ragsolver.rag.evaluate --limit 20                             # MathlibQR vía Retriever.search
```

`Retriever.search(RetrievalRequest) -> RetrievalResult` (contratos en `docs/interfaces/`):
0 llamadas LLM, citas con `decl_name`/`docs_url` o `doc_id`/página/figuras, y un evento
JSONL por búsqueda en `data/eval/traces/`. Plantillas por modo y flags de ablación en
`configs/retrieval.yaml`.

## Agentes y evaluación (Fases 4-5)

Requiere `OPENROUTER_API_KEY` en `.env` (DeepSeek V4 Flash; o `provider: gemini` + `GEMINI_API_KEY`, ver
`configs/versions.yaml`) y la caché de Mathlib en ext4 (`~/ragsolver_cache/lean_project`,
`docs/DECISIONS.md` #35) — sin ella el verificador usa `lean_project/` del repo (lento en `/mnt/c`).

```bash
python -m ragsolver.orchestrator.run --problems data/eval/problems/dev_trivial.jsonl --configs full,no_rag,no_critic,baseline_a,baseline_b
python scripts/build_premise_gold.py && python scripts/eval_premises.py      # premisas gold de miniF2F
python scripts/eval_informal_gold.py && python scripts/contamination.py    # gold informal y contaminación
python scripts/eval_report.py --run-id <run_id>                             # docs/eval_report.md
bash scripts/run_chain.sh                                                   # experimento principal completo y reanudable (eval_v1 → índice completo → §6.3)
python scripts/fill_final_report.py --run-id eval-v1                        # tablas de §6.3 del informe final
```

## Demo y publicación (Fase 6)

```bash
python demo/app.py                                              # Gradio: pestañas "Buscar" y "Resolver"
HF_TOKEN=... python scripts/publish_hf.py --repo <usuario>/ragsolver-index --dry-run   # dataset: índice Mathlib + evaluación (sin el PDF)
```

## Documentación

- [`docs/PROGRESS.md`](docs/PROGRESS.md) — estado por fase · [`docs/DECISIONS.md`](docs/DECISIONS.md) — decisiones con evidencia
- [`docs/architecture.md`](docs/architecture.md) · [`docs/install.md`](docs/install.md) · [`docs/interfaces.md`](docs/interfaces.md) · [`docs/reproduce.md`](docs/reproduce.md)
- [`docs/eval_report.md`](docs/eval_report.md) — resultados del experimento principal (y [`eval_report_dev.md`](docs/eval_report_dev.md), ablación de desarrollo) · [`docs/final_report.md`](docs/final_report.md) — informe final (inglés, IMRaD) · [`docs/qa_ingest_edd3436ca148.md`](docs/qa_ingest_edd3436ca148.md) — QA de la ingesta del PDF

## Estructura

Ver `docs/PLAN_RAG.md §5.2` para el layout completo del repositorio y el
razonamiento detrás de cada carpeta.
