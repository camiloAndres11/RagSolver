# Plan de mejora del RAG — recuperar para el *goal* de Lean, no para el enunciado

> Documento de ejecución para el agente implementador. Fecha: 2026-10-14. Idioma de trabajo: español; código, prompts y comentarios siguen en el idioma de cada archivo.
> Antecedentes: [`PLAN_RAG.md`](PLAN_RAG.md) (diseño original), [`eval_report.md`](eval_report.md) y [`final_report.md`](final_report.md) §6-§10 (resultado nulo del RAG), [`PLAN_CORRECCIONES.md`](PLAN_CORRECCIONES.md) (sus Fases 1-4 ya cerradas; su Fase 7.7, reranker por GPU, queda absorbida aquí como canal C).
> Todas las cifras de la sección 2 se midieron el 2026-10-14 con 0 llamadas LLM y US$0. Los datos y el script de la sonda están en `data/eval/results/probe_close/`:
> - `probe_close.py`, `probe.jsonl`;
> - `names.json`: top-16 del RAG actual por problema;
> - `lwb_prior.json`: prior de Lean Workbook.
>
> Con esos archivos se recalculan exactamente las filas NL / formal / prior de la tabla 2.2. Las filas "selector" y "mezcla" salieron de un script exploratorio que no se conservó (reinicio de WSL); se reconstruyen en la Fase 0, tarea 1.

---

## Estado de implementación (2026-10-14)

Implementado y probado con 0 llamadas LLM y US$0: Fases 0, 1, 2, 2b, 3 y 4. La Fase 5 no se dispara. **La Fase 6 está pendiente de R4** (gasto). Decisiones: `DECISIONS.md` #89-#100.

