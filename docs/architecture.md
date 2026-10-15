# Arquitectura de RagSolver (estado implementado)

Referencia de diseño: `PLAN_RAG.md` §5-§8; flujo por goal (por defecto desde `prompt_version: "2"`): `PLAN_MEJORA_RAG.md` §4. Este documento describe **lo que existe en el código** al cierre de las Fases 0-6, con los desvíos registrados en `DECISIONS.md`. Correcciones de la auditoría del 23-09 en curso: ver `docs/AUDIT_2026-09-23.md` y `docs/PLAN_CORRECCIONES.md`.

## 1. Vista general

```mermaid
flowchart LR
  subgraph Orq["Orquestador — src/ragsolver/orchestrator/graph.py (LangGraph)"]
    P[PLAN] --> R[RETRIEVE] --> G[GENERATE] --> F[FORMALIZE\nesqueleto con sorry] --> K[CLOSE\n0 LLM: premisas por goal + batería]
    K -- todos cerrados --> V[VERIFY]
    K -- goals abiertos --> FL[FILL\n1 llamada] --> V
    K -- esqueleto no elabora --> C
    V -- ok --> D[DONE]
    V -- error --> C[CRITIQUE]
    C -- retry --> G
    C -- retry + lema faltante --> R
    C -- replan --> P
    C -- abandon / k_max / presupuesto --> X[FAIL]
  end
  subgraph RAG["Módulo RAG — src/ragsolver/rag/"]
    IDX[(LanceDB\nmathlib · informal · figures)]
    RET[Retriever\nhíbrido + RRF + rerank + citas]
    ING1[ingest_mathlib] --> IDX
    ING2[ingest_informal\nMinerU + VLM] --> IDX
    IDX --> RET
  end
  subgraph Lean["Verificador — src/ragsolver/lean/verify.py"]
    REPL[LeanChecker\nREPL lean-interact · Mathlib v4.28.0-rc1]
  end
  LLM[ragsolver/llm.py\nGemini | OpenAI-compatible]
  P & G & F & C --> LLM
  R --> RET
  V --> REPL
  Orq --> LOG[(traza JSONL\nun evento por agente/búsqueda)]
```

## 2. Componentes

