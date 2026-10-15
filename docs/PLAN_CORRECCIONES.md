# Plan de corrección — hallazgos de la auditoría del 2026-09-23

> Documento de ejecución para el agente implementador. Fuente de los hallazgos (IDs SEC-, VER-, BUG-, …): [`AUDIT_2026-09-23.md`](AUDIT_2026-09-23.md).
> Fecha: 2026-09-23. Idioma de trabajo: español; código, prompts y comentarios de código siguen en el idioma que ya usa cada archivo.

---

## 0. Cómo usar este documento

- Plan **por fases**. Cada fase trae objetivo, hallazgos que cierra, tareas numeradas, archivos, criterios de aceptación medibles, plan B, esfuerzo y costo en US$. No se pasa a la siguiente fase sin cumplir los criterios de aceptación, salvo que la sección 7 marque que dos fases son independientes.
- **Reglas de trabajo** (valen para todas las fases):
  1. **Corrección mínima en el lugar correcto.** Rige la misma regla de `PLAN_RAG.md §0` (sin sobreingeniería, sin abstracciones de una sola implementación). Un arreglo va en la función por la que pasan todos los llamadores, no en cada llamador.
  2. **Todo bug empieza con un test de regresión que falla**; después viene el arreglo.
  3. **Toda decisión o desvío se registra en `docs/DECISIONS.md`**, con el formato existente y numeración a partir de **#69**.
  4. **Si cambia un contrato Pydantic**, se regeneran los JSON Schema con `python scripts/export_schemas.py` y se comprueba que siguen sincronizados.
  5. **Los resultados ya reportados de `eval-v1` no se reescriben en su sitio.** Si un arreglo cambia una cifra publicada, se recalcula desde los datos guardados (0 llamadas LLM), se escribe en un archivo nuevo y se actualiza el informe con una nota del cambio.
  6. **Ninguna llamada de pago sin aprobación explícita del usuario.** La key tiene un tope de US$2,00 y quedan unos US$0,42.
  7. **Operación en esta máquina** (7,5 GB de RAM, repo en `/mnt/c`):
     - Un solo proceso con modelos cargados a la vez.
     - Corridas largas con `setsid nohup … < /dev/null &` y log persistente en `data/eval/results/`.
     - Nunca `uv add` ni `uv sync` con una corrida en marcha. Siempre `uv sync --extra dev --extra ingest` (más `--extra demo` desde la Fase 3).
     - El REPL de Lean se usa desde `~/ragsolver_cache/lean_project`.
  8. **Después de cambiar código**, ejecutar `graphify update .` (lo pide `CLAUDE.md`).
  9. **No hacer commit ni push** salvo que el usuario lo pida explícitamente.
  10. **Cierre de cada fase:** la suite pasa (`uv run pytest -q --deselect tests/test_retriever_integration.py`, más la integración cuando aplique) y la documentación afectada queda actualizada en la misma fase.

---

## 1. Decisiones que necesita el usuario

| # | Decisión | Se necesita en | Opción recomendada |
|---|---|---|---|
| D1 | Licencia del repositorio | Fase 9 (antes del commit) | Apache-2.0, coherente con `CITATION.cff`, Mathlib, miniF2F-v2 y Qwen3 |
| D2 | Commit y push de la rama (habilita el CI) | Fase 6.4 y Fase 9 | Tras cerrar las Fases 1–3 |
| D3 | Qué hacer con `blobs/` (caché de MinerU con el texto del PDF) | Fase 2 | Ignorarlo en git; borrarlo sólo si el usuario lo confirma |
| D4 | Alcance de la ingesta informal | Fase 5 | Guard de un solo documento con metadatos correctos; multidocumento sólo si hay un segundo PDF real |
| D5 | Replan | Fase 7 | Pasar el feedback del Crítico al Planificador (0 llamadas extra); alternativa: `max_replans: 0` |
| D6 | Gasto | Fases 4.8, 7.1 y 9.4 | Smoke de 5 problemas triviales (≈ US$0,001); `eval-v1-full` (≈ US$0,30) |
| D7 | `HF_TOKEN` y nombres de los repos | Fase 9.3 | — |
| D8 | Sandbox a nivel de sistema operativo | Fase 10 | Obligatorio antes de exponer la demo fuera de localhost |

---

## 2. Estado del arte consultado y decisiones