| Fase | Estado | Resultado medido |
|---|---|---|
| 0 | ✅ | Gold por goal contra Lean (`premises_gold_goals.jsonl`: 1 295 goals de 230 problemas; premisas = constantes que la táctica nombra y el término usa, #99). Scripts: `eval_premises_goals.py`, `probe_close.py` |
| 1 | ✅ | `data/corpus/premise_prior.jsonl`: 778 lemas validados contra Lean, incluido el núcleo (`Nat.mul_mod`, `eq_comm`). Recall@16 por goal **0.327** con el filtro por símbolos (0.261 sin filtro) |
| 2 | ✅ | `LeanChecker.goals`/`step` (modo sonda, `ProofStep`, estados con generación del REPL), `fill_sorries`, registro `proved`. Tests rápidos y con Lean real en verde |
| 2b | ✅ | Replan con feedback del Crítico + estrategia anterior + goals probados; `prompt_version: "2"` |
| 3 | ✅ con desvío | Canal C (denso con el goal) **apagado**: recall@16 0.028, y fusionado baja el total a 0.097 (#98). El canal B se consume en CLOSE (#92). La tarea 4b queda sin objeto mientras C esté apagado |
| 4 | ✅ | Batería sobre el goal raíz de `eval_v1`: sin premisas 11/60, **con premisas 12/60**. `grind` cierra 6 → 8, y aparece un problema nuevo con `nlinarith [sq_nonneg …]` instanciado del prior. 4 problemas que no resolvió ninguna configuración de `eval_v1`; con `full` llega a 15/60 |
| 5 | no se dispara | Recall@16 del canal A ≥ 0.30 por goal |
| 6 | ronda 1/4 ✅, detenida por el guardia | Ronda 1: `full` 6/15, `no_rag` 7/15, `baseline_auto` 5/15 (eval-v1: 3 y 4). `baseline_auto` en los 60: 11/60 sin LLM. El RAG por goal no muestra efecto medible todavía (1 premisa usada por el kernel). Proyección para las rondas 2-4 > saldo: decisión del usuario en `docs/eval_report_v2.md` §6 (#108) |

---

## 0. Resumen ejecutivo

**Objetivo del sistema** (`PLAN_RAG.md` §1.1): resolver problemas de olimpiada con pruebas que acepta el kernel de Lean 4 + Mathlib. **El trabajo del RAG** (RF-2, contrato 6.3) es entregar, **para cada subobjetivo**, los lemas de Mathlib que hacen falta para cerrarlo, con su firma y su cita. **Que el RAG dé resultados** significa tres cosas medibles:

1. Los lemas recuperados están entre los que usan las pruebas reales (recall de premisas).
2. Esos lemas **aparecen en pruebas que acepta el kernel** (`used_in_proof` > 0).
3. `full` supera a `no_rag` en pass@1 (métrica 8.3 de la propuesta).

Hoy no se cumple ninguna: recall de premisas ≈ 1 % sobre el índice completo, `used_in_proof` ≈ 0 y `full` = `no_rag` = 9/60.

**Diagnóstico.** El RAG y el bucle de reintentos fallan en cinco puntos a la vez. Arreglar sólo uno no mueve el resultado:

| # | Falla | Evidencia (sección 2) |
|---|---|---|
| D1 | **Consulta equivocada.** Se busca con el enunciado o un subobjetivo en lenguaje natural. Las pruebas de competición usan una "caja de herramientas" de lemas genéricos (`sq_nonneg`, `mul_pos`, `Finset.sum_range_succ`…) cuyo enunciado no se parece al problema. | Recall@8 de premisas gold: búsqueda densa NL 0.012, densa con el enunciado formal 0.000, **prior de competición 0.201-0.216** |
| D2 | **Unidad equivocada.** Se busca una vez por problema o por subobjetivo informal, antes de existir un *goal* de Lean. El estado del arte consulta con el **estado de prueba** en el punto donde se necesita el lema. | LeanHammer: recall@16 63.5 con estado→premisa vs. 35.1 de ReProver. Lean 4.28 ya trae selección de premisas nativa sobre el goal |
| D3 | **Consumo equivocado.** Los lemas se pegan en el prompt y el LLM escribe la prueba entera a ciegas. Aun cuando el prompt traía premisas gold, no se usaron. | En `eval_v1` el 32 % de las premisas gold estuvo en el prompt (86/266), pero `used_in_proof` ≈ 0. Los errores dominantes son de tácticas a ciegas (`unsolved goals`, `rewrite failed`, `linarith failed`), no de nombres desconocidos (~6 %) |

| D4 | **Corpus incompleto.** El JSONL de LeanSearch v2 sólo trae módulos de Mathlib. Falta todo el núcleo de Lean (`Nat.mul_mod`, `Eq.symm`, `Or.inl`, `eq_comm`…) | El 28 % de los usos de premisas en pruebas verificadas de miniF2F son lemas que existen en Lean y no en el corpus. Sólo el 4 % de ellos son `eq_*` (sección 2.4) |
| D5 | **Se tira el trabajo verificado.** La prueba es un único bloque, y tanto un reintento como un replan empiezan de cero; el replan además sin el feedback del Crítico (DES-1) | 55 de 99 intentos fallidos con ≥ 2 `have` tenían al menos un `have` completo antes del primer error. 65 replans, 2 éxitos (sección 2.5) |

**La prueba de fuego** (sección 2.3, 0 LLM): pasar a `grind` las premisas que recupera hoy el RAG **baja** de 7 a 6 los problemas cerrados. Pasarle el prior de competición o la selección nativa de Lean los **sube** a 9. `try?` solo cierra 12/60, más que el pipeline completo con LLM (9/60), y la unión de ambos caminos llega a 16/60.

**Decisión.** Se pasa de "recuperar para el enunciado y pegar en el prompt" a **"recuperar para cada goal de Lean y consumir con Lean"**:

- **Input del retrieval:** el *goal* pretty-printed que devuelve el REPL en cada `sorry` de un esqueleto `have … := by sorry`. El lenguaje natural queda sólo para los ejemplares del corpus informal, donde NL↔NL sí funciona (recall@5 = 1.0).
- **Tres canales fusionados con RRF, 0 llamadas LLM:**
  - (A) **prior de competición**, la frecuencia de cada lema en 29 750 pruebas de Lean Workbook (licencia MIT, sin miniF2F);
  - (B) **selección nativa de Lean** (`suggestions`, Sine Qua Non) sobre el goal;
  - (C) el **índice denso actual** consultado con el goal, con reranker por GPU, sólo para lemas específicos (`Real.log_mul`).
- **Consumo:** primero una batería de tácticas automáticas que recibe las premisas recuperadas (`grind +lax [...]`, `nlinarith [...]`, `simp only [...]`), por goal y con 0 llamadas LLM. Sólo los goals que queden abiertos van al LLM, con el goal exacto y las premisas con firma. Así `used_in_proof` pasa a ser mecánico y real.

**Qué se descarta:**
- Reescribir mejor las consultas NL (HyDE, más `search_queries`): el techo medido es ~1 %.
- Consultar el índice actual con el enunciado formal: 0.0-0.1 %.
- Entrenar un selector propio: fuera del alcance §5.3 de la propuesta.
- El servidor remoto de LeanHammer: dependencia externa y versión de Mathlib distinta.

Justificación en la sección 5.

---

## 1. Decisiones que necesita el usuario

| # | Decisión | Se necesita en | Opción recomendada |
|---|---|---|---|
| R1 | Cambiar el contrato del Autoformalizador: esqueleto con `sorry` + relleno por goal, en vez de la prueba entera de una vez | Fase 2 | Sí. Es el cambio que habilita todo lo demás; el presupuesto RNF-3 no sube (sección 4.3) |
| R2 | Usar datos de Lean Workbook (Goedel-LM/Lean-workbook-proofs, MIT) para el prior | Fase 1 | Sí. Ampliar a Goedel-Pset-v1-solved sólo si su licencia lo permite (no figura en su ficha) |
| R3 | Redefinir la ablación `no_rag` como "misma automatización, sin premisas recuperadas" (no "sin automatización") | Fase 6 | Sí. Si no, la comparación mezcla el efecto del RAG con el de la batería de tácticas |
| R4 | Gasto de la re-evaluación: `full` + `no_rag` + `rag_nl` (la estrategia actual) sobre `mathlib_full` | Fase 6 | ≈ US$0.15 por configuración × 3 ≈ US$0.45. Requiere subir el tope de la key (quedan ≈ US$0.42) |
| R5 | Servir el reranker en la GPU (Windows, segundo `llama-server`) | Fase 3, canal C | Sí. Mismo procedimiento ya escrito en `PLAN_CORRECCIONES.md` 7.7 |
| R6 | Replan (es la D5 de `PLAN_CORRECCIONES.md`) | Fase 2b | Replan con feedback **y** con los subobjetivos ya probados (0 llamadas extra); alternativa: `max_replans: 0` |

---

## 2. Evidencia (2026-10-14, 0 llamadas LLM)

### 2.1 Qué rompe las pruebas hoy (`eval_v1`, primer error de Lean por intento rechazado)

| Primer error | Intentos |
|---|---|
| `unsolved goals` | 44 |
| `rewrite failed: did not find an occurrence` | 38 |
| `omega` / `linarith` no cierran | 27 |
| `type mismatch` (varias formas) | ~25 |
| `sorry` / `native_decide` rechazados por el validador | 19 |
| `No goals to be solved` | 15 |
| `introN` sin binders | 10 |
| sintaxis | ~11 |
| **identificador o constante desconocida** | **~15 (≈ 6 %)** |

La mayoría son fallos de **escribir tácticas sin ver el goal**. El modo `error` del retriever, que fija el nombre exacto, sólo puede ayudar en el ~6 % de los casos.

### 2.2 Recall de premisas gold según el input del retrieval

Gold: 352 problemas de miniF2F con premisas extraídas de pruebas verificadas de DeepSeek-Prover-V2 (`data/eval/premises_gold.jsonl`). Índice: `mathlib_full` (278 160 declaraciones, ANN), sin rerank, k = 8 / 16.

| Input / método | recall@8 | recall@16 | Nota |
|---|---|---|---|
| Enunciado informal (NL), denso + BM25, como hoy | 0.012 | 0.013 | 42 problemas de `eval_v1` con gold |
| Enunciado formal (Lean), mismo índice | 0.000 | 0.001 | El embedder genérico no sirve para consultas formales largas |
| **Prior de competición estático** (top-k por frecuencia en Lean Workbook) | **0.201** | **0.273** | Las 352 consultas. Sin ningún dato de miniF2F |
| Selector LeanHammer (82M, estado→premisa) dentro del pool del prior | 0.056 | 0.099 | Serialización aproximada (enunciado, no estado pretty-printed sin notación). No concluyente |
| Mezcla prior + selector (z-scores) | **0.227** | **0.308** | El contexto sí suma sobre el prior |

Concentración del gold: los 100 lemas más usados cubren el 75 % de los usos y los 200 primeros el 88 %. `sq_nonneg` aparece en 16 719 de las 29 750 pruebas de Lean Workbook. Ejemplo cualitativo: para un problema sobre edades, la búsqueda NL devuelve `FirstOrder.Language.Structure.FG.mem_age_of_equiv`.

### 2.3 Lo que cierra Lean solo, sin LLM (sonda sobre los 60 problemas de `eval_v1`)

Script y datos: `data/eval/results/probe_close/` (`probe_close.py`, `probe.jsonl`, 360 filas). Cada variante es **la prueba entera** del teorema, con `maxHeartbeats 200000`, sobre el REPL y el Mathlib fijados. 0 llamadas LLM.

| Variante (táctica única) | Cerrados /60 | Mediana s | Cierra y `full` no |
|---|---|---|---|
| `grind` (sin premisas) | 7 | 0.2 | 4 |
| `grind +lax [top-16 de nuestro RAG, consulta NL]` | **6** | 0.5 | 3 |
| `grind +lax [top-16 de nuestro RAG, consulta formal]` | **6** | 0.6 | 3 |
| `grind +lax [prior de competición, top-32 estático]` | **9** | 0.6 | 5 |
| `grind +suggestions` (Sine Qua Non nativo) | **9** | 0.6 | 5 |
| `try?` (batería nativa) | **12** | 7.8 | 7 |
| *Referencia:* pipeline `full` de `eval_v1` (LLM, 7.6 llamadas de media) | 9 | 287 | — |

Lecturas:

1. **Las premisas que recupera hoy el RAG empeoran la automatización.** Con ellas, `grind` pierde un problema que sin premisas cierra (`amc12b_2003_p6` con NL, `mathd_algebra_104` con formal) y no gana ninguno. Es ruido inyectado.
2. **El prior y la selección nativa la mejoran**, con los mismos 2 problemas extra (`mathd_numbertheory_229`, `_728`). El prior es una tabla estática, sin modelo, y sin datos de miniF2F iguala al motor de Lean.
3. **Lean solo, sin LLM, ya iguala o supera al sistema completo**: `try?` cierra 12/60 frente a 9/60 de `full`. La unión de las variantes automáticas con `full` llega a **16/60**. 4 problemas (`mathd_algebra_77`, `mathd_numbertheory_155`, `_403`, `amc12b_2003_p6`) los cierra la automatización y no los resolvió ninguna de las 5 configuraciones de `eval_v1`. A la inversa, 4 problemas que resuelve `full` (`algebra_sqineq_2unitcircatblt1`, `mathd_algebra_28`, `amc12a_2016_p3`, `amc12b_2003_p17`) no los cierra ninguna táctica sola. **Los dos caminos son complementarios.** Ese es el argumento para CLOSE antes que el LLM (Fase 4) y para una línea base "sólo automatización" en la evaluación (Fase 6).
4. Esto se midió sobre el **goal raíz** (el teorema entero). Por goal intermedio, que es lo que propone este plan, los goals son más simples y la automatización cierra más. Es la hipótesis que mide la Fase 4.

### 2.4 Otros hechos que condicionan el diseño

- **`eval_v1` usó la tabla de muestra** (3 903 filas, sembrada con el gold). El RAG se midió en condiciones favorables y aun así no aportó (`final_report.md` §8).
- **El corpus de LeanSearch v2 deja fuera todo el núcleo de Lean, no sólo `eq_*`** (medido 2026-10-14; `data/eval/results/corpus_gap/`, `#check` uno por uno en el REPL):
  - Las 310 579 declaraciones del JSONL son de módulos `Mathlib.*`. No hay nada de `Init`/`Std` ni de core, y ningún nombre con `.eq_` o con último componente `eq_*`.
  - En las 438 pruebas verificadas de DeepSeek-Prover-V2, **el 28 % de los usos de premisas (734 de 2 585) son lemas que existen en Lean pero no en el corpus**: 160 nombres distintos.
  - Sólo el 4 % de esos usos son `eq_*` (`eq_comm`, `eq_of_sub_eq_zero`…). **El 96 % es núcleo**: aritmética modular de `Nat`/`Int` (`Nat.mul_mod` 43, `Nat.add_mod` 39, `Nat.mod_eq_of_lt` 39, `Nat.mod_mod` 37, `Int.mul_emod`…), `Eq.symm`, `Or.inl`/`Or.inr`, `congr_arg` y lógica (`or_iff_not_imp_left`, `ne_eq`, `and_imp`). Teoría de números depende justo de esos lemas.
  - El filtro propio de `configs/ingest.yaml` (`exclude_name_substrings: [".eq_"]`, pensado para los lemas de ecuación autogenerados `foo.eq_1`/`foo.eq_def`) también borraría lemas reales como `Nat.Prime.eq_one_or_self_of_dvd` si la fuente los trajera.
  - **Consecuencia para las métricas:** `premises_gold.jsonl` se construyó filtrando contra ese corpus, así que **excluye ese 28 %**, y todos los recall de premisas publicados (incluida la tabla 2.2) se miden sobre un gold recortado.
  - El disparador de DATA-1 (`PLAN_CORRECCIONES.md` 7.4, umbral 5 %) queda superado.
  - Los canales A y B no dependen de ese corpus: el B lee el entorno real de Lean y el A se filtra contra él. Para el canal C, ver la Fase 3, tarea 4b.
- **Lean v4.28.0-rc1 (el fijado) trae selección de premisas nativa** (verificado en el toolchain local):
  - `Lean/LibrarySuggestions/{MePo,SineQuaNon,SymbolFrequency,Default}.lean`;
  - Mathlib importa `Lean.LibrarySuggestions.Default` (`Mathlib/Init.lean`), que fija Sine Qua Non + teoremas del archivo actual;
  - tácticas `suggestions`, `grind +suggestions`, `simp? +suggestions` y `try?`.
- **`lean-interact` (instalado) ya expone lo necesario**: `CommandResponse.sorries` (goal + `proofState` por cada `sorry`) y `ProofStep(tactic, proofState)`, que ejecuta una táctica sobre ese estado sin re-elaborar el teorema.

### 2.5 El trabajo verificado se tira: prueba monolítica y replan sin memoria

- **Prueba monolítica.** El Autoformalizador entrega un único bloque de tácticas con todos los `have`, y el veredicto es binario.
  - Lean **no** rechaza el bloque entero: con recuperación de errores, elabora los `have` correctos y reporta el error en su posición.
  - Lo que tira el trabajo es el sistema: el siguiente intento regenera la prueba completa desde cero.
  - Medido en `eval_v1` (`full`/`no_rag`/`no_critic`, último intento fallido con ≥ 2 `have`): **en 55 de 99 intentos al menos un `have` completo estaba antes del primer error** y se descartó igual. La mediana es de 4 errores por intento.
- **Replan sin memoria (DES-1, abierto; `PLAN_CORRECCIONES.md` 7.1, decisión D5).** Sigue en el código:
  - `graph.py`, `plan()`, devuelve `"feedback": ""`;
  - el prompt del Planificador sólo tiene `{informal}`, `{formal}` y `{exemplars}`.
  
  Resultado en `eval_v1`: **65 replans** (35 en `full`) con **2 éxitos**, 133 798 tokens, y planes nuevos con similitud mediana 0.13 con el anterior. Es un remuestreo a ciegas, no un replan.

Las dos son la misma falla de diseño: **no hay un registro de qué subobjetivos ya están probados**. La Fase 2 lo introduce (tarea 5) y la Fase 2b lo usa en el replan.

---

## 3. Por qué falla preguntar en lenguaje natural (y por qué "formal" a secas tampoco basta)

1. **Asimetría de contenido.** El documento indexado describe *qué afirma* un lema: "the square of a real number is nonnegative". La consulta describe *el problema*: "Show that for real a, b, a² + b² ≥ 2ab". Un embedder de similitud temática no puede saber que el puente es `sq_nonneg (a - b)`. Ese conocimiento es de **uso**: qué lemas sirven para cerrar qué forma de goal. Se aprende de pruebas, no de descripciones. El prior de competición lo captura con una tabla de frecuencias.
2. **Granularidad.** El enunciado completo mezcla todas las necesidades de la prueba. El lema hace falta en un punto concreto, con un goal concreto (`0 ≤ (a - b) ^ 2`). Todos los selectores del estado del arte consultan con el estado de prueba: ReProver, LeanHammer, LeanSearch-PS y Lean State Search.
3. **El embedder genérico no lee Lean.** Qwen3-Embedding con el enunciado formal entero da 0.0-0.1 %. "Formal" funciona cuando la consulta es un goal corto y el modelo fue entrenado con pares (estado, premisa), como LeanHammer: recall@16 63.5 en Mathlib. Funciona todavía mejor cuando la selección la hace Lean mismo sobre los símbolos del goal (Sine Qua Non, MePo: recall@16 38.4 en Mathlib según el paper de LeanHammer).
4. **Pegar lemas en el prompt no es usarlos.** El LLM escribe 10-20 líneas de tácticas sin ver ningún goal intermedio. El lema correcto en el contexto no evita un `rw` que no encuentra el patrón. La automatización de Lean sí prueba las premisas contra el goal real, y devuelve cuál funcionó.

---

## 4. Arquitectura objetivo

### 4.1 Flujo por problema

PLAN (sin cambios: ejemplares informales NL→NL + Planificador)
→ GENERATE (sin cambios: boceto NL)
→ **SKETCH**: el Autoformalizador devuelve un **esqueleto** con los pasos `have hᵢ : … := by sorry` y el cierre final. Es 1 llamada, la misma que hoy.
→ **GOALS** (0 LLM): el REPL elabora el esqueleto en modo sonda. Devuelve un goal y un `proofState` por cada `sorry`. Un esqueleto que no elabora vuelve al Crítico con el error, igual que hoy.
→ **RETRIEVE por goal** (0 LLM): canales A + B + C, fusión RRF y top-k premisas con firma y cita.
→ **CLOSE por goal** (0 LLM): batería de tácticas con las premisas, vía `ProofStep` sobre el `proofState`. Se registra qué táctica y qué premisas cerraron cada goal.
→ **FILL** (LLM, sólo goals residuales): 1 llamada por intento que recibe *todos* los goals abiertos, cada uno con su goal exacto y sus premisas, y devuelve un bloque de tácticas por goal. Cada bloque se prueba con `ProofStep`.
→ **VERIFY** del archivo completo, con el validador de tácticas y la auditoría de axiomas de siempre.
→ CRITIQUE (sin cambios en su contrato; recibe además los goals residuales).

### 4.2 Seguridad (no se relaja nada de `AUDIT_2026-09-23` SEC-1/VER-1)

- El esqueleto con `sorry` se elabora en un **modo sonda** cuyo veredicto nunca puede ser `ok`. El único camino a `ok` sigue siendo `LeanChecker.check` sobre el archivo final ensamblado, que rechaza `sorry` y audita axiomas.
- El validador `find_forbidden_tactic` se aplica a **cada bloque** antes de `ProofStep`: tácticas del esqueleto, de la batería y de FILL.
- `grind +suggestions` y `suggestions` usan el motor que fija Mathlib. El LLM no puede cambiarlo: `set_library_suggestions` es un comando y ya está prohibido en tácticas.
- Las tácticas con `?` (`try?`, `exact?`) sólo se usan en CLOSE. Del resultado se extrae el script concreto ("Try this: …"), y ese script es el que entra al archivo final. El kernel nunca acepta un `?`.

### 4.3 Presupuesto RNF-3 (≤ 20 llamadas por problema)

| Llamada | Hoy | Nuevo |
|---|---|---|
| Planificador | 1 | 1 |
| Generador | 1 por intento | 1 por intento |
| Autoformalizador | 1 por intento (prueba entera) | 1 por intento (esqueleto) |
| FILL | — | 0 si CLOSE cierra todo, si no 1 por intento |
| Crítico | 1 por intento fallido | 1 por intento fallido |

Peor caso con `k_max = 3`: 1 + 3 × 4 = 13 llamadas, por debajo de 20. El retrieval y CLOSE cuestan 0. En la práctica bajan las llamadas, porque CLOSE cierra goals que hoy consumen intentos.

---

## 5. Alternativas evaluadas y descartadas

| Alternativa | Por qué no |
|---|---|
| Mejor reescritura de consultas NL (HyDE, más `search_queries`, LeanSearch con aumentación) | Ataca D1 desde el lado equivocado. El techo medido de la búsqueda NL con este índice es ~1 % de recall de premisas, y el problema es de uso, no de redacción |
| Consultar el índice actual con el enunciado formal | Medido: 0.000-0.001. El embedder genérico no está entrenado para estado→premisa |
| Entrenar o ajustar un selector propio (contrastivo estado→premisa) | Fuera del alcance §5.3 de la propuesta ("sin entrenar modelos desde cero") y del cómputo disponible. LeanHammer necesitó 6.5 días-A6000 |
| Servidor remoto `leanpremise.net` de LeanHammer | Dependencia de red y otra versión de Mathlib (rompe RNF-2); envía los goals a un tercero |
| LeanHammer completo (Zipperposition + Duper) | Paquete con dependencias nativas y otro toolchain. La parte de mayor valor, la selección nativa, ya está en el Lean fijado |
| Subir k o el número de consultas | Más lemas irrelevantes en el prompt sin mejorar recall (D1) ni consumo (D3) |

Queda como opción condicionada (Fase 5) el **selector LeanHammer servido localmente**. El modelo de 82M corre en CPU y se registraría como motor de `set_library_suggestions` en el entorno del verificador. Disparador: que los canales A + B no alcancen el criterio de la Fase 3.

---

## 6. Fases

Reglas de trabajo: las mismas de `PLAN_CORRECCIONES.md` §0 (corrección mínima, test que falla primero, `DECISIONS.md` desde **#89**, una carga pesada a la vez, `setsid nohup` para corridas largas, `graphify update .` tras cambiar código, sin commit salvo pedido, ninguna llamada de pago sin aprobación).

### Fase 0 — Banco de medición offline (0 LLM, US$0)

**Objetivo:** que cada fase siguiente se acepte o rechace con un número, no con una impresión.

1. **`scripts/eval_premises.py`, extensión.** Añadir las variantes de la tabla 2.2: denso NL, denso formal, prior, selector y mezcla. Por defecto sobre `mathlib_full`. Reportar recall@8/@16/@32 y escribir `data/eval/results/premises_v2.json`.
2. **`scripts/probe_close.py`.** Se promueve a `scripts/` el script ya escrito en `data/eval/results/probe_close/probe_close.py` (reanudable, salida persistente). Se parametriza la lista de variantes, para que cada canal nuevo se mida igual que en la sección 2.3: mismas tácticas, mismos heartbeats, una fila por problema y variante.
3. **Gold por goal**, para la Fase 3. Elaborar con `sorry` en cada `have` las pruebas verificadas de DeepSeek-Prover-V2 de los 352 problemas. Para cada `have`, registrar el goal (de `sorries`) y los identificadores de Mathlib usados en su bloque. Resultado: `data/eval/premises_gold_goals.jsonl`. Pares (goal, premisas) a nivel de subobjetivo: es la unidad que exige RF-2.

4. **Gold contra el entorno de Lean, no contra el JSONL.** `scripts/build_premise_gold.py` filtra hoy los identificadores contra `data/corpus/mathlib.jsonl`, y eso borra el 28 % de las premisas reales, las del núcleo (sección 2.4). Se reconstruye `premises_gold.jsonl`:
   - aceptando todo identificador que resuelve `#check` en el REPL con la cabecera del problema;
   - normalizando accesos a campo (`foo.mpr` → `foo`);
   - descartando nombres de tácticas.
   
   El código ya existe en `data/eval/results/corpus_gap/corpus_gap.py`. Las cifras de la tabla 2.2 se vuelven a publicar con el gold nuevo, y las viejas quedan como "gold recortado al corpus".

**Aceptación:** las cifras de las secciones 2.2 y 2.3 se reproducen con estos scripts (±1 punto). El gold por goal existe para ≥ 300 problemas. El gold nuevo incluye lemas de núcleo (`Nat.mul_mod`, `Eq.symm`…).
**Plan B (tarea 3):** si una prueba no se deja partir en `have`, se usa su goal raíz y las premisas del problema entero.

### Fase 1 — Canal A: prior de competición

1. **Fuente:** `Goedel-LM/Lean-workbook-proofs` (29 750 pruebas, MIT), descargada a un directorio propio. Se registra en `configs/versions.yaml` con su revisión de HF.
2. **Higiene:**
   - Excluir toda prueba cuyo enunciado coincida con un problema de miniF2F-v2 (por nombre, y por coseno > 0.9 con `scripts/contamination.py`).
   - Quedarse sólo con identificadores que **existen en el Mathlib fijado**, con `LeanChecker.check_identifiers_exist`, no contra el JSONL de LeanSearch, que tiene huecos.
   - Descartar definiciones y tokens espurios que no son lemas usables como premisa (`Real.sqrt`, `Int.ModEq`, `sequence`, `Cauchy`…), filtrando por `kind = theorem`. El `kind` y la firma se toman **del REPL** (`#check`/`#print`), no del JSONL, para que entren los lemas de núcleo (`Nat.mul_mod`, `Nat.mod_eq_of_lt`, `eq_comm`) que el corpus no tiene.
3. **Artefacto:** tabla LanceDB `premise_prior` (`decl_name`, `freq`, `log_prior`, `kind`, `signature`, constantes de la firma). Se construye con un script de una pasada; no hace falta re-embeber.
4. **Filtro por goal:** de la tabla se sirven sólo los lemas cuyas constantes principales aparecen en el goal (ℝ, ℕ, `Finset.sum`, `Real.sqrt`, `abs`, `%`…), más las de álgebra ordenada (`sq_nonneg`, `mul_pos`…), que se sirven siempre.

**Aceptación:** recall@16 de premisas ≥ 0.27 sobre las 352 (gold por problema), y no peor con el gold por goal de la Fase 0.
**Plan B:** si el filtro por constantes baja el recall, se sirve el top-k estático y el filtro queda sólo para el reranking.

### Fase 2 — Input nuevo: esqueleto y goals (R1)

1. **Prompt del Autoformalizador** (`configs/agents.yaml`). Devuelve un esqueleto: los pasos del boceto como `have hᵢ : <afirmación> := by sorry` más el cierre. Instrucción explícita: afirmaciones cortas y cerrables. Sube `prompt_version`.
2. **`LeanChecker.goals(statement, skeleton)`**, método nuevo. Elabora en modo sonda y devuelve `[{goal, proof_state}]` desde `sorries`. Nunca devuelve un `Verdict` `ok`. Test: un esqueleto con dos `have` devuelve dos goals con los tipos correctos, y uno con error de elaboración devuelve el error.
3. **`LeanChecker.step(proof_state, tactic)`**, método nuevo, sobre `ProofStep`. Pasa por `find_forbidden_tactic` antes de ejecutar. Test: `sorry` rechazado sin tocar Lean; `norm_num` cierra `2 + 2 = 4`.
4. **Ensamblado:** el archivo final reemplaza cada `sorry` por el bloque que lo cerró. `build_file` sigue siendo la única fuente (BUG-2).
5. **Registro de goals probados** (cierra la mitad "monolítica" de D5).
   - El estado del grafo guarda, por problema, los `have` cuya afirmación ya se cerró: afirmación, bloque de tácticas y premisas usadas.
   - Un reintento **no** regenera esos pasos: el Autoformalizador recibe el esqueleto con los pasos cerrados ya rellenos y sólo reescribe los abiertos.
   - CLOSE y FILL sólo trabajan sobre goals abiertos.
   - Si un reintento cambia la afirmación de un `have` cerrado, ese paso pasa a abierto. La clave es el texto normalizado de la afirmación.
   - El Crítico recibe los errores **por goal**, no el primer error del archivo.

**Aceptación:**
- En `eval_v1`, con el mismo LLM, ≥ 90 % de los esqueletos elaboran a la primera o tras una corrección del Crítico. Se mide en el smoke de la Fase 6; aquí basta con tests.
- Test con el LLM falso: un intento cierra `h1` y falla `h2`; el prompt del intento 2 trae `h1` relleno, y el archivo final contiene el bloque original de `h1`.
- La suite sigue en verde y los tests de Lean real pasan.

**Plan B:** si el modelo no produce esqueletos fiables, se usa el goal raíz del teorema como único goal. Así todo lo demás funciona igual, con un solo goal.

### Fase 2b — Replan con memoria (DES-1; sustituye a `PLAN_CORRECCIONES.md` 7.1, decisión R6)

1. El prompt del Planificador recibe `{feedback}`, con tres partes:
   - la razón del Crítico;
   - la estrategia anterior;
   - **la lista de subobjetivos ya probados** (del registro de la Fase 2, tarea 5), presentados como hechos disponibles.
   
   Instrucción: replanificar sólo lo que falló, reutilizando los hechos probados.
2. `graph.py`, `plan()`: deja de devolver `"feedback": ""` al replanificar. Lo vacía después de usarlo, como ya proponía `PLAN_CORRECCIONES.md` 7.1.
3. Los `have` probados se conservan a través del replan, como hipótesis del esqueleto nuevo. El replan cuesta 1 llamada, igual que hoy.
4. `prompt_version` sube. `eval_v1` no se repite: sus trazas quedan con la versión vieja.

**Aceptación:**
- Test con el LLM falso: el segundo prompt del Planificador contiene el feedback y los subobjetivos probados.
- En la Fase 6 se reporta la similitud mediana plan anterior ↔ plan nuevo (hoy 0.13) y los éxitos tras un replan (hoy 2/65).

No se exige una mejora significativa (n pequeño); sí que el replan deje de ser un remuestreo: similitud > 0.13 con más éxitos o menos tokens. **Plan B:** si no mejora, `max_replans: 0` y ese presupuesto pasa a FILL.

### Fase 3 — Retrieval por goal: canales A + B + C

1. **`RetrievalRequest.mode = "state"`**, valor nuevo, compatible con el contrato §8: `queries = [goal pretty-printed]`.
2. **Canal A:** `premise_prior` filtrado por las constantes del goal, ordenado por `log_prior`.
3. **Canal B:** `ProofStep(proof_state, "suggestions")`. Se parsea su mensaje (nombres + score) y es 0 LLM. Si la táctica no está disponible en el estado, se omite el canal y se registra en la traza.
4. **Canal C:** búsqueda actual sobre `mathlib_full` con el goal como consulta, en modo `formal`, con el reranker por GPU (R5, `PLAN_CORRECCIONES` 7.7). Aporta lemas específicos que no están en el prior (`Real.log_mul`, `Nat.Prime.two_le`).
4b. **Núcleo de Lean en el índice** (cierra D4 para el canal C).
   - Extraer del entorno del REPL las declaraciones que no están en el corpus y aparecen en el prior o en el gold nuevo (del orden de cientos), con su firma vía `#check`.
   - Indexarlas en una tabla `lean_core_extra`, con `text` = nombre + firma y sin informalización, servida junto a `mathlib_full`.
   - En `configs/ingest.yaml`, el filtro `".eq_"` pasa a excluir sólo los lemas de ecuación autogenerados (sufijos `.eq_<n>` y `.eq_def`), para no borrar lemas reales si la fuente los trae.
   
   La extracción completa de Init/Std/Batteries queda como plan B de `PLAN_CORRECCIONES.md` (Fase 10, "Completar el corpus").
5. **Fusión:** RRF (la existente, `rrf_k`). Se dedupea por `decl_name`. Salida: top-16 con firma, `docs_url` y cita `M*` como hoy. Las `search_queries` NL del Planificador dejan de consultar Mathlib.
6. **Traza:** cada evento lleva el canal de cada hit, para medir qué canal aporta.

**Aceptación:** sobre el gold por goal de la Fase 0, recall@16 de la fusión ≥ max(canal A, canal B) + 3 puntos, con canal C sólo como desempate.
**Plan B:** si C no suma ≥ 1 punto, se apaga y queda A + B (sin GPU). Si A + B quedan por debajo de 0.30, se activa la Fase 5.

### Fase 4 — Consumo por Lean: CLOSE por goal

1. **Batería por goal** (orden fijo, cada paso con `ProofStep` y presupuesto de heartbeats fijo):
   1. `grind +lax [premisas]`
   2. `nlinarith [premisas aplicables como términos, p.ej. sq_nonneg (a - b) generado de las variables del goal]`
   3. `simp only [premisas] at *` seguido de `omega` / `norm_num` / `linarith`
   4. `positivity`
   5. `try?`, del que se extrae el script concreto.
   
   La batería sale de configuración (`configs/agents.yaml: close.tactics`), sin código por táctica.
2. **Registro:** por goal se guarda qué táctica cerró y **con qué premisas**. `used_in_proof` se calcula sobre el archivo final como hoy (`identifiers_used`) y ahora refleja premisas del RAG que el kernel aceptó.
3. **Ablación interna:** la misma batería sin premisas (`grind`, `nlinarith` sin lista…) es lo que corre `no_rag` (R3).

**Aceptación:** con 0 LLM sobre los goals raíz de `eval_v1` (sonda 2.3, `probe_close.py`):
- la batería con premisas de la fusión A + B (+ C) cierra **≥ 12/60** (lo que ya cierra `try?` solo);
- cierra **≥ 1 problema** que la misma batería sin premisas no cierra;
- `grind +lax [fusión]` cierra **≥ 9/60**, igual que el prior y la selección nativa por separado, y nunca menos que `grind` sin premisas (7/60).

Si no se cumple, el RAG no aporta en el consumo y se revisa la fusión antes de seguir. El criterio del RAG actual (6/60) es el que no se debe repetir.

### Fase 5 — (Condicionada) selector LeanHammer local

Disparador: Fase 3 por debajo de 0.30 de recall@16 por goal.

1. Servir `hanwenzhu/all-distilroberta-v1-lr2e-4-bs256-nneg3-ml-ne5-may07` (82M, `sentence-transformers`, ~330 MB) en CPU o por llama.cpp. Embeber las premisas del Mathlib fijado con la serialización del paper: firma `kind nombre args : tipo`, nombres totalmente calificados y sin notación (`pp.notation false`, extraída desde el REPL, no desde el JSONL).
2. Consultar con el goal serializado igual. Entra como canal D en la RRF.

**Aceptación:** +3 puntos de recall@16 por goal sobre A + B + C. Si no se alcanza, se descarta y se documenta.

### Fase 6 — Re-evaluación end-to-end (requiere R4)

1. **Smoke:** 5 triviales + 8 dev con `full` nuevo, ≈ US$0.01.
2. **`eval_v1` sobre `mathlib_full`** con tres configuraciones de pago y una gratuita:
   - `full` (nuevo);
   - `no_rag` (misma batería sin premisas, R3);
   - `rag_nl` (la estrategia actual, para medir el cambio de input);
   - **`baseline_auto`** (nueva, 0 LLM): sólo la batería CLOSE sobre el goal raíz, sin agentes. Hoy cierra 12/60 (sección 2.3). Es la línea base que el sistema con agentes tiene que superar para justificar sus llamadas.
   
   Mismas semillas, mismo modelo y `--workers 6`.
3. **Informe:** tabla de pass@1 con IC de Wilson, McNemar pareado y `used_in_proof` por configuración. También: aporte por canal (desde la traza) y fracción de goals cerrados por CLOSE vs. FILL.

**Aceptación del plan completo:**
- (a) `used_in_proof` > 0 en ≥ 30 % de los problemas resueltos por `full`;
- (b) `full` ≥ `no_rag` + 3 problemas;
- (c) `full` > `rag_nl`;
- (d) `full` > `baseline_auto`. Como referencia, la unión medida de automatización y del `full` actual ya llega a 16/60.

Con n = 60 no se exige significancia; se reporta el p de McNemar tal cual. Si (b) no se cumple, se reporta honestamente como en `final_report.md` §7.

---

## 7. Orden, dependencias y costo

| Fase | Depende de | LLM | US$ | Esfuerzo |
|---|---|---|---|---|
| 0 | — | 0 | 0 | S |
| 1 | 0 | 0 | 0 | S |
| 2 | R1 | 0 (tests) | 0 | M |
| 2b | 2, R6 | 0 (tests) | 0 | S |
| 3 | 1, 2 (R5 para C) | 0 | 0 | M |
| 4 | 2, 3 | 0 | 0 | S-M |
| 5 | 3 (condicionada) | 0 | 0 | M |
| 6 | 4, R4 | sí | ≈ 0.45 | S |

Las Fases 0 y 1 se pueden hacer ya y por sí solas dan el número que justifica el resto.

---

## 8. Riesgos

| Riesgo | Mitigación |
|---|---|
| El gold de premisas refleja el estilo de un prover (DeepSeek-Prover-V2, muy `nlinarith [sq_nonneg …]`) y el prior viene de otro parecido (Goedel-Prover) | La métrica final no es el recall sino el cierre en el kernel (2.3, Fase 4) y el pass@1 (Fase 6) |
| El LLM sin razonamiento (DeepSeek V4 Flash, `reasoning_effort: off`) escribe esqueletos que no elaboran | Plan B de la Fase 2 (goal raíz). El Crítico ya clasifica `lean_syntax` |
| `try?` / `grind` lentos en goals difíciles | Presupuesto fijo de heartbeats por paso; la batería corta en el primer éxito |
| Huecos del prior para áreas raras (combinatoria) | Los canales B y C no dependen del prior |
| RAM (7,5 GB): REPL 1,9 GB + embedder 1,8 GB | El reranker va en la GPU (R5). El selector de la Fase 5 pesa ~0,3 GB |

---

## 9. Fuentes

- LeanHammer, *Premise Selection for a Lean Hammer* (Zhu, Clune, Avigad, Jiang, Welleck, 2025): selector estado→premisa, recall@16 63.5 vs. MePo 38.4 vs. ReProver 35.1; serialización de estados y premisas. https://arxiv.org/abs/2506.07477 · paquete https://github.com/hanwenzhu/premise-selection (Apache-2.0)
- LeanSearch v2 (2026): modo estándar embedding + reranker; modo razonamiento sketch-retrieve-reflect; 20 % vs 4 % de éxito sin recuperación en su bucle fijo. https://arxiv.org/abs/2605.13137
- *A Semantic Search Engine for Mathlib4* (LeanSearch, 2024): aumentación de consultas NL. https://arxiv.org/abs/2403.13310
- Premise retrieval por estado (Lean State Search / premise-search.com, 2025). https://arxiv.org/abs/2501.13959
- REAL-Prover / LeanSearch-PS (2025): estado de prueba y teoremas en un mismo espacio. https://arxiv.org/abs/2505.20613
- DeepSeek-Prover-V2 (2025): descomposición en subobjetivos `have … := by sorry` resueltos recursivamente (base de la Fase 2). https://arxiv.org/abs/2504.21801
- Lean 4.26 release notes: `grind +suggestions`, `simp? +suggestions`, Sine Qua Non por defecto, `try?` con sugerencias. https://lean-lang.org/doc/reference/stable/releases/v4.26.0/
- LeanInteract (`sorries` con `proofState`, `ProofStep`). https://github.com/augustepoiroux/LeanInteract
- Goedel-LM/Lean-workbook-proofs (29 750 pruebas, MIT). https://huggingface.co/datasets/Goedel-LM/Lean-workbook-proofs
