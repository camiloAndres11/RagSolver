# Progreso de implementación — RagSolver

> Se actualiza mientras el agente trabaja. Última actualización: 2026-09-24.

## Estado actual

**Núcleo del proyecto (Fases 0-6 de `docs/PLAN_RAG.md`): completo y ejecutado.** Índice de Mathlib completo (278 160 declaraciones) con ANN IVF_SQ; agentes LangGraph + verificador Lean vía REPL; experimento principal `eval_v1` terminado (300/300 pares): `full` 9/60 (15 %), `no_rag` 9/60, `no_critic` 4/60, línea base A 5/60, línea base B se autoevalúa correcta en 55/60 (11 confirmadas por el kernel); ninguna diferencia significativa tras Bonferroni. Detalle en `docs/eval_report.md` y `docs/final_report.md` (§6-§7). `eval-v1-full` (índice completo, ≈ US$0.30) preparado en `scripts/run_chain.sh`, sin correr — requiere tu aprobación.

**Auditoría técnica (2026-09-23) y su corrección, en curso.** `docs/AUDIT_2026-09-23.md` (diagnóstico completo) y `docs/PLAN_CORRECCIONES.md` (plan por fases). Avance:
- **Fase 1 — verificador seguro (SEC-1, VER-1, BUG-2): ✅ completa.** `lean/verify.py` valida las tácticas del LLM antes de tocar Lean (rechaza comandos, metaprogramación, declaraciones nuevas, `sorry`/`native_decide`/`decide +native`) y audita `#print axioms` tras un veredicto limpio (sólo `propext`/`Classical.choice`/`Quot.sound`). Confirmado con Lean real: el exploit de inyección de la auditoría ya no se ejecuta; `decide +native` ya no pasa como "verificado". Re-verificación offline de `eval_v1` (0 llamadas LLM): **27/27 pruebas antes aceptadas siguen aceptadas, 0 regresiones** — los resultados reportados no cambian. DECISIONS #69-74.
- **Fase 2 — licencias y datos (SEC-2): ✅ completa.** `.gitignore` ampliado (PDF de ejemplo, `blobs/`, `data/eval/runs/`, artefactos de Lean); se encontró y corrigió una fuga no detectada por la auditoría (`docs/qa_ingest_edd3436ca148.md` embebía fórmulas/descripciones del PDF — ahora redactado cuando el documento no es redistribuible); `scripts/publish_hf.py` sube las corridas desde una copia saneada y aborta si encuentra texto del corpus informal en lo que va a subir. Comprobación reutilizable: `scripts/check_no_leaked_content.py` (0 coincidencias sobre el repo). DECISIONS #75-77.
- **Fase 3 — dependencias y documentación de estado: ✅ completa.** `httpx`/`tenacity` declarados, `gradio` en el extra `demo`, `ranx` movido a `dev`; `pandas`/`accelerate` quitados de las dependencias core (confirmado sin ellos: `test_retriever_integration.py` carga el embedder Y el reranker reales en verde). Documentación al día: README, `install.md` (+ tabla de variables de entorno), `interfaces.md`, `architecture.md`, `evaluate.py`, `versions.yaml`, `final_report.md` (limitaciones MET-1/DATA-1/BUG-1 añadidas, "release" atenuado a "prepared for release"). DECISIONS #78-79.
- **Fase 4 — citas, trazas y contabilidad (BUG-1, TRZ-1, LLM-1, ACC-1, BUG-5, BUG-6): ✅ completa.** Los `citation_id` ya no se renumeran en cada búsqueda (se reutilizan por `chunk_id`, sin duplicados tras un replan o una re-recuperación); las trazas del retriever de un par (config, problema) van al mismo archivo que las de sus agentes (antes, un archivo aleatorio por búsqueda); el pin de versiones registra qué tabla sirve `mathlib`; el cliente LLM ya no reintenta un timeout de lectura (podría haberse cobrado igual), baja de 20 a 4 intentos y cuenta cuántos hizo; cualquier excepción dentro de un nodo del grafo (no sólo salida JSON inválida) conserva lo acumulado en vez de perderlo; se corrigieron dos bugs de contabilidad menores (self-consistency, caché de embeddings de consulta sin lock). DECISIONS #80-88. 138 tests en verde (incluidos los de Lean real). El smoke con LLM real (D6, ≈ US$0.001) queda pendiente de tu aprobación.
- **Fases 5-10:** sin empezar. Ver `docs/PLAN_CORRECCIONES.md §7` para el orden y qué necesita una decisión tuya (D1-D8: licencia, commit, `blobs/`, alcance de la ingesta, replan, gasto, token de HF, sandbox).