| Tema | Qué hacen los proyectos de referencia | Decisión para RagSolver | Por qué |
|---|---|---|---|
| Validar pruebas no confiables | La guía oficial de Lean ([Validating a Lean Proof](https://lean-lang.org/doc/reference/latest/ValidatingProofs/)) define niveles: (1) el kernel acepta; (2) `#print axioms` con sólo `propext`, `Classical.choice` y `Quot.sound` (avisa de que `Lean.trustCompiler` delata evaluación nativa); (3) replay con `lean4checker`; (4) `comparator` en sandbox. Advierte que "las tácticas y el metacódigo pueden realizar acciones arbitrarias". | **Ahora, nivel 2**: lista blanca de axiomas y rechazo de comandos y metaprogramación en las tácticas. **Nivel 3/4 opcional**, sólo para las pruebas que se publiquen (Fase 10). | Cierra SEC-1 y VER-1 con pocas líneas y sin infraestructura nueva. |
| Replay del entorno | [SafeVerify](https://github.com/GasStationManager/SafeVerify): `Environment.replay` más la lista blanca de los 3 axiomas; lo usa el leaderboard de PutnamBench, y Mistral mantiene un fork para su evaluación ([LeanstralSafeVerify](https://github.com/mistralai/LeanstralSafeVerify)). Rechaza `native_decide`. | La misma lista blanca. Replay con SafeVerify sólo como verificación opcional de las pruebas publicadas. | Reutiliza una herramienta estándar en lugar de escribir un replay propio. |
| Juez de referencia | [leanprover/comparator](https://github.com/leanprover/comparator) (Lean FRO, 2026): compila en un sandbox `landrun`, exporta con `lean4export` y hace replay con el kernel oficial y con kernels independientes. | No adoptarlo por ahora. | Necesita Landlock, que **no está activo** en este kernel de WSL2 (verificado: `landlock` no aparece en `/sys/kernel/security/lsm`), y un `.olean` por problema. |
| Precedente | En los artefactos miniF2F publicados de Kimina-Prover, [15/225 soluciones dependían de `Lean.ofReduceBool`](https://github.com/MoonshotAI/Kimina-Prover-Preview/issues/11). | VER-1 se trata como un riesgo real, no teórico. | Un prover publicado cayó exactamente en este fallo. |
| Sandbox en WSL2 | bubblewrap funciona en WSL2 porque usa namespaces de usuario, y Landlock es sólo una capa adicional ([Codex linux-sandbox](https://github.com/openai/codex/blob/main/codex-rs/linux-sandbox/README.md), [lince #356](https://github.com/RisorseArtificiali/lince/issues/356)). | bubblewrap mediante un script envoltorio pasado como `lake_path` de `lean-interact` (Fase 10). | Verificado aquí: `/usr/bin/bwrap` está instalado, `max_user_namespaces = 29869`, y `LeanREPLConfig(lake_path=…)` es configurable, así que no hay que parchear la librería. |
| Reintentos de LLM | Lección propia (DECISIONS #59): los reintentos invisibles gastaron unos US$0,9. | No reintentar timeouts de lectura (pueden haberse cobrado) y contar cada intento. | Mismo tipo de fallo que LLM-1. |
| Reranker con poca RAM (2026-09-23, a pedido del usuario) | llama.cpp sirve Qwen3-Reranker de forma nativa por `/v1/rerank` (`--reranking --pooling rank --embedding`), con conversión GGUF oficial ya publicada ([`ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF`](https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF); confirmado funcionando en el hilo de la propia model card de Qwen, [discusión #22](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/discussions/22)). | Reranker de los agentes por GPU vía Vulkan (Fase 7.7), mismo patrón que el embedder (#48); no se espera a una máquina con más RAM. | `tools/llama-vulkan/llama-server.exe` ya instalado es un binario genérico de llama.cpp (sirve cualquier GGUF); `/dev/dxg` confirma el passthrough de GPU activo en esta máquina; el reranker (~1,8 GB) cabe junto al embedder en los 16 GB de VRAM de la RX 9060 XT sin tocar los 7,3 GB de RAM de WSL. |

---

## 3. Mapa hallazgo → fase

| Hallazgo | Severidad | Fase | Acción |
|---|---|---|---|
| SEC-1 | HIGH | 1 (y 10) | Validación de tácticas y del enunciado de la demo; sandbox de sistema operativo con disparador |
| VER-1 | HIGH | 1 | Auditoría de axiomas con lista blanca |
| SEC-2 | HIGH | 2 | `.gitignore` y publicación saneada |
| ING-1 | HIGH | 5 | Guard, metadatos reales, `section_path` real |
| BUG-1 | MEDIUM | 4 | IDs de cita estables |
| BUG-2 | MEDIUM | 1 | Una sola fuente Lean para el REPL y el reporte |
| BUG-3 | MEDIUM | 5 | CLI con `argparse` |
| BUG-4 | MEDIUM | 5 | Rutas ancladas a la raíz del repo |
| LLM-1 | MEDIUM | 4 | Política de reintentos |
| DES-1 | MEDIUM | 7 | Replan con feedback (D5) |
| TRZ-1 | MEDIUM | 4 | `run_id` en `RetrievalContext` |
| MET-1 | MEDIUM | 3 (doc), 4 (pin), 7 (tabla por defecto) | Documentar, registrar la tabla servida, `mathlib_full` por defecto |
| DATA-1 | MEDIUM | 3 (doc), 7 (medición), 10 (corpus) | Limitación documentada; disparador medido |
| DEP-1 | MEDIUM | 3 | `pyproject.toml` |
| TEST-1…4, CI-1 | MEDIUM/LOW | 1, 4, 5, 6 | Tests por fase y CI en funcionamiento |
| ACC-1, BUG-5, BUG-6 | LOW | 4 | Contabilidad completa |
| MET-2 | LOW | 7 | Redefinir la métrica |
| ARQ-2 | LOW | 5 | Versiones sólo desde `versions.yaml` |
| ARQ-3 | LOW | 8 | `channels` como parámetro del constructor |
| ARQ-4 | LOW | — | **No se corrige**: una reconstrucción atómica de `mathlib_full` (1,1 GB de vectores) no cabe cómodamente en 7,5 GB. Se documenta el riesgo; la tabla se puede regenerar desde el JSONL embebido |
| ARQ-5 y §9 (Ponytail) | INFO | 8 | Limpieza |
| ARQ-1 | LOW | 10 | Con disparador |
| SEC-3 | LOW | 3 (doc), 10 | Documentar el `llama-server` en `0.0.0.0` y cerrarlo con el sandbox |
| DES-2 | INFO | 7 | Sólo documentar |
| DES-3 | INFO | — | Ya reconocido en `final_report.md §7`; sin acción |
| DOC-1…12 | — | 3 y la fase que corresponda | Ver cada fase |
| Tareas del PLAN sin hacer (§3.A de la auditoría) | — | 1.5, 5, 7.3, 10 | Test índice ↔ Lean, etiqueta de cita, taxonomía del retriever; el resto con disparador |

---

## 4. Fases

### Fase 1 — Verificador seguro y sólido

**Cierra:** SEC-1, VER-1, BUG-2, TEST-1 y el test de arranque índice ↔ Lean de `PLAN_RAG §12`. **Esfuerzo:** M. **Costo:** US$0.

**Objetivo.** Que "verificado" signifique: el kernel aceptó la prueba usando sólo `propext`, `Classical.choice` y `Quot.sound`. Que el texto generado por el LLM no pueda ejecutar comandos. Y que el `lean_code` reportado sea exactamente el código que se verificó.

**Tareas**

1. **Una sola fuente Lean por intento** (`lean/verify.py`). Una función arma, a partir del problema y las tácticas, el texto que se envía al REPL: la cabecera `open …` sin imports, `set_option maxHeartbeats N in` y el enunciado con las tácticas empalmadas. Esa misma función la usan:
   - `LeanChecker.check`;
   - el `lean_code` del `SolutionReport`, tanto en el grafo (`graph.py:143`) como en las líneas base (`run.py:59`);
   - el backend `lake`, mientras exista.

   `lean_code` pasa a ser los imports de configuración seguidos de esa fuente: un archivo autocontenido que reproduce el entorno del REPL. Sobre `Problem.imports` adicionales: comprobar que ningún conjunto de problemas actual los usa y rechazar con un error explícito los problemas que los traigan, en lugar de ignorarlos en silencio.

2. **Validación previa de las tácticas.** Una función pura que reemplaza a `_FORBIDDEN`:
   - Quita antes los comentarios (`--` y `/- … -/`).
   - Compara tokens completos, con límites de palabra.
   - Rechaza:
     - comandos que empiezan por `#` (`#eval`, `#print`, `#exit`, `#check`, …);
     - metaprogramación: `run_tac`, `run_cmd`, `run_elab`, `elab`, `elab_rules`, `macro`, `macro_rules`, `syntax`, `notation`, `infix`, `infixl`, `infixr`, `prefix`, `postfix`;
     - atributos y código no confiable: `attribute`, `@[`, `unsafe`, `implemented_by`, `extern`;
     - cambios de entorno: `set_option` (el presupuesto de heartbeats lo fija el sistema), `import`;
     - declaraciones: `theorem`, `lemma`, `def`, `abbrev`, `instance`, `example`, `axiom`, `opaque`, `structure`, `inductive`, `class`, y `namespace` / `section` / `end` al principio de una línea;
     - pruebas no verificadas por el kernel: `native_decide`, `+native`, `ofReduceBool`, `trustCompiler`, `sorry`, `admit`;
     - `IO.`.
   - Se permite `open … in`, que es una táctica legítima.
   - El rechazo devuelve un `Verdict` con un error legible, para que el Crítico pueda corregirlo en el siguiente intento.

3. **Auditoría de axiomas.**
   - Cuando el veredicto sale sin errores, el sistema (no el LLM) ejecuta `#print axioms <nombre del teorema>` sobre el entorno que devolvió el REPL.
   - Se acepta sólo si los axiomas son un subconjunto de {`propext`, `Classical.choice`, `Quot.sound`}. Cualquier otro (`sorryAx`, `Lean.ofReduceBool`, `Lean.trustCompiler`, un axioma nuevo) da `ok=false` con el error "non-standard axioms: …".
   - El nombre del teorema se saca de `formal_statement`. Si el enunciado es un `example` sin nombre, al empalmar se declara como `theorem` con un nombre fijo; eso no debilita el enunciado.
   - `Verdict` gana el campo `axioms: list[str]`: regenerar los esquemas y registrar el cambio en DECISIONS.

4. **Demo** (`demo/app.py`).
   - El enunciado formal que escribe el usuario pasa por la misma validación y debe ser exactamente un `theorem` o `lemma` que termina en `:= by sorry`.
   - La pestaña "Resolver" se niega a ejecutar si el servidor escucha en una dirección que no es de loopback.

5. **Consistencia índice ↔ Lean** (`PLAN_RAG §12`).
   - En el precalentamiento del runner: `#check` de 20 `decl_name` elegidos al azar (semilla fija) de la tabla servida, excluyendo nombres privados y auxiliares.
   - Se escribe en el log cuántos existen. Si son menos de 19/20, se muestra un aviso, sin abortar.
   - Cuesta segundos, porque el REPL ya está caliente.

6. **Tests sin Lean** (`tests/test_verify.py`, archivo nuevo).
   - Deben rechazarse: la inyección exacta de la auditoría (`simp` seguido de `#eval IO.println …`), `decide +native`, `exact Lean.ofReduceBool _ _ rfl`, `run_tac`.
   - Deben aceptarse: un comentario que contiene la palabra "axiom"; tácticas legítimas como `nlinarith [sq_nonneg (a - b)]`, `norm_num [Nat.Prime] at h ⊢`, `decide`, `omega`, `interval_cases x <;> simp_all`.
   - Parseo de la salida de `#print axioms`, con fixtures de mensajes reales capturados una vez del REPL.
   - `lean_code` del reporte == texto recibido por un checker falso que registra lo que le llega.

7. **Test lento con Lean real.**
   - Marcador `slow`, registrado en `pytest.ini`; se salta si no existe la caché de Lean.
   - Comprueba que la inyección nunca llega al REPL y que `decide +native` sobre `2 ^ 10 = 1024` queda rechazado por la auditoría de axiomas.
   - Se ejecuta una vez en local y el resultado se anota en DECISIONS.

8. **Re-verificación offline de `eval-v1`** (0 llamadas LLM, sólo el REPL: unos 5 minutos de import más unos segundos).
   - Extraer las tácticas de los `lean_code` de las 27 filas `done` y verificarlas de nuevo con el verificador nuevo.
   - Pasar la validación de tácticas sobre los `lean_code` finales de las 300 filas, para medir falsos positivos.
   - Salida en un archivo nuevo, `data/eval/runs/eval-v1/reverify.json`; `results.jsonl` no se modifica.

9. **Documentación.**
   - Nueva sección "Modelo de amenazas del verificador" en `architecture.md`: qué se valida y qué no (no hay sandbox de sistema operativo hasta la Fase 10).
   - Entrada #69 en DECISIONS.

**Aceptación**
- Los tests nuevos pasan. El test lento se ejecutó una vez en local: la inyección fue rechazada y `+native` también.
- Re-verificación: **27/27 siguen aceptadas y sólo usan los 3 axiomas estándar**. Si alguna no pasa, la discrepancia queda en DECISIONS y en `final_report.md §6.3/§8` con las cifras corregidas.
- **0 falsos positivos** de la validación sobre las 27 pruebas aceptadas.
- Esquemas sincronizados y `graphify update .` ejecutado.

**Plan B**
- Si la salida de `#print axioms` no se puede leer de forma fiable del REPL, el sistema emite un `#eval` propio con `Lean.collectAxioms` sobre la constante. Es código del arnés, no del LLM, así que no contradice la tarea 2.
- Si la lista de rechazo bloquea tácticas legítimas en más del 1 % de los intentos de desarrollo, se afina la lista y se registra el cambio.

---

### Fase 2 — Licencias y datos, antes de cualquier commit

**Cierra:** SEC-2. **Esfuerzo:** S. **Costo:** US$0. **Depende de:** D3.

**Tareas**

1. **`.gitignore`.** Añadir:
   - `problemas resueltos olimpiadas.pdf`;
   - `blobs/`;
   - `lean_project/scratch/` y `lean_project.lock`;
   - `.claude/*.graphify-bak`;
   - `data/eval/runs/`: las corridas se publican ya saneadas en HF (tarea 3) y contienen 354 fragmentos del PDF.

   Preguntar al usuario si `Propuesta_Proyecto_Final_IA_UPTC.pdf`, que es suya, debe versionarse.

2. **Comprobación por contenido.**
   - Tomar los primeros 60 caracteres del `content_md` de cada una de las 153 filas de `informal.jsonl`.
   - Buscarlos en todos los archivos que se añadirían al repo: la lista de `git add --dry-run .` y los archivos de `data/eval/results/` y `data/eval/problems/`.
   - Resultado esperado: **0 coincidencias**.

3. **`scripts/publish_hf.py`.**
   - Cuando algún documento tenga `redistributable=false`, las carpetas de `runs` se suben desde una copia temporal saneada: `snippet` vacío en las citas del corpus informal, y nada del PDF en las trazas.
   - Los originales nunca se modifican.
   - Aplicar la misma comprobación de la tarea 2 sobre la copia saneada.

4. **Documentación.** DECISIONS #70 y una nota en `user_guide.md` ("Publicación").

**Aceptación**
- `git status --porcelain --ignored` muestra esos archivos como ignorados.
- La comprobación por contenido da 0 coincidencias, tanto en el repo como en la copia saneada que produce `publish_hf.py --dry-run`.

**Plan B.** Si el usuario quiere las corridas en git, se versiona una copia saneada generada por la misma función (por ejemplo `data/eval/runs_public/`).

---

### Fase 3 — Dependencias y documentación de estado

**Cierra:** DEP-1, DOC-1…3, DOC-5, DOC-7…10, DOC-12, la parte documental de MET-1 y DATA-1, y SEC-3 (documentación). **Esfuerzo:** M. **Costo:** US$0.

**Tareas**

1. **`pyproject.toml`.**
   - Declarar `httpx` y `tenacity`.
   - Nuevo extra `demo` con `gradio`, con cota inferior a la versión mayor instalada (6.x).
   - Pasar `ranx` a `dev`.
   - Quitar `pandas` y `accelerate` **sólo** si, sin ellos, cargan el embedder y el reranker (bf16 en CPU) y la suite pasa. Si falla algo, se restauran y se anota.
   - `uv lock` y `uv sync --extra dev --extra ingest --extra demo`, siempre sin corridas en marcha.

2. **Documentación inmediata.** Todo esto no depende de cambios de código:
   - `README.md`: `uv sync` con los extras.
   - `install.md`: GPU vía Vulkan (#48), número real de tests, y tabla única con las 9 variables de entorno (`RAGSOLVER_MATHLIB_TABLE`, `RAGSOLVER_EMBED_BACKEND`, `RAGSOLVER_EMBED_URL`, `RAGSOLVER_EMBED_BF16`, `RAGSOLVER_LEAN_PROJECT`, `RAGSOLVER_DB`, `RAGSOLVER_HF_DATASET`, `GRADIO_SERVER_NAME`, `HF_TOKEN`).
   - `PROGRESS.md`: reescritura. Una sección "Estado actual" al principio, sin contradicciones, y el material antiguo movido a "Historial". Se quita la tabla invalidada de la Fase 1, la sección "Sin GPU" y "Qué esperar cuando vuelvas".
   - `interfaces.md`: rango real de `subgoals`.
   - DECISIONS: tachar la #34 indicando que la reemplaza la #40.
   - Docstring de `evaluate.py`.
   - `versions.yaml`: el comentario de `provider_routing`.

3. **Limitaciones en `final_report.md` §8 (y §10 donde aplique).**
   - La tabla de 3.903 filas está sembrada con las premisas gold de miniF2F, lo que favorece al RAG (MET-1).
   - Hueco de cobertura: ninguna declaración `*.eq_*` ni del núcleo de Lean en el corpus (DATA-1).
   - `used_in_sketch` no es fiable en los 34 reportes con IDs repetidos (BUG-1, hasta la Fase 4).
   - RF-3 y RF-6 se aplican por problema, no por subobjetivo.
   - La frase "we release" pasa a indicar que la publicación está preparada pero pendiente.

4. **DECISIONS #71.** Una sola tabla con los desvíos del plan que no estaban registrados (§3.A de la auditoría): recuperación informal sólo en PLAN, sin boost por `math_area`, `_fts_text` en lugar de `name_tokens`, embeddings float32 en lugar de float16, `re_retrieve` implícito, sin tabla `documents`.

5. **`scripts/run_chain.sh`.** El resumen automático se escribe en `data/eval/results/chain_summary.md` en lugar de agregarse a `PROGRESS.md` (cambio de una línea).

6. **`llama-server` en `0.0.0.0` (SEC-3).** En `install.md` y en la cabecera de `embed_full_mathlib.sh`, indicar que se prefiera ligarlo a la IP de WSL, o apagarlo al terminar.

**Aceptación**
- `uv run python -c "import httpx, tenacity, gradio"` funciona en el entorno sincronizado con los extras; la suite pasa.
- Checklist de la auditoría §11 cerrado, cada punto contrastado con el código (con `grep` o `graphify explain`).
- Nada en `README`, `install.md`, `PROGRESS.md`, `architecture.md` ni `interfaces.md` contradice el código.

---

### Fase 4 — Citas, trazas y contabilidad

**Cierra:** BUG-1, TRZ-1, LLM-1, ACC-1, BUG-5, BUG-6 y el pin de tabla de MET-1. **Esfuerzo:** M. **Costo:** US$0, más un smoke opcional (D6).

**Tareas**

1. **IDs de cita estables.**
   - El estado del grafo guarda un mapa `chunk_id → citation_id`.
   - Los nodos `plan` y `retrieve` sólo asignan número a los chunks nuevos (el siguiente libre de cada prefijo, `M` o `I`) y reutilizan los existentes. Los prompts muestran esos IDs.
   - Test de regresión: un replan y una re-recuperación que devuelven chunks repetidos y nuevos no producen IDs duplicados, y `used_in_sketch` marca exactamente el chunk citado.
   - Los datos de `eval-v1` no se recalculan: no es reconstruible porque las trazas del retriever no se pueden atribuir a una configuración. Queda como limitación (Fase 3.3).

2. **Trazas del retriever por corrida.**
   - `RetrievalContext` gana `run_id`. El orquestador le pasa el identificador del par.
   - El `Retriever` escribe en el archivo de ese par cuando `run_id` está presente; si no, se mantiene el comportamiento actual (CLI, demo, evaluación).
   - El tratamiento de trazas interrumpidas del runner pasa a cubrir también las búsquedas.
   - Para las corridas nuevas, cambiar `:` por `__` en los nombres de archivo de traza, porque `/mnt/c` es NTFS. Registrarlo en DECISIONS.
   - Test: tras una corrida falsa, hay un archivo por par con los eventos de agentes y de búsquedas, y ningún archivo `<problema>:<uuid>`.

3. **Pin de tabla.** `VersionsPin` gana `mathlib_table`, con la tabla que realmente sirve el corpus `mathlib`. Regenerar esquemas.

4. **Política de reintentos** (`llm.py`).
   - Número de intentos configurable, por defecto 4.
   - Se reintenta ante error de conexión, timeout de conexión, 429 y 5xx. **No** se reintenta ante un timeout de lectura, porque el proveedor puede haber cobrado.
   - `timeout_s` de la sección `llm` baja a 300 s: 4.000 tokens de salida al proveedor más lento medido (19 tok/s, #62) son unos 210 s.
   - Cada intento fallido se registra con su código, y la respuesta lleva el número de intentos (`attempts`), que también va a la traza.
   - Tests con `httpx.MockTransport`, que viene con `httpx` (sin dependencias nuevas): 503, 503, 200 da 3 intentos registrados; un timeout de lectura no se reintenta.

5. **Contabilidad sin pérdidas.**
   - `_guard` captura cualquier excepción, salvo `DailyQuotaExhausted`, y la convierte en `status: error` dentro del grafo, conservando lo acumulado. Hoy `solve()` descarta el estado (`graph.py:222`).
   - El evento `invalid_json` registra `llm_calls`, tokens y `cost_usd`.
   - Las filas de error de las líneas base llevan el modelo.
   - Test: una excepción a mitad de corrida conserva los contadores (análogo al test existente de JSON inválido).

6. **Self-consistency.** Sumar `cost_usd` y el número real de llamadas de cada muestra (BUG-5).

7. **Caché de consultas.** Las escrituras y el desalojo de `_QUERY_CACHE` van bajo un lock (BUG-6).

8. **Smoke opcional (D6).** Los 5 problemas triviales con `full`, código nuevo, LLM real y REPL. Costo ≈ US$0,001.
   - Esperado: 5/5 resueltos; `axioms` informado; una traza por par con las búsquedas; `mathlib_table` en el pin.

9. **Documentación.** `architecture.md` (trazas), `reproduce.md` y `user_guide.md` (estructura de `traces/`), DECISIONS.

**Aceptación**
- Tests nuevos en verde.
- En una corrida falsa de 3 pares con replan: 0 IDs duplicados, 1 archivo de traza por par, y los contadores de llamadas, tokens y costo del reporte coinciden con la suma de los eventos de la traza.

---

### Fase 5 — Ingesta, CLI y configuración

**Cierra:** ING-1, BUG-3, BUG-4, ARQ-2 y la etiqueta de cita de `PLAN §7.5`. **Esfuerzo:** M (L si D4 = multidocumento). **Costo:** US$0, porque la caché de enriquecimiento evita llamadas.

**Tareas**

1. **Rutas ancladas a la raíz.** Una función que resuelve la raíz del repo a partir de `__file__` (válido con la instalación editable que hace `uv`). La usan todas las lecturas de `configs/`, `.env` y los valores por defecto de `data/`: `llm.py`, `agents/agents.py`, `rag/retrieve.py`, `rag/enrich.py`, `rag/ingest_*.py`, `rag/embed_corpus.py`, `orchestrator/run.py`, `logging/jsonl_writer.py`, `demo/app.py`.
   - Si algún día se distribuye como wheel no editable, añadir una variable de entorno para la raíz; hoy no hace falta.
   - Test: con otro directorio de trabajo, se cargan la configuración de `LLM` (con una clave falsa en el entorno) y la del `Retriever`.

2. **Versiones desde `versions.yaml`.** `index_version` y la versión del parser dejan de estar escritas en `ingest_informal.py` (4 sitios). `publish_space.py` cuenta las filas de la tabla, igual que `publish_hf.py`. El título de la demo toma la tag de `versions.yaml`.

3. **CLI.** `ragsolver ingest` delega en el `argparse` de `ingest_informal`, a través de un `main(argv)`. Test: las flags se interpretan igual en cualquier orden (`--skip-llm --pdf X` y `--pdf X --skip-llm`).

4. **Ingesta correcta (D4 = guard).**
   - Si `data/corpus/informal.jsonl` existe con otro `doc_id`, la ingesta aborta con un mensaje claro, salvo que se pase `--replace`.
   - Los metadatos del documento se construyen con el modelo `Document`, que así deja de estar muerto y además valida. Salen de `pymupdf` (`metadata`: title, author, creationDate) y los sobrescriben las flags `--title`, `--authors`, `--year`, `--license` y `--redistributable`.
   - `reproduce.md §2` pasa las flags del PDF de ejemplo, de modo que su `document.json` quede **idéntico** al actual.
   - `section_path` toma el título de sección que realmente detectó `parse_structure`, no el literal "Problemario".

5. **Etiqueta de cita** (`PLAN §7.5`).
   - Cada `InformalChunk` gana `source_label` (por ejemplo "Moreno Núñez & Tellechea Armenta (2002)"), escrito en la ingesta.
   - `_informal_citation_label` lo usa si existe; si no, el `doc_id`.
   - Hay que reingerir (0 llamadas por la caché), reembeber (≈ 215 s en CPU) y reconstruir las tablas `informal` y `figures`.

6. **Multidocumento, sólo si D4 = multi.** Archivos por documento (`data/corpus/informal/<doc_id>.jsonl` y sus figuras), `ragsolver ingest <dir>` que recorre los PDFs, y el CLI de `index` concatenando el directorio.

7. **Tests.** Guard de otro `doc_id`, metadatos desde flags, parseo del CLI, rutas desde otro directorio. La prueba de ingesta de extremo a extremo va en la Fase 6.

8. **Documentación.** `user_guide.md §4` (qué soporta y qué no), `reproduce.md §2`, DECISIONS.

**Aceptación**
- Reingerir el PDF de ejemplo da 153 chunks con los **mismos `content_sha`**, `document.json` sin cambios y 0 llamadas LLM.
- `ragsolver search … --corpus informal --mode exemplar` muestra la etiqueta con autor y año.
- `ragsolver ingest` desde otro directorio funciona.
- Ingerir un segundo PDF sin `--replace` aborta sin tocar el corpus.

**Plan B.** Si `pymupdf` no trae metadatos útiles para un PDF, las flags son obligatorias para ese documento (error explícito si faltan).

---

### Fase 6 — Tests de las zonas sin cobertura y CI

**Cierra:** TEST-2 (resto), TEST-3, TEST-4 y CI-1. **Esfuerzo:** M. **Costo:** US$0. **Depende de:** Fases 1, 4 y 5; D2 para el CI.

**Tareas**

1. **Fixture sintético.** Sin contenido con licencia:
   - un PDF de 2 páginas generado dentro del test con `pymupdf`, con una sección "Problemario" (2 problemas), otra "Soluciones" (2 soluciones) y un dibujo vectorial;
   - un JSON de bloques estilo MinerU mínimo en `tests/fixtures/`.

   Test: `ingest_informal.run(skip_llm=True)` de extremo a extremo, con directorios temporales. Esperado: 2 cards emparejadas, 1 figura recortada, archivos de corpus escritos.

2. **Tests con modelos falsos** (reutilizando el embedder falso y la LanceDB temporal de `test_retriever.py`):
   - `_cli` de búsqueda;
   - `evaluate_variants` sobre una tabla temporal;
   - `embed_corpus.run` con `embed_documents` sustituido.

3. **Integración con Lean real** (marcador `slow`; no se ejecuta en CI). Los 5 problemas triviales pasan por el `Orchestrator` con un LLM falso que devuelve tácticas correctas conocidas y el `LeanChecker` real. Esperado: 5/5 `done` con axiomas estándar. Tarda unos 5 minutos, casi todos del import.

4. **CI** (tras D2).
   - Se ejecuta en cada push con `-m "not slow"` y deseleccionando la integración con el índice.
   - Caché de `uv` en `setup-uv`.
   - Medir la duración. **Sólo si** supera 15 minutos, usar en CI el índice de ruedas de torch para CPU.

5. **Documentación.** En `install.md`, qué tests se saltan sin datos locales (15, según la auditoría) y cómo ejecutar los `slow`.

**Aceptación**
- Cobertura: `ingest_informal.py` ≥ 50 %, `cli.py` ≥ 80 %, `evaluate.py` y `embed_corpus.py` > 0 %, `lean/verify.py` ≥ 70 %, total ≥ 78 %.
- El CI termina en verde una vez en GitHub.

---

### Fase 7 — Diseño y métricas

**Cierra:** DES-1, MET-2, la taxonomía de errores del retriever (`PLAN F5 t5`), la medición de DATA-1, la tabla por defecto de MET-1, la documentación de DES-2 y el reranker para los agentes (restricción de capacidad del §8 de la auditoría). **Esfuerzo:** S–M. **Costo:** US$0, salvo la medición opcional.

> **Orden:** si se aprueba `eval-v1-full` (D6), debe ejecutarse **antes** de la tarea 7.1, con `prompt_version: "1"`, para no mezclar el cambio de prompt con el cambio de índice (ver Fase 9.4).

**Tareas**

1. **Replan con feedback (D5).**
   - El prompt del Planificador recibe `{feedback}`: la razón del Crítico y la estrategia anterior.
   - El grafo deja de vaciar `feedback` antes de replanificar; lo vacía después.
   - `prompt_version: "2"`.
   - Test con el LLM falso: el segundo prompt del planner contiene el feedback.
   - `eval-v1` no se repite; sus trazas registran `prompt_version: "1"`.
   - Medición opcional (con aprobación de gasto) sobre el split dev, **sin** afirmar una mejora: con n = 24, la variación entre corridas es del tamaño del efecto (informe §7).

2. **Métrica de re-recuperación (MET-2).** Se reemplaza `re_retrieval_success` por `re_retrieval_used`: la fracción de re-recuperaciones en modo `error` tras las cuales el siguiente intento usa (según `identifiers_used`) al menos una declaración que devolvió esa búsqueda, o se resuelve. Actualizar `eval_report.py` y `fill_final_report.py`. `eval-v1` sólo tiene 1 re-recuperación: se recalcula desde los datos si es trivial y, si no, se anota.

3. **Taxonomía de errores del retriever.** Se calcula desde los datos guardados, sin traza nueva y a US$0:
   - "Lema necesario no recuperado": premisas gold (`premises_gold.jsonl`) que no están entre las citas consultadas del par (`results.jsonl`).
   - "Nombre alucinado": identificadores de `unknown identifier` en los errores del verificador que no existen en el corpus.
   - "Ejemplar irrelevante": revisión manual de 20 listas de ejemplares, o se omite con una nota si no hay revisor.
   - "Figura mal descrita": no aplica a miniF2F.
   - Nueva sección en `eval_report.md`.

4. **Disparador de DATA-1.** A partir de la tarea 3, contar los identificadores desconocidos, y los `missing_identifiers` del Crítico, cuyo último componente empieza por `eq_` o que existen en Lean (se comprueba con `#check` en el REPL) pero no en el corpus. Si superan el **5 %** de los fallos por lema faltante, se activa la tarea de corpus de la Fase 10.

5. **Tabla por defecto (MET-1).** Si la tabla `mathlib_full` existe y `RAGSOLVER_MATHLIB_TABLE` no está definida, el corpus `mathlib` se sirve desde `mathlib_full` (el pin de la Fase 4.3 lo registra). `run_chain.sh` y `reproduce.md` fijan `RAGSOLVER_MATHLIB_TABLE=mathlib` de forma explícita para reproducir `eval-v1`.

6. **DES-2.** Documentar en `architecture.md` y en `final_report.md` que la crítica del último intento sólo alimenta la taxonomía (40 llamadas, 3,6 %). Sin cambio de código.

7. **Reranker para los agentes por GPU (revisado 2026-09-23 — ya no depende de más RAM).**
   - **Hallazgo:** el reranker no necesita más RAM del sistema, necesita correr en la GPU, igual que ya se hizo con el embedder (DECISIONS #48). `tools/llama-vulkan/llama-server.exe` es un binario genérico de llama.cpp: sirve cualquier GGUF, no sólo el embedder. llama.cpp soporta Qwen3-Reranker de forma nativa por el endpoint `/v1/rerank` (`--reranking --pooling rank --embedding`), con conversión GGUF oficial ya publicada y verificada (`ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF`). La RX 9060 XT (16 GB VRAM) tiene sitio de sobra junto al embedder (~1,8 GB); el tráfico va por Vulkan, sin tocar los 7,3 GB de RAM de WSL. Verificado en esta máquina: `/dev/dxg` presente (passthrough de GPU activo), `tools/llama-vulkan/llama-server.exe` ya instalado.
   - **Tareas del lado del usuario** (Windows, fuera de este agente): descargar `Qwen3-Reranker-0.6B-Q8_0.gguf` (o el f16 si se prefiere) a `tools/llama-vulkan/models/`; arrancar un segundo `llama-server.exe` en un puerto distinto al del embedder (por ejemplo 8082) con `--reranking --pooling rank --embedding -ngl 99 --host 0.0.0.0 --port 8082`.
   - **Tareas de código** (este agente, en la Fase 7):
     - `rag/retrieve.py::Retriever._reranker()` gana un camino `RAGSOLVER_RERANK_BACKEND=llama` que llama a `POST {RAGSOLVER_RERANK_URL}/v1/rerank` con `{query, documents}` en vez de `CrossEncoder.predict()`, replicando el patrón de `embed.py::_llama_embed` (mismo `ThreadPoolExecutor`, mismo manejo de reintentos, `RAGSOLVER_RERANK_URL` con el mismo fallback de gateway que `_llama_url()`).
     - `configs/agents.yaml: retrieval.rerank: true`.
     - Nuevo script `scripts/rerank_llama.sh` (mismo formato que `embed_full_mathlib.sh`) con el comando exacto del servidor, para no depender de memoria.
     - Test con un servidor HTTP falso (`httpx.MockTransport`, sin dependencias nuevas) que verifica el payload y el parseo de la respuesta.
   - **Medición** (con el servidor arriba, 0 costo de LLM): repetir la Fase 5 de `PLAN_RAG.md` (evaluación sobre MathlibQR, variante `hibrido_rerank`) sobre el índice completo y comparar con los 0,45 de recall@10 ya medidos sin reranker de agentes (#56). Si la ganancia es ≥ 3 puntos, se documenta como confirmada y `retrieval.rerank: true` queda como valor por defecto también en un smoke de los 5 triviales.

**Aceptación**
- Tests del prompt y de la métrica en verde.
- Sección de taxonomía publicada en `eval_report.md`, con el cálculo reproducible con un comando.
- Resultado del disparador de DATA-1 registrado en DECISIONS.
- Con el servidor de reranking arriba: `RAGSOLVER_RERANK_BACKEND=llama ragsolver search "gcd is commutative" --corpus mathlib` devuelve resultados reordenados y el campo `scores.rerank` poblado, sin cargar el `CrossEncoder` de torch (verificable por RSS o por log).

**Plan B (tarea 7).** Si el usuario no tiene la máquina con la GPU disponible en el momento de esta fase, la tarea de código se implementa y se prueba con el servidor falso igual, y la medición y el `rerank: true` por defecto quedan pendientes de que el servidor esté arriba — no bloquean el resto de la fase.

---

### Fase 8 — Limpieza (Ponytail)

**Cierra:** la auditoría §9, ARQ-3, ARQ-5 y el código muerto de §3.D. **Esfuerzo:** S–M. **Costo:** US$0. **Depende de:** Fase 6 (red de tests) y D3.

**Tareas**

1. **Borrar código sin uso:** `render_pages`, `read_events` (sólo si ningún script lo necesita), `LLMResponse.json`, `Enricher.models` y `Enricher.model`, `QUERY_INSTRUCTION`, `VectorFigure.n_paths` y los tests que sólo prueban eso.

2. **Borrar scripts reemplazados:** `scripts/run_night.sh` y `scripts/run_eval_v1.sh` (los reemplaza `run_chain.sh`), y actualizar sus menciones en la documentación.

3. **Quitar el backend `lake`** (`verify()`, `run_lean()`, `parse_messages()`). El sandbox de la Fase 10 envuelve el REPL mediante `lake_path` y no necesita el camino por proceso. La función de fuente única de la Fase 1 sustituye a `build_file`.

4. **Deduplicar:**
   - despacho de verificación: `graph.verify` y `run_baseline` llaman a una sola función de chequeo;
   - un helper para construir `VersionsPin`.

5. **Estado del `Retriever` (ARQ-3):** `channels` pasa a ser parámetro del constructor. `evaluate.py` construye un `Retriever` por variante; el embedder ya está cacheado a nivel de módulo, así que es barato. La caché de tablas deja de ser `lru_cache` sobre un método y pasa a un dict por instancia.

6. **Artefactos (según D3):** `lean_project/scratch/`, `lean_project.lock` y `.claude/settings.json.graphify-bak`; `blobs/` sólo si el usuario lo confirma.

7. **`graphify update .`** y comprobar con `graphify explain` que no quedan nodos huérfanos de lo borrado.

**Aceptación**
- La suite pasa.
- Balance de líneas registrado en DECISIONS; la auditoría estimaba unas −150.
- Ningún `import` roto: `python -m compileall src scripts demo`.

---

### Fase 9 — Entregables (decisiones del usuario)

**Esfuerzo:** S. **Costo:** ≈ US$0,30 si se ejecuta `eval-v1-full`. **Depende de:** D1, D2, D6 y D7. **Obligatorio antes:** Fases 1 y 2.

**Tareas**

1. **Licencia (D1).** Archivo `LICENSE` y `CITATION.cff` con la URL real del repositorio.

2. **Commit (D2).**
   - Revisar la lista de archivos antes; la comprobación de la Fase 2.2 debe dar 0.
   - El commit y el push los ordena el usuario. El push dispara el CI (Fase 6.4).

3. **Hugging Face (D7).**
   - `publish_hf.py --dry-run` y revisión de la copia saneada; luego publicar el dataset y el Space.
   - Comprobar que el Space arranca y responde a una búsqueda, y que "Resolver" muestra el aviso de uso sólo local.

4. **`eval-v1-full` (D6).**
   - Se ejecuta con `run_chain.sh` después de las Fases 1 y 4 y **antes** de la Fase 7.1, con `prompt_version: "1"` y la misma configuración de agentes que `eval-v1`.
   - Los cambios de las Fases 1 y 4 no alteran los resultados, salvo lo que muestre la re-verificación (1.8): así, la única diferencia con `eval-v1` es el índice. Hay que declararlo así en el informe.

5. **Cierre.** Actualizar `final_report.md` (enlaces publicados, §6.3 con `eval-v1-full` si se ejecutó) y `PROGRESS.md`.

**Aceptación**
- Repo y dataset públicos sin texto del PDF (comprobación por contenido = 0).
- CI en verde.
- Informe con los enlaces reales.

---

### Fase 10 — Trabajo con disparador (sólo si se cumple la condición)

| Extensión | Disparador | Qué implica | Aceptación |
|---|---|---|---|
| Sandbox de sistema operativo para Lean (SEC-1 residual, SEC-3) | Antes de exponer la demo fuera de localhost, de procesar problemas o corpus de terceros, o de cambiar a un proveedor LLM no confiable | Script envoltorio pasado como `lake_path` de `lean-interact`: `bwrap` sin red (`--unshare-net`), bind de sólo lectura del proyecto Lean y del toolchain, `tmpfs` para lo temporal, sin acceso a `$HOME` ni al repo | Un `#eval IO.FS.readFile` sobre el `.env`, forzado con la validación desactivada en un test aislado, falla por permisos; el import de Mathlib tarda lo mismo (±10 %) |
| Replay de las pruebas publicadas (nivel 3) | Antes de presentar las pruebas como definitivas en HF o en el informe final | SafeVerify sobre las pruebas resueltas. `comparator` queda descartado mientras Landlock no esté activo | Todas pasan, o las discrepancias quedan documentadas |
| RF-1, RF-3 y RF-6 por subobjetivo | Si el curso exige el cumplimiento literal | Subobjetivos como lemas `have` o `lemma` verificados por separado, con reintentos por subobjetivo | Diseño propio (L) antes de implementar |
| Gold de OCR de 10 páginas | Cuando haya un anotador humano | Transcripción manual; métricas de `PLAN F2 t10` | Fórmulas ≥ 95 %, texto ≤ 3 % de distancia de edición |
| Completar el corpus (DATA-1) | Si el disparador de la Fase 7.4 supera el 5 % | Extraer las declaraciones que faltan (`eq_*` y núcleo de Lean) con el pipeline de LeanSearch v2 sin informalización (`PLAN F1`, plan B) y reindexar | El recall de premisas gold sube y se documenta |
| Multidocumento (si D4 = guard) | Un segundo documento real para ingerir | Fase 5.6 | Dos documentos conviven con citas correctas |
| Contratos transversales (ARQ-1) | El próximo cambio que toque contratos de dos paquetes | Mover `TraceEvent`, `VersionsPin`, `Citation` y `Verdict` a un único módulo de contratos | Sin dependencias `lean → agents` ni `logging ↔ rag` (verificado con Graphify) |
| Optimizaciones (auditoría §8) | Corridas en las que la recuperación supere el 10 % del tiempo de pared | Embeber las consultas de todos los subobjetivos en un lote; figuras en una sola consulta; embeddings en Arrow en lugar de JSONL | Latencia medida antes y después |
| PutnamBench | Trabajo futuro (#67) | Portar los enunciados al pin `v4.28.0-rc1` | — |

---

## 5. Esfuerzo y costo estimados

| Fase | Esfuerzo | Costo | Bloquea a |
|---|---|---|---|
| 1 Verificador | M (≈ 1 día) | US$0 | 3 (parcial), 4, 6, 9 |
| 2 Licencias | S | US$0 | 9 |
| 3 Dependencias y documentación | M | US$0 | 6 |
| 4 Citas, trazas y contabilidad | M | US$0 (+ US$0,001 opcional) | 6, 7, 9.4 |
| 5 Ingesta, CLI y configuración | M (L si hay multidocumento) | US$0 | 6 |
| 6 Tests y CI | M | US$0 | 8 |
| 7 Diseño y métricas | S–M | US$0 (+ medición opcional) | — |
| 8 Limpieza | S–M | US$0 | — |
| 9 Entregables | S | ≈ US$0,30 si hay `eval-v1-full` | — |

Total estimado del agente: **6 a 8 días de trabajo**, sin contar la Fase 10.

---

## 6. Definición de terminado (global)

1. Los 4 HIGH (SEC-1, VER-1, SEC-2, ING-1) están cerrados, con tests.
2. Cada MEDIUM está cerrado, o aplazado con su motivo registrado en DECISIONS.
3. `eval-v1` re-verificado con el verificador nuevo y el resultado documentado.
4. La documentación de estado es consistente con el código (checklist de la auditoría §11 cerrado).
5. La suite pasa (incluidos los tests nuevos); el test `slow` se ejecutó al menos una vez; el CI está en verde.
6. `graphify update .` ejecutado tras la última fase de código.

---

## 7. Orden de ejecución

1. **Fase 1** y **Fase 2** (independientes entre sí; ambas antes de cualquier commit).
2. **Fase 3** (depende de la Fase 1 sólo para la sección de amenazas; el resto puede ir en paralelo).
3. **Fase 4**. Si se aprueba, aquí va el smoke de D6.
4. **Fase 9.1–9.2**: licencia y commit, si el usuario lo decide. Habilita el CI.
5. **Fase 5**, luego **Fase 6**.
6. **Fase 9.4** (`eval-v1-full`, si se aprueba), **antes** de la Fase 7.1.
7. **Fase 7**, **Fase 8**, **Fase 9.3** (publicación en HF).
8. **Fase 10**, según los disparadores.

---

## 8. Referencias consultadas

- Lean FRO, *Validating a Lean Proof* (manual de referencia): https://lean-lang.org/doc/reference/latest/ValidatingProofs/
- leanprover/comparator (juez en sandbox para pruebas generadas por IA): https://github.com/leanprover/comparator
- GasStationManager/SafeVerify (replay más lista blanca de axiomas; leaderboard de PutnamBench): https://github.com/GasStationManager/SafeVerify · fork de Mistral: https://github.com/mistralai/LeanstralSafeVerify
- Kimina-Prover-Preview, issue #11 (15/225 soluciones de miniF2F dependían de `Lean.ofReduceBool`): https://github.com/MoonshotAI/Kimina-Prover-Preview/issues/11
- Kimina Lean Server: https://arxiv.org/abs/2504.21230
- OpenAI Codex, `linux-sandbox` (Landlock y bubblewrap): https://github.com/openai/codex/blob/main/codex-rs/linux-sandbox/README.md
- Sandbox en WSL (bubblewrap y namespaces de usuario): https://github.com/RisorseArtificiali/lince/issues/356
- Comprobaciones locales (2026-09-23): `/usr/bin/bwrap` presente; `max_user_namespaces = 29869`; Landlock ausente de `/sys/kernel/security/lsm`; `lean-interact` 0.11.5 admite `LeanREPLConfig(lake_path=…)`.
- llama.cpp, soporte de Qwen3-Reranker vía `/v1/rerank`: https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF · https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/discussions/22 · issue de calidad del endpoint (usar la conversión oficial): https://github.com/ggml-org/llama.cpp/issues/16407
- Comprobación local (2026-09-23): `/dev/dxg` presente (passthrough de GPU activo en WSL2); `tools/llama-vulkan/llama-server.exe` ya instalado (mismo binario usado para el embedder, #48).
