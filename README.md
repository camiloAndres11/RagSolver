# RagSolver

Sistema de razonamiento verificable para problemas matemáticos de nivel olimpiada: un
**demostrador especializado local** (Goedel-Prover-V2-8B en la GPU, 0 API) más agentes por API
(planificador → recuperador RAG → generador → autoformalizador → crítico), con **toda prueba
verificada por el kernel de Lean 4 + Mathlib**. Proyecto del curso de IA, UPTC.

**Resultado actual** (eval-v3, 30 problemas de miniF2F-v2 pareados): **21/30** frente a 8/30 del
sistema anterior (McNemar p = 0.0002), incluidos dos problemas de IMO. Cómo funciona:
[`docs/diagramas.md`](docs/diagramas.md) · qué cambió y por qué: [`docs/Mejorasuwu.md`](docs/Mejorasuwu.md).

## Usarlo en 3 comandos

```bash
uv sync --extra dev --extra ingest --extra demo     # dependencias (ver docs/install.md para Lean + Mathlib)
bash scripts/demo_up.sh                             # demostrador en la GPU + demo en http://127.0.0.1:7860
bash scripts/prover_server.sh stop                  # al terminar, libera la GPU
```

El demostrador necesita la GPU (llama.cpp/Vulkan, `docs/install.md` §7); sin ella el sistema corre sólo
con los agentes por API.

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

## Demostrador local y DRAFT (octubre 2026)

```bash
bash scripts/prover_server.sh start                             # Goedel-Prover-V2-8B en la GPU (puerto 8082, sólo red WSL↔Windows)
RAGSOLVER_MATHLIB_TABLE=mathlib_full python -m ragsolver.orchestrator.run --problems data/eval/problems/eval_v1_round1.jsonl --configs baseline_prover,full --run-id <nombre> --workers 2
```

Configuraciones nuevas: `baseline_prover` (sólo el demostrador), `baseline_prover32`, `dsp` (el modelo de la
API escribe plan y prueba informal y el demostrador los recibe en su prompt, con lemas del RAG). El respaldo
por agentes de API está apagado por defecto (`configs/agents.yaml: prover.api_fallback`).

## Demo y publicación (Fase 6)

```bash
bash scripts/demo_up.sh                                         # Gradio: pestañas "Buscar" y "Resolver" (con el demostrador)
HF_TOKEN=... python scripts/publish_hf.py --repo <usuario>/ragsolver-index --dry-run   # dataset: índice Mathlib + evaluación (sin el PDF)
```

Publicado en Hugging Face: [código](https://huggingface.co/CamiloS1111/ragsolver) y [índices + evaluación](https://huggingface.co/datasets/CamiloS1111/ragsolver-index) (`docs/user_guide.md`).

## Documentación

- [`docs/PROGRESS.md`](docs/PROGRESS.md) — estado por fase · [`docs/DECISIONS.md`](docs/DECISIONS.md) — decisiones con evidencia
- [`docs/architecture.md`](docs/architecture.md) · [`docs/install.md`](docs/install.md) · [`docs/interfaces.md`](docs/interfaces.md) · [`docs/reproduce.md`](docs/reproduce.md)
- [`docs/eval_report.md`](docs/eval_report.md) — resultados del experimento principal (y [`eval_report_dev.md`](docs/eval_report_dev.md), ablación de desarrollo) · [`docs/final_report.md`](docs/final_report.md) — informe final (inglés, IMRaD) · [`docs/qa_ingest_edd3436ca148.md`](docs/qa_ingest_edd3436ca148.md) — QA de la ingesta del PDF

## Estructura

Ver `docs/PLAN_RAG.md §5.2` para el layout completo del repositorio y el
razonamiento detrás de cada carpeta.

## Licencia y cita

Código bajo [Apache-2.0](LICENSE) © 2026 Camilo Andres Niño Amaya y Jose L. Salamanca (UPTC Sogamoso). Para citarlo: [`CITATION.cff`](CITATION.cff).
Datos y modelos de terceros conservan su licencia: Mathlib y el corpus de LeanSearch v2 (Apache-2.0), miniF2F-v2 (Apache-2.0), Goedel-Prover-V2 (Apache-2.0), Lean Workbook proofs (MIT), Qwen3-Embedding (Apache-2.0). El PDF de olimpiadas y todo lo derivado de él (`pdf_set.jsonl`, texto del corpus informal) no se redistribuye.