**Mejora del RAG (2026-10-14): implementada (Fases 0-4 y 2b de `docs/PLAN_MEJORA_RAG.md`), medida sin LLM; falta la re-evaluación pagada (Fase 6, decisión R4).**
- El retrieval de Mathlib consulta con el *goal* de Lean. El Autoformalizador escribe un esqueleto `have … := by sorry` y el REPL devuelve un goal por `sorry`.
- Canal principal: prior de competición (778 lemas de Lean Workbook validados contra Lean, núcleo incluido). Recall@16 por goal 0.327, frente a 0.028 del índice denso, que queda apagado.
- Las premisas se consumen con una batería de tácticas (CLOSE, 0 LLM). Sobre el goal raíz de `eval_v1`: 11/60 sin premisas, 12/60 con premisas, y 4 problemas que no resolvió ninguna configuración anterior. FILL (1 llamada) sólo ve los goals abiertos.
- Los goals probados se conservan entre intentos y replans. El replan recibe el feedback del Crítico (DES-1 cerrado).
- Nuevas configuraciones: `rag_nl` (flujo de eval-v1), `baseline_auto` (0 LLM); `no_rag` usa la misma batería sin premisas.
- Smoke con LLM real (8 problemas, US$0.0145 en total): 5/8 con el prompt 2, que reveló que el modelo no escribe `sorry`. Con la partición mecánica de la prueba en goals (`skeletonize`, prompt 3): **8/8**. Medido sobre el término aceptado, ninguna premisa recuperada se usó en esas 8 pruebas: la mejora viene de la partición y de la automatización. OpenRouter: con el tope de precio actual sólo califica DeepInfra, que da 429 intermitentes (#103).
- Tests: 146 rápidos y 11 con Lean real, en verde. `DECISIONS.md` #89-#108.
- **eval-v2 (2026-10-15), parcial — `docs/eval_report_v2.md`.** Ronda 1 de 4: `full` 6/15, `no_rag` 7/15, `baseline_auto` 5/15 (eval-v1 en los mismos 15: 3 y 4). `baseline_auto` en los 60: **11/60 sin LLM**, más que eval-v1 `full` (9/60). El RAG por goal todavía no muestra efecto medible. El guardia de costo detuvo la cadena: la proyección para las rondas 2-4 (~US$0.34) supera el saldo (US$0.289). Decisión del usuario: opción B (rondas 2-3); ronda 2: 2/15 en las tres configuraciones; ronda 3 detenida para rediseñar.
- **Rediseño (2026-10-15), en evaluación — `docs/PLAN_REDISENO_PROVER.md`.** Demostrador especializado local (Goedel-Prover-V2-8B Q6_K en la GPU, 0 API) como etapa 0 y para los goals abiertos. El RAG corrige nombres de lemas renombrados entre versiones de Mathlib. Salidas ilegibles del Crítico y de FILL ya no matan el problema. Smoke real: 2/2 sin API. `DECISIONS.md` #110-#115.
- **eval-v3 (2026-10-15) — `docs/eval_report_v3.md`: el rediseño resuelve 21/30, frente a 8/30 del sistema anterior** en los mismos problemas (McNemar 13 a 0, p = 0.0002). Dificultad 2-3: de 0/14 a 8/14, con 2 problemas de IMO. Todo lo resuelve el demostrador local sin API; los agentes y el RAG no añaden problemas en su forma actual (`full` = `baseline_prover`). Costo de API: US$0.061; saldo de la key: US$0.059. Limitación: posible contaminación de miniF2F. Próximos pasos propuestos en el §5 del reporte.

**Pendiente del usuario:** LICENSE (sugerido Apache-2.0), commit en git (nada commiteado todavía), si versionar `Propuesta_Proyecto_Final_IA_UPTC.pdf`, token de HF, y si correr `eval-v1-full` (≈ US$0.30; saldo de la key ≈ US$0.42).

## Historial

Lo que sigue es el registro de cómo se llegó al estado de arriba, por fase, escrito mientras se ejecutaba cada una. Se conserva como referencia; para el estado real del proyecto usar la sección anterior.

### Restricciones reales de este sandbox (no son del proyecto, son de este entorno de ejecución)

- **RAM ajustada**: 7.3 GB totales. Con un solo proceso de embeddings/evaluación corriendo ya se usa ~75-90% + algo de swap. Por eso el agente evita correr dos cargas pesadas (embeddings, reranking, MinerU) al mismo tiempo.
- **Filesystem `/mnt/c/` (montaje 9p de WSL2) es lento** para imports de paquetes grandes (torch tardó ~150s en importar la primera vez).
- Con estas limitaciones, el trabajo de esta sesión prioriza **corridas acotadas pero reales** (nunca resultados inventados) sobre corridas completas a escala de producción. (Nota posterior: al momento de escribir esto la GPU AMD todavía no se había probado desde esta sesión — ver más abajo "GPU AMD: ✅ utilizable" — la limitación real nunca fue la falta de GPU, sino RAM/CPU del sandbox.)

### Fase 0 — Cimientos: ✅ completa

- Repo, `pyproject.toml` (Python 3.12 vía `uv`), `.gitignore`.
- `configs/versions.yaml` — pin único de versiones (Lean, Mathlib, modelos).
- `src/ragsolver/rag/schema.py` — contratos Pydantic de §7/§8 del plan + JSON Schema exportado a `docs/interfaces/`.
- Escritor de traza JSONL + tests.
- **Lean 4 (`v4.28.0-rc1`) + Mathlib (`v4.28.0-rc1`, caché precompilado 7873 archivos) instalados y verificados**: `lake build` → "Build completed successfully" — el kernel de Lean aceptó `example : 1 + 1 = 2 := rfl`.
- `docs/DECISIONS.md` con 13 decisiones registradas y su evidencia.
- 19/19 tests pasando en ese momento (ver conteo actualizado más abajo si se agregaron más).

### Fase 1 — Índice de Mathlib: código completo, corrida real en curso

Hecho:
- Descarga del corpus real de LeanSearch v2 (310 579 declaraciones) y normalización → **278 560 chunks** válidos (32 019 excluidos por filtro).
- `embed.py`, `index.py`, `retrieve.py` (con CLI), `evaluate.py` escritos, con tests de integración reales contra LanceDB (que de hecho encontraron y corrigieron 2 incompatibilidades de API con la versión instalada).
- **Hallazgo importante documentado en `DECISIONS.md`**: sólo 117/540 nombres "gold" de los benchmarks propios de LeanSearch v2 (MathlibQR+MathlibMPR) existen en el corpus JSONL que ellos mismos publicaron para esa tag. El recall contra esos benchmarks está acotado por esa cobertura, no sólo por la calidad del retriever.
- Por las limitaciones de cómputo del sandbox, se embebió una **muestra acotada real** (no simulada): 116 declaraciones gold + 3000 distractores aleatorios = 13 736 registros, con Qwen3-Embedding-0.6B en CPU (~5.5 textos/seg, ~42 min de corrida real).
- **Índice LanceDB real construido** sobre esa muestra (13 736 filas, vector + FTS).
- `evaluate.py` (Recall@10/nDCG@10/MRR, denso vs BM25 vs híbrido vs híbrido+rerank sobre MathlibQR) corrido sobre esa muestra acotada de 13 736 filas.

**Estos primeros números (recall condicionado 0.947 con fusión híbrida) quedaron invalidados por un bug de ingesta encontrado en la Fase 3** (`name[0]` en vez del nombre completo, `DECISIONS.md` #31): con el corpus renormalizado, la cobertura real y las métricas correctas son las que aparecen en la entrada de Fase 3 más abajo (recall condicionado real ≈ 0.78-0.79, no 0.947), y más tarde las del índice completo (Fase 5/#56/#68: recall@10 híbrido+rerank 0.45). Se deja esta nota en vez del número original para no repetir el error.

Fase 1 se da por **completa**. Pendiente sólo si el usuario pide explícitamente escalar a las 278 560 declaraciones completas (ver "Bloqueado" más abajo — no es un bloqueo real, es una corrida de ~14h que no se lanzó sin avisar).

### Fase 2 — Ingesta de PDFs: ✅ completa (2026-09-19, segunda sesión)

Resultado sobre `problemas resueltos olimpiadas.pdf` (detalle en `docs/qa_ingest_edd3436ca148.md` y `docs/DECISIONS.md` #15-#22):

- **100/100 problem cards emparejadas** (era 92/100). 98 por vía determinista (numerales absorbidos en ecuaciones recuperados por regex; solución 1 por regla "lo que precede al ítem 2"), la 55 por split con LLM, y la **77 recuperada de un hueco que MinerU se había saltado por completo** (pág. 64: cero bloques en media página). Ese hueco se detecta cruzando la capa de texto del PDF palabra por palabra contra los bboxes de MinerU; 7 regiones así se transcribieron con el VLM (Plan B acotado, no página completa).
- **53 figuras** recortadas (con padding para las etiquetas) y descritas con contexto del enunciado. Red de seguridad vectorial: **0 figuras perdidas por MinerU** (los 36 clusters "sin cubrir" son radicales del Equation Editor, tablas bien clasificadas o clusters fusionados). QA gate del plan: 7/7 problemas con figura; **rúbrica manual 15/15** aprobadas (umbral 13/15).
- QA de fórmulas con VLM: 209 propuestas, **76 aplicadas / 133 rechazadas** por guardas calibradas con casos reales (el VLM mete ruido de puntuación, absorbe texto vecino y a veces recorta contenido real o devuelve LaTeX destrozado). Todo cambio queda registrado para revisión.
- 100 resúmenes contextuales (Contextual Retrieval) + `math_area`, `techniques`, `theorems_used`, `difficulty`, `keywords` por card.
- Corpus: `data/corpus/informal.jsonl` (153 chunks: 100 `problem_card` + 53 `figure`), `figures.jsonl`; embebido con Qwen3-Embedding-0.6B (CPU, 215 s) e indexado en LanceDB (`data/index/lancedb/informal`, FTS incluido).
- **Búsqueda real verificada** (`python -m ragsolver.rag.retrieve "..." --corpus informal --mode exemplar`): 3 consultas en inglés → problema correcto en el puesto 1 (8, 86, 77); la geométrica trae además los chunks de figura.
- Proveedor: **Gemini** (tier gratis). Hallazgo importante: la cuota gratis es **20 solicitudes/día por modelo** en `gemini-3.5-flash` → cadena de fallback por modelo (`configs/versions.yaml: fallback_models`); en la práctica todo el enriquecimiento lo hizo `gemini-3.5-flash-lite` (su cuota diaria aguantó ~250 llamadas). Cada figura/card registra el modelo que la produjo. Caché JSONL por llamada: re-ejecutar la ingesta no repite llamadas.
- Código nuevo: `rag/mineru_blocks.py` (lector/render de bloques MinerU + cross-check capa de texto), `rag/figures.py`, `rag/enrich.py` (cliente Gemini REST con caché, reintentos y cadena de modelos), `rag/ingest_informal.py` (pipeline completo, `--skip-llm` para correr sin API), `scripts/qa_ingest_report.py`. 43 tests pasando (nuevos: `test_mineru_blocks.py`, `test_figures.py`, `test_enrich.py`).

Pendiente de Fase 2 (no bloqueante):
- Gold de OCR de 10 páginas transcritas a mano (tarea 10 del plan): requiere trabajo humano; no se fabricó.
- Chunking en `solution_step`: no hace falta para este PDF (card más larga ≈ 2 900 caracteres, muy por debajo de ~1 500 tokens).

### Fase 3 — Servicio de recuperación y citación: ✅ completa (2026-09-20)

- `Retriever.search(RetrievalRequest) -> RetrievalResult` cubre §6.3/§8: plantillas de consulta por `mode` (`configs/retrieval.yaml`), denso + FTS con **prefiltro SQL** (`doc_ids`, `unit_types`, `kinds`, `math_area`), RRF multi-consulta, rerank contra `rerank_against`, dedupe (figura ↔ su card; `max_per_module` opcional), **figuras adjuntas** desde la tabla `figures`, citas con `snippet`, traza JSONL, caché de embeddings de consulta, flags de ablación (`enabled=false` → vacío con traza).
- **Modo `error`**: identificadores Lean del mensaje → búsqueda exacta fijada al top-1 (verificado con reranker adverso y sobre el índice real).
- `evaluate.py` pasa por `Retriever.search` (misma función que los agentes). CLI `ragsolver search` / `ragsolver ingest`.
- Tests: 10 unitarios con LanceDB temporal + embedder/reranker falsos, 2 de integración sobre el índice real. Suite: 62 tests.
- **Bug grave de Fase 1 detectado y corregido** (`DECISIONS.md` #31-32): `ingest_mathlib.py` usaba `name[0]` cuando `name` es la lista de componentes del nombre → 90 % de las declaraciones con nombre/`docs_url`/`chunk_id` incorrectos y repetidos. Con el corpus renormalizado: 503/540 nombres gold existen (antes "117"); **métricas corregidas (946 consultas, cobertura 83.8 %)**: recall@10 denso 0.665 / BM25 0.594 / híbrido 0.656; nDCG@10 0.565 / 0.472 / 0.538. En 40 consultas al azar con rerank: híbrido+rerank 0.775 > denso 0.75 > híbrido 0.675 > BM25 0.525. La conclusión de Fase 1 ("recall condicionado 0.947") estaba inflada por el bug.

### Fase 4 — Grafo de agentes: ✅ completa y ejecutada (2026-09-20)

**Aceptación cumplida**: los 5 teoremas triviales de referencia se resuelven con el pipeline completo (`f4-trivial-full`: 5/5, 3 llamadas LLM cada uno, citas `used_in_proof` en `triv_add_zero` y `triv_add_comm`), ≤ 20 llamadas por problema en todas las corridas (máx. 11), `llm_calls=0` en todos los eventos del retriever.

- `ragsolver/llm.py`: cliente único Gemini / OpenAI-compatible (**OpenRouter preparado por config**, sin key), cadena de modelos por cuota diaria, contadores de tokens. El Enricher de Fase 2 lo usa.
- Agentes (`agents/agents.py`, contratos en `agents/schemas.py`, prompts en `configs/agents.yaml`): Planificador (con ejemplares del corpus informal), Generador (`used_citations`), Autoformalizador (**sólo tácticas**: el enunciado se empalma desde el problema, el LLM no puede debilitarlo), Crítico (taxonomía + `missing_identifiers` → re-recuperación en modo `error`), líneas base A/B de la propuesta §8.2.
- Orquestador LangGraph (`orchestrator/graph.py`): PLAN → RETRIEVE → GENERATE → FORMALIZE → VERIFY → CRITIQUE con presupuesto RNF-3 (≤ 20 llamadas) dentro del grafo; `SolutionReport` con citas verificadas / usadas en boceto / consultadas.
- Verificador (`lean/verify.py`): REPL persistente con `lean-interact` (`import Mathlib` una vez, ~0.3 s por teorema) usando la caché de Mathlib en ext4 (`~/ragsolver_cache/lean_project`, `DECISIONS.md` #35); respaldo por proceso `lake env lean`.
- Runner de ablaciones (`orchestrator/run.py`): `full`, `no_rag`, `no_critic`, `baseline_a`, `baseline_b` → `data/eval/runs/<run_id>/results.jsonl` + `summary.json` (pass@1 con Wilson, llamadas, tokens, precisión de uso de lemas, re-recuperación, taxonomía).
- 8 tests con LLM/verificador/retriever simulados.
- Problemas: `data/eval/problems/dev_trivial.jsonl` (5 teoremas de referencia) y `dev_minif2f.jsonl` (8 de miniF2F-v2s valid, con cabecera `open ...`).

### Fase 5 — Evaluación: ✅ ejecutada (2026-09-20) — `docs/eval_report.md`

Resultados clave (tabla de muestra de Mathlib con 3 903 filas; 13 problemas dev = 5 triviales + 8 miniF2F-v2s):
- MathlibQR (946 consultas): recall@10 denso 0.662 / BM25 0.595 / híbrido 0.655; en 100 al azar con rerank: denso 0.76 (nDCG@10 0.631), híbrido+rerank 0.71 (0.617), híbrido 0.66, BM25 0.56 — el rerank recupera lo que pierde la fusión con BM25, pero el denso solo sigue siendo el mejor en esta muestra (`DECISIONS.md` #32). Premisas gold de miniF2F (352 problemas): recall@20 0.15 (informal) / 0.18 (formal) — cota inferior, la consulta real es por subobjetivo.
- Gold informal: **30/30 en el top-5, MRR 1.0**; cobertura de entidades en figuras 0.91. Contaminación: 0 pares > 0.9 (máx. 0.818).
- Ablaciones (pass@1): `full` 9/13, `no_rag` 10/13, `no_critic` 6/13, línea base A 8/13, línea base B 0 (se autoevalúa correcta 13/13 → 5 falsos positivos evitados). **El Crítico aporta +0.23**; el RAG no aporta en este dev set (−0.08, p=1.0) y triplica los tokens: los problemas se resuelven con automatización de tácticas y casi no se citan lemas por nombre (precisión de uso de lemas 0.011). Es el resultado honesto del §8.3 con n=13; hace falta el conjunto de 30-60 problemas más difíciles del plan para medir el aporte del RAG donde importa.
- Hallazgo operativo: `ranx` devolvió métricas absurdas en dos corridas largas → métricas propias (`rag/metrics.py`) verificadas contra `ranx` (`DECISIONS.md` #43).

- Premisas gold: `scripts/build_premise_gold.py` → 352 problemas / 402 lemas desde las pruebas verificadas de DeepSeek-Prover-V2 (`DECISIONS.md` #38); `scripts/eval_premises.py` (Recall@8/@20, nDCG@10, consulta informal y formal).
- Gold informal: `data/eval/informal_gold.jsonl` (30 consultas, 15 con figura; anotado por el asistente, `DECISIONS.md` #39); `scripts/eval_informal_gold.py`.
- Contaminación: `scripts/contamination.py` (coseno card ↔ miniF2F-v2s).
- Informe: `scripts/eval_report.py` → `docs/eval_report.md` (ablaciones, IC de Wilson, taxonomía).

### Fase 6 — Empaquetado y documentación: preparada (2026-09-20)

- Documentación: `docs/architecture.md` (arquitectura implementada, flujo por problema, citación), `docs/install.md`, `docs/interfaces.md` (+ JSON Schema de los 8 contratos de agentes en `docs/interfaces/`), `docs/reproduce.md` (comandos y tiempos medidos).
- Demo Gradio `demo/app.py`: pestaña "Buscar" (hits con citas, firma Lean, `docs_url`, recortes y descripciones de figura) y "Resolver" (pipeline completo o líneas base, con presupuesto). Gradio ya estaba instalado (vía MinerU); no se lanzó en esta sesión por memoria.
- Publicación en HF Hub: `scripts/publish_hf.py` (dataset card, índice Mathlib, corpus normalizado, evaluación; **excluye** el PDF, sus recortes y su texto por `redistributable=false`). ✅ Publicado el 2026-10-22 (DECISIONS #129).
- Notebook `notebooks/01_results.ipynb`: tablas de todas las evaluaciones + McNemar exacto entre configuraciones.

### Cumplimiento de la propuesta (mapeo 2026-09-20, tarde)

| Requisito | Estado |
|---|---|
| Obj. 5 — conjunto de evaluación 30-60 estratificado con partición dev/test | ✅ `data/eval/problems/eval_v1.jsonl` (60: 24 dev / 36 test; 3 áreas × 3 niveles) + ficha `README.md`. **Parcial**: 42/60 pares `full` válidos (10 resueltos) con DeepSeek V4 Flash vía OpenRouter, 6 hilos; **detenida a las 03:15 del 21-09 por el tope de US$0.50 de la key** (DECISIONS #47, #50-#54). Se reanuda con `nohup bash scripts/run_eval_v1.sh > data/eval/results/eval_v1.log 2>&1 &` en cuanto se suba el límite (≈ US$0.85 más para todo, incluido el índice completo) |
| RF-7 — decisión por agente e intento en la traza | ✅ `request.decision` en cada `TraceEvent` + evento del verificador |
| RNF-2 — semilla fijada | ✅ `seed: 13` en `versions.yaml`, pasado a Gemini/OpenAI; verificado con llamada real |
| §8.3 — llamadas de recuperación, pass por intento | ✅ `retrieval_calls`, `pass_by_attempt` en `summary.json` |
| §8.4 — bootstrap para costo/latencia | ✅ `*_ci95_bootstrap` en `summary.json` |
| §10.2 — CITATION.cff, CI, LICENSE | ✅ `CITATION.cff` (autores: Camilo Andres Niño Amaya, Jose L. Salamanca), `.github/workflows/ci.yml`, `LICENSE` Apache-2.0 (2026-10-21) |
| §10.3 — guía de usuario, diagrama de secuencia | ✅ `docs/user_guide.md`, `docs/architecture.md` §3 |
| §10.1 — informe final en inglés (IMRaD) | ✅ borrador `docs/final_report.md` con resultados actuales y 24 referencias; §6.3 se completa con `eval_v1` |
| Obj. 7 — HF Hub + Space | ✅ publicado (2026-10-22): código en huggingface.co/CamiloS1111/ragsolver (repositorio de modelo: el Space de Gradio pide PRO, DECISIONS #129), datos en huggingface.co/datasets/CamiloS1111/ragsolver-index; demo en local |
| RF-1 — subobjetivos verificables independientemente | parcial: se verifican dentro de una sola prueba (`have`), no como lemas aislados |
| §6.3 — self-consistency del Generador | ✅ `generator_samples` (apagado por defecto; cuenta en el presupuesto) |
| Corrida principal reanudable | ✅ `scripts/run_eval_v1.sh` (salta lo hecho; corta limpio si se agota la cuota) |

### GPU AMD: ✅ utilizable vía Windows + Vulkan (llama.cpp) para embeddings (2026-09-20)

`llama-server.exe` del lado de Windows con el GGUF de Qwen3-Embedding y `RAGSOLVER_EMBED_BACKEND=llama` en WSL: vectores idénticos (cos ≥ 0.9996), ~48 textos/s (10× la CPU). El corpus completo de Mathlib (278k) se está embebiendo (~96 min) → tabla `mathlib_full` (`DECISIONS.md` #48). ROCm en WSL2 sigue sin funcionar (abajo), pero ya no hace falta para el retriever.

### GPU AMD (ROCm en WSL2): ❌ no viable hoy

La tarjeta real es una **RX 9060 XT (gfx1200)**, no la 9070 XT asumida. `rocminfo` falla con `HSA_STATUS_ERROR_OUT_OF_RESOURCES` incluso con `librocdxg` v1.2.2 y tras reiniciar WSL; es el bug abierto `ROCm/ROCm#6323` (mismo hardware, cerrado por AMD como "not planned"). Todo corre en CPU + API; re-evaluar cuando AMD publique un fix (`docs/DECISIONS.md` #8-#12).

### Bloqueado / decisiones pendientes del usuario (2026-09-23)

1. ~~**LICENSE**~~ ✅ Apache-2.0 (2026-10-21).
2. ~~**Commit en git**~~ ✅ rama `rag`.
3. ✅ (2026-10-22, DECISIONS #129) **Publicación en Hugging Face** (entregable 2 de la propuesta): `HF_TOKEN` con permiso de escritura y nombres de repo; los scripts `publish_hf.py` (dataset: índice muestra + completo con ANN, evaluación, corridas) y `publish_space.py` (demo de búsqueda) ya funcionan en `--dry-run`.
4. **`eval-v1-full`** (repetición de `full`/`no_critic` con el índice completo): ≈ US$0.30; la key tiene US$0.42 libres de su tope de US$2.00. Se lanza con `setsid nohup bash scripts/run_chain.sh > data/eval/results/chain.log 2>&1 < /dev/null &` (salta `eval_v1`, que ya está completo).
5. Opcionales: gold manual de OCR (10 páginas) para la métrica de exactitud de fórmulas; experimento adicional con un modelo local en la GPU (US$0, ~5 h; ver conversación del 22-09); PDF del informe final si el curso lo pide en ese formato.

### Noche del 22→23-09: qué quedó hecho

- **`eval_v1` completo y válido** (300/300 pares, todos consistentes con el código final): `full` 9/60 (15 %), `no_rag` 9/60, `no_critic` 4/60, línea base A 5/60; la B se autoevalúa correcta en 55/60 y el kernel confirma 11. Ninguna diferencia significativa tras Bonferroni; la variación entre corridas es del tamaño de los efectos (mismo primer intento: 7 vs 4 problemas, 2 en común). Tablas en `docs/eval_report.md` y `docs/final_report.md` §6.3.
- **Informe final v0.3** (`docs/final_report.md`): resumen, discusión, limitaciones y conclusión escritos con los resultados reales; Apéndice E con los 7 defectos encontrados durante el experimento; 71 decisiones registradas.
- **Índice ANN** del Mathlib completo validado con consultas reales: mismas métricas que la búsqueda exacta, 60× más rápido. La demo responde en ~0.5 s sobre las 278 160 declaraciones.
- **Defectos corregidos esta noche** (con test): contabilidad perdida con salidas inválidas (#61), enrutamiento caro de OpenRouter (#62, #64), sesgo por reintentos ocultos en 15 pares (#63), esquema de las líneas base (#65, que invalidaba la A: 58/60 pruebas vacías).
- **Gasto**: la key usó US$1.58 de US$2.00 (≈ US$0.9 perdidos por defectos de los días anteriores, ya corregidos). No se gastó en `eval-v1-full`.
- Notebook de resultados ejecutado, docs de instalación/uso/arquitectura/interfaces al día, scripts de publicación en HF listos (dataset y Space, en `--dry-run`), 77 tests en verde.