| Componente | Módulo | Contrato | Costo LLM |
|---|---|---|---|
| Ingesta Mathlib | `rag/ingest_mathlib.py` | corpus LeanSearch v2 → `MathlibChunk` (`decl_name` = unión de componentes de `name`, #31) | 0 |
| Ingesta PDF | `rag/ingest_informal.py` (+ `mineru_blocks.py`, `figures.py`, `enrich.py`) | MinerU → huecos por capa de texto → QA de fórmulas → cards → figuras descritas → `InformalChunk`/`Figure` | offline (~230/documento, cacheadas) |
| Embeddings | `rag/embed.py` | Qwen3-Embedding-0.6B, documentos sin instrucción, consultas con instrucción por modo; bf16 en CPU | 0 |
| Índice | `rag/index.py` | LanceDB: vector + FTS (`_fts_text`) + prefiltro SQL; tablas `mathlib` (muestra 3 903), `mathlib_full` (278 160, ANN IVF_SQ con re-ranking exacto, #60), `informal`, `figures`; `RAGSOLVER_MATHLIB_TABLE` elige cuál sirve el corpus `mathlib` | 0 |
| Retriever | `rag/retrieve.py` | `RetrievalRequest → RetrievalResult` (§8); modos `nl/formal/error/exemplar`; citas `M*/I*` con localizador | **0** (RNF-3) |
| LLM | `llm.py` | `generate(prompt, schema, images, system)`; Gemini u OpenAI-compatible (OpenRouter con tope de precio, servidor local); cadena de modelos por cuota; tokens y costo exacto por llamada; sin reintentos por JSON inválido (el único reintento lo hace el agente, #59) | 1 por llamada |
| Agentes | `agents/agents.py`, contratos `agents/schemas.py`, prompts `configs/agents.yaml` | Planificador, Generador, Autoformalizador (sólo tácticas), Crítico; líneas base A/B | 1 cada uno |
| Verificador | `lean/verify.py` | `LeanChecker.check(enunciado, tácticas) → Verdict`; el enunciado se empalma desde el problema (#40); tácticas validadas (SEC-1) y axiomas auditados (VER-1) antes de aceptar — §6 | 0 |
| Premisas por goal | `rag/premises.py`, `orchestrator/graph.py::select_premises` | goal de Lean (REPL) → canal A prior de competición (`data/corpus/premise_prior.jsonl`, `scripts/build_premise_prior.py`) filtrado por los símbolos del goal + canal C índice denso con la meta del goal (modo `formal`) → RRF; canal B (selección nativa de Lean) se consume en CLOSE con `grind +suggestions` | 0 |
| CLOSE / FILL | `orchestrator/graph.py::close_goal`, nodos `close`/`fill`; `LeanChecker.goals`/`step` | `goals`: el esqueleto se elabora en modo sonda (nunca da `ok`) y devuelve un goal + `proof_state` por `sorry`; `step`: `ProofStep` validado por `find_forbidden_tactic`; batería `configs/agents.yaml: close.tactics` (la primera que cierra gana; `try?` → script concreto); FILL sólo ve los goals abiertos; `proved` conserva los goals cerrados entre intentos y replans | CLOSE 0, FILL 1 |
| Orquestador | `orchestrator/graph.py`, runner `orchestrator/run.py` | estado con `evidence`, presupuesto ≤ 20 llamadas, `SolutionReport` con citas verificadas/usadas/consultadas; salida inválida persistente = fallo del intento dentro del grafo (#61); runner con hilos (`--workers`), reanudable y con reintento de filas `error` (#50, #58) | — |
| Traza | `logging/jsonl_writer.py` | `TraceEvent` por (problema, agente, intento), versiones fijadas (RNF-2/5) | — |

## 3. Flujo por problema (implementado)

```mermaid
sequenceDiagram
  participant O as Orquestador
  participant R as Retriever (0 LLM)
  participant P as Planificador
  participant G as Generador
  participant F as Autoformalizador
  participant V as Verificador (Lean)
  participant C as Crítico
  O->>R: exemplar(enunciado) → I1..I3
  O->>P: enunciado + ejemplares
  P-->>O: subobjetivos + search_queries
  loop por subobjetivo
    O->>R: nl(statement_nl, search_queries) → M1..Mk
  end
  O->>G: plan + lemas + ejemplares (+ feedback)
  G-->>O: boceto + used_citations
  O->>F: enunciado + boceto + lemas (+ error previo)
  F-->>O: tácticas
  O->>V: theorem (del problema) := by tácticas
  V-->>O: Verdict (ok | errores)
  alt rechazado y k_max no agotado
    O->>C: errores + boceto + historial
    C-->>O: error_type, acción, missing_identifiers
    O->>R: error("unknown identifier 'X'") → X fijado al top-1
    Note over O: retry → G | replan → P | abandon → FAIL
  end
  O-->>O: SolutionReport (citas: verificadas / boceto / consultadas)
```


Con `goal_mode: true` (por defecto; `rag_nl` es el flujo de `eval_v1`), los pasos 2 y 4 cambian:
RETRIEVE ya no consulta Mathlib con lenguaje natural; FORMALIZE escribe un esqueleto `have … := by sorry`;
CLOSE recupera premisas **por goal** y prueba la batería (0 LLM); FILL (1 llamada) cierra los goals restantes;
el archivo final se arma con `fill_sorries` y pasa por VERIFY igual que antes. En un replan, el Planificador
recibe el feedback del Crítico, su estrategia anterior y los goals ya probados (DES-1).

1. **PLAN**: recuperación de ejemplares en el corpus informal (`mode=exemplar`, k=3, 0 llamadas) → Planificador (1 llamada) → subobjetivos con `search_queries` y `math_area`.
2. **RETRIEVE**: por subobjetivo, `queries = [statement_nl] + search_queries` en Mathlib (`k=8`, RRF, sin rerank por RAM — #40); si el Crítico dejó `missing_identifiers`, búsqueda `mode=error` (identificador exacto fijado al top-1). Las citas se acumulan en `evidence`.
3. **GENERATE** (1 llamada): boceto + `used_citations`.
4. **FORMALIZE** (1 llamada): sólo el bloque de tácticas; el archivo Lean se arma con el `theorem` del problema.
5. **VERIFY** (0 llamadas): REPL de Lean con `import Mathlib`; tácticas validadas y axiomas auditados antes de aceptar (§6).
6. **CRITIQUE** (1 llamada si hay Crítico): `error_type` ∈ {lean_syntax, missing_lemma, misformalization, timeout, planning, unknown}, acción retry/replan/abandon.
7. Presupuesto: 3 llamadas por intento + 1 por crítica + 1 por plan → con `k_max=3` ≈ 12-13 ≤ 20.

## 4. Citación (§6.4)

`used_in_proof`: el `decl_name` aparece en el código aceptado por el kernel (mecánico). `used_in_sketch`: declarado por el Generador. Consultadas: todo lo recuperado. Localizadores: Mathlib `{decl_name, module_path, docs_url}`; informal `{doc_id, page_start, page_end, item_number, figure_ids}` + figuras adjuntas `{figure_id, image_path, description, ...}`.

## 5. Datos y versiones

`configs/versions.yaml` fija Lean/Mathlib (`v4.28.0-rc1`), embedder, reranker, parser, proveedor LLM e índice; cada evento de traza copia el pin. Corpus y tablas en `data/` (no versionados en git); la caché de Mathlib compilada vive en `~/ragsolver_cache/lean_project` (#35).

## 6. Modelo de amenazas del verificador (docs/AUDIT_2026-09-23.md SEC-1/VER-1, docs/PLAN_CORRECCIONES.md Fase 1)

Las tácticas que verifica el kernel las escribe un LLM que puede venir de un proveedor no confiable (#62 enruta a varios proveedores de OpenRouter) o estar contaminado por un corpus de terceros (prompt injection vía documentos ingeridos). `lean/verify.py` sigue los niveles de la [guía oficial de Lean para validar pruebas](https://lean-lang.org/doc/reference/latest/ValidatingProofs/):

- **Nivel 1 — validación de tácticas** (`find_forbidden_tactic`, antes de tocar Lean): rechaza `#comando` (en cualquier posición, no sólo al inicio de línea — `open X in #eval ...` es sintaxis válida), metaprogramación (`run_tac`, `macro`, `elab`, `notation`…), declaraciones nuevas (`theorem`, `def`, `axiom`…), cambios de entorno (`set_option`, `import`, `open` salvo `open X in`) y caminos que evitan el kernel (`sorry`, `native_decide`, `decide +native`, `IO.*`). **Confirmado empíricamente** (docs/PLAN_CORRECCIONES.md Fase 1, tarea 7): antes de este cambio, `simp\n#eval IO.println "..."` empalmado como una prueba normal **ejecutaba** el comando inyectado.
- **Nivel 2 — auditoría de axiomas** (`#print axioms <nombre>`, tras un veredicto sin errores): `ok=true` exige que los axiomas usados sean un subconjunto de `propext`, `Classical.choice`, `Quot.sound`. Sin esto, `decide +native` — que el kernel **acepta** — pasaría como "verificado" dependiendo de `Lean.ofReduceBool`/`Lean.trustCompiler` (evaluación del compilador, no del kernel); es el mismo fallo que afectó a [15/225 soluciones publicadas de Kimina-Prover](https://github.com/MoonshotAI/Kimina-Prover-Preview/issues/11).
- **`lean_code` fiel a lo verificado** (BUG-2): `theorem_block`/`build_file` son la única fuente para el REPL y para el reporte — no pueden divergir.

**Lo que esto NO cubre todavía** (Fase 10 de `docs/PLAN_CORRECCIONES.md`, con disparador): no hay sandbox de sistema operativo — un bug del propio elaborador de Lean, o una táctica válida pero que agote CPU/disco (el `maxHeartbeats` sólo limita pasos de elaboración), no está contenido. Antes de exponer la demo fuera de `localhost`, de procesar corpus de terceros sin revisar, o de cambiar a un proveedor LLM no confiable, corresponde el sandbox de sistema operativo (`bwrap` envolviendo `lake_path` de `lean-interact`, ya viable en esta máquina — verificado `/usr/bin/bwrap` presente en WSL2).

Re-verificación offline de `eval-v1` con este verificador (`scripts/reverify_eval_v1.py`, 0 llamadas LLM): **27/27 pruebas antes aceptadas siguen aceptadas, 0 regresiones**, todas con axiomas estándar (DECISIONS #69).
