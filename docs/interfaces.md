# Contratos de interfaz (RNF-4)

Todos los contratos son modelos Pydantic v2; los JSON Schema en `docs/interfaces/*.schema.json` se regeneran con `python scripts/export_schemas.py` y **son derivados del código, no al revés**.

## Módulo RAG (`src/ragsolver/rag/schema.py`, `PLAN_RAG.md` §7-§8)

| Esquema | Uso |
|---|---|
| `Document` | metadatos por fuente (`doc_id`, licencia, `redistributable`, parser) |
| `ChunkBase` / `MathlibChunk` / `InformalChunk` | filas de las tablas LanceDB `mathlib` / `informal` |
| `Figure` | tabla `figures` (recorte, bbox, descripción VLM, `detected_by`) |
| `RetrievalRequest` | entrada del retriever: `queries[1-4]`, `mode`, `corpora`, `k`, `n_candidates`, `filters`, `rerank`, `include_figures`, `context` |
| `RetrievalResult` | `hits[]` (con `figures[]` y `citation`), `citations[]`, `trace` |
| `Citation` | `citation_id` (`M1..`/`I1..`), `locator`, `scores`, `used_in_proof`, `used_in_sketch`, `snippet` |
| `TraceEvent` | evento JSONL por búsqueda/llamada (RNF-5) con `VersionsPin`; en llamadas LLM `request` lleva `model`, `tokens_in`, `tokens_out`, `cost_usd` (exacto, OpenRouter) y `decision` (salida validada) |

## Agentes (`src/ragsolver/agents/schemas.py`, propuesta §6.3)

| Esquema | Agente | Campos clave |
|---|---|---|
| `Problem` | entrada | `informal_statement`, `formal_statement` (`... := by sorry`), `header` (`open ...`), `imports` |
| `PlannerOutput` | Planificador | `strategy`, `subgoals[1-6]` con `statement_nl`, `formal_hint`, `search_queries[≤3]`, `math_area` |
| `GeneratorOutput` | Generador | `sketch`, `used_citations[]` |
| `FormalizerOutput` | Autoformalizador | `proof_tactics` (sólo el bloque tras `:= by`), `notes` |
| `CriticOutput` | Crítico | `error_type`, `action` (retry/replan/abandon), `missing_identifiers[]`, `feedback` |
| `BaselineAOutput` / `BaselineBOutput` | líneas base A / B | A: `proof_tactics` (obligatorio); B: `natural_language_proof` + `self_assessed_correct` (obligatorios) |
| `Verdict` | Verificador | `ok`, `errors[{line,col,message}]`, `uses_sorry`, `axioms[]` (auditoría VER-1: `ok=true` exige subconjunto de `propext`/`Classical.choice`/`Quot.sound`), `seconds` |
| `SolutionReport` | orquestador | estado, intentos, llamadas, tokens, `lean_code`, `citations[]` clasificadas, taxonomía de errores, re-recuperaciones |

## Configuración

- `configs/versions.yaml`: pins (Lean/Mathlib, modelos, proveedor LLM y cadena de modelos, índice).
- `configs/retrieval.yaml`: plantillas de consulta por modo, `rrf_k`, `max_per_module`, flags de ablación del retriever.
- `configs/agents.yaml`: `k_max`, `llm_budget`, recuperación de los agentes, backend Lean, configuraciones de ablación, prompts versionados.
- `configs/ingest.yaml`: filtros de Mathlib, parámetros del PDF, prompts de enriquecimiento (versionados).
