# Mejoras de RagSolver — qué cambiamos y cómo (14-22 de octubre de 2026)

Resumen de todo lo que se hizo para que el sistema resolviera más problemas. El detalle está en:
- `DECISIONS.md`, entradas #89 a #115 (cada decisión con su evidencia);
- los planes `PLAN_MEJORA_RAG.md` y `PLAN_REDISENO_PROVER.md`;
- los reportes `eval_report_v2.md`, `eval_report_v3.md` y `eval_report_v4.md` (§9).

---

## 1. Resultado final

| Sistema | Problemas resueltos (30 problemas pareados) | Dificultad 1 · 2 · 3 |
|---|---|---|
| Sistema original (eval-v1, en esos 30) | 5 | — |
| Primera mejora: flujo por goal (eval-v2) | 8 | 8/16 · 0/10 · 0/4 |
| **Rediseño con demostrador local (eval-v3)** | **21** | 13/16 · **6/10** · **2/4** |

**El sistema pasó de resolver el 17 % (original) y el 27 % (primera mejora) al 70 % de estos problemas.** Resuelve 13 que antes no resolvía y no pierde ninguno (McNemar p = 0.0002). Incluye dos problemas de IMO. Todas las pruebas las acepta el kernel de Lean con el enunciado original intacto y sólo axiomas estándar.

Gasto total de API en estos dos días: unos **US$0.36**. La mayor parte se fue en la evaluación intermedia (eval-v2). El rediseño final usa la GPU local y casi no gasta API.

---

## 2. Punto de partida: por qué el RAG no daba resultados

El sistema original buscaba lemas de Mathlib **con frases en lenguaje natural** y los pegaba en el prompt para que el LLM escribiera la prueba completa. Lo medimos sin gastar en LLM:

| Problema | Evidencia |
|---|---|
| **La consulta no servía.** Las pruebas de competición usan lemas genéricos (`sq_nonneg`, `mul_pos`) que no se parecen al enunciado del problema | La búsqueda en lenguaje natural encontraba el 1.2 % de los lemas correctos; para un problema de edades devolvía `FirstOrder.Language...age` |
| **El corpus estaba incompleto.** El dataset de LeanSearch v2 sólo trae Mathlib, sin el núcleo de Lean | El 28 % de los lemas que usan las pruebas reales (`Nat.mul_mod`, `Eq.symm`, `Or.inl`) no existía en el índice. Sólo el 4 % eran `eq_*` |
| **Los lemas no se usaban.** Pegar lemas en el prompt no hace que el LLM los use | Con el 32 % de los lemas correctos en el prompt, Lean casi no usó ninguno |
| **Se tiraba trabajo ya verificado.** Cada reintento rehacía la prueba entera; el replan no recibía el feedback del Crítico | 55 de 99 intentos fallidos tenían pasos correctos que se descartaron; 65 replans con 2 éxitos |

---

## 3. Primera mejora: buscar para cada paso de Lean, no para el enunciado

**Idea:** el RAG busca lemas para **el paso concreto que hay que demostrar**, con el goal exacto que muestra Lean, y es Lean quien prueba esos lemas antes que el LLM.

**Cómo se hizo:**

1. **Partir la prueba en pasos.** El modelo escribe la prueba con pasos `have`, y el sistema la parte mecánicamente: cada paso pasa a `sorry` y su bloque queda como primer intento (`lean/verify.py::skeletonize`). Lo hace el sistema porque el modelo casi nunca escribía el esqueleto con `sorry` que se le pedía.
2. **Obtener el goal exacto de cada paso.** El REPL de Lean (`lean-interact`) elabora el esqueleto y devuelve el goal y el estado de cada `sorry` (`LeanChecker.goals`). Después se aplican tácticas sobre ese estado sin re-elaborar el teorema (`LeanChecker.step`).
3. **Lista de lemas de competición** (`scripts/build_premise_prior.py`). Se contó la frecuencia de cada lema en 29 750 pruebas de Lean Workbook (licencia MIT) y se validó cada nombre contra Lean: quedaron 778 lemas, incluidos los del núcleo. Para cada paso se filtran según los símbolos del goal (`rag/premises.py`). Encuentra el 33 % de los lemas correctos por paso; el índice viejo, el 3 %.
4. **Batería de tácticas por paso** (CLOSE, sin LLM): `grind` con los lemas → `nlinarith` con pistas como `sq_nonneg (a - b)` → `grind +suggestions` (la selección de lemas nativa de Lean 4.28) → `try?`. Gana la primera que cierra el paso.
5. **El LLM sólo ve los pasos que siguen abiertos** (FILL), con su goal exacto, los lemas recuperados y el error de la automatización.
6. **Registro de pasos probados.** Un paso ya demostrado no se vuelve a demostrar en el siguiente intento ni tras un replan.
7. **Replan con memoria.** El Planificador recibe el feedback del Crítico, su estrategia anterior y los pasos ya probados.
8. **Conteo honesto de citas.** "Lema usado" ahora significa "está en el término que aceptó el kernel" (`Verdict.used_constants`), no "aparece en el texto de la prueba".

**Errores que encontramos y arreglamos en el camino:**
- `sq_nonneg` no aparecía en la lista porque era un nombre ambiguo con `open Nat`;
- la sangría rota cuando el modelo devolvía la primera línea sin indentar;
- estados de prueba obsoletos cuando el REPL se reiniciaba;
- timeouts demasiado cortos para `try?`;
- reintentos insuficientes ante el error 429 de OpenRouter;
- el filtro de ingesta borraba lemas reales como `Nat.Prime.eq_one_or_self_of_dvd`.

**Resultado (eval-v2):**
- Duplicó al sistema viejo en problemas fáciles, pero en los difíciles seguía en 0.
- Las premisas recuperadas apenas se usaban: `full` 8 y `no_rag` 9.

**Diagnóstico:**
- En los problemas difíciles quedaban abiertos 5.2 de 5.7 pasos.
- El modelo general (DeepSeek V4 Flash, sin razonamiento) no sabe demostrar en Lean a ese nivel.
- El 28 % de los fallos eran JSON inválido.

---

## 4. Rediseño dirigido: un demostrador especializado en tu GPU

**Idea:** los mejores sistemas en miniF2F no usan modelos generales, sino demostradores entrenados para Lean que generan varias pruebas y se quedan con la que verifica. DeepSeek-Prover-V2 usa justo la estructura que ya teníamos: un modelo general parte la prueba y uno pequeño demuestra los pasos.

**Cómo se hizo:**

1. **El modelo:** Goedel-Prover-V2-8B, licencia Apache-2.0, con 84.6 % pass@32 en miniF2F según su paper. Versión GGUF de la comunidad, cuantización **Q6_K** (6.7 GB, en `tools/models/`). No hay demostradores en OpenRouter (lo comprobamos), así que corre local.
2. **Servirlo en la RX 9060 XT (16 GB):**
   - `llama-server` de llama.cpp con Vulkan, el mismo programa que ya se usaba para los embeddings;
   - lanzado desde WSL y escuchando sólo en la red interna WSL↔Windows (no en tu red local);
   - 8 slots de 12 288 tokens, caché KV en q8_0 y flash attention.
   - Comparamos Q8 con 4 slots y Q6_K con 8: Q6_K es más rápida por muestra (45 tok/s frente a 36), y la GPU se satura en unos 70 tok/s en total.
3. **Etapa 0, sin API** (`prover.py::prove`, nodo `prove` del grafo):
   - el demostrador recibe el teorema completo en su formato de prompt;
   - genera 4 muestras en paralelo, las verifica y, sólo si ninguna funciona, genera 4 más;
   - si tampoco, hace una ronda de autocorrección con los errores de Lean.
4. **Seguridad:** del modelo se toman **sólo las tácticas**, que se insertan en el enunciado original del problema; así no puede debilitarlo. Cada candidato pasa por el mismo verificador de siempre: validador de tácticas, kernel y auditoría de axiomas.
5. **Si la etapa 0 no resuelve,** entra el flujo por goal de la sección 3. Los pasos que la batería no cierra se le pasan al demostrador como lemas sueltos (`goal_to_theorem`, con `rename_i` para los nombres inaccesibles), y sólo después al LLM de pago.
6. **Nuevo rol del RAG:** el modelo se entrenó con Lean 4.9 y usa nombres de lemas que ya cambiaron. Ante "este identificador no existe", el RAG busca nombres parecidos, comprueba con `#check` que existan en nuestro Mathlib y se los pasa al demostrador para que corrija.
7. **Caché en disco de las muestras.** `baseline_prover` y `full` comparten exactamente las mismas muestras: no se generan dos veces y la comparación queda pareada.
8. **Los fallos de formato ya no matan problemas.** Un Crítico o un FILL con JSON inválido ahora reintenta en lugar de abandonar.

**Resultado (eval-v3): 21/30 frente a 8/30.** Todo lo resolvió la etapa 0, sin API.

---

## 5. Lo que no funcionó (para ser honestos)

- **El RAG todavía no suma.** En el flujo por goal, `grind` ya conoce los lemas genéricos y no usa los que le damos. En el rediseño, de 8 nombres sugeridos el kernel no usó ninguno. El índice denso, consultado con el goal, sólo encontraba el 3 % de los lemas correctos y se apagó.
- **Los agentes por API tampoco suman sobre el demostrador.** Corrieron en los 9 problemas que el demostrador no resolvió, gastaron US$0.06 y no resolvieron ninguno.
- **Posible contaminación.** miniF2F es el benchmark público de este demostrador. Para medir si generaliza hace falta un conjunto de problemas que no venga de miniF2F.

---

## 6. Cómo se protegió el gasto

- Evaluación en rondas de 15 problemas, con un guardia (`data/eval/results/round_check.py`) que mide el gasto real de la key y **detiene todo** si la proyección supera el saldo. Ocurrió una vez y funcionó.
- Las líneas base sin LLM (`baseline_auto`, `baseline_prover`) se corrieron con la key deshabilitada, para que ningún bug pudiera gastar.
- Ante la saturación de DeepInfra subimos el tope de precio de OpenRouter de 1 a 8 proveedores, a pedido tuyo. El costo esperado casi no cambió porque se sigue usando primero el más barato.

---

## 7. Archivos principales

| Qué | Dónde |
|---|---|
| Selección de lemas por paso | `src/ragsolver/rag/premises.py` (nuevo) |
| Demostrador local, autocorrección con RAG, caché | `src/ragsolver/prover.py` (nuevo) |
| Grafo: etapa 0, CLOSE, FILL, registro de pasos, replan con memoria | `src/ragsolver/orchestrator/graph.py` |
| Líneas base `baseline_auto` y `baseline_prover` | `src/ragsolver/orchestrator/run.py` |
| Goals, `step`, `skeletonize`, `fill_sorries`, `signatures`, constantes usadas | `src/ragsolver/lean/verify.py` |
| Prompts (versión 3), batería, demostrador, configuraciones | `configs/agents.yaml` |
| Datos nuevos | `data/corpus/premise_prior.jsonl`, `data/eval/premises_gold_goals.jsonl`, `data/eval/prover_cache.jsonl` |
| Scripts de medición | `scripts/build_premise_prior.py`, `build_premise_gold_goals.py`, `eval_premises_goals.py`, `probe_close.py`; `data/eval/results/eval_v3_analysis.py` |
| Tests nuevos | `tests/test_goal_mode.py`, `test_goal_mode_slow.py`, `test_prover.py` (149 rápidos + 11 con Lean real, en verde) |

---

## 8. Cómo volver a correrlo

1. **Levantar el demostrador en la GPU** (desde WSL, en la raíz del repo):
   `./tools/llama-vulkan/llama-server.exe -m tools/models/Goedel-Prover-V2-8B.Q6_K.gguf -ngl 99 --host 172.31.112.1 --port 8082 -c 98304 --parallel 8 -fa on -ctk q8_0 -ctv q8_0 --jinja`
2. **Correr una evaluación:**
   `RAGSOLVER_MATHLIB_TABLE=mathlib_full .venv/bin/python -m ragsolver.orchestrator.run --problems data/eval/problems/eval_v1_round1.jsonl --configs baseline_prover,full --run-id <nombre> --workers 2`
3. **Apagar el servidor al terminar:**
   `pgrep -f "llama-server[.]exe" | xargs kill`

---

## 9. Lo que se probó después (eval-v4, 18-22 de octubre; `eval_report_v4.md`, DECISIONS #116-#129)

Las cuatro propuestas que estaban aquí se hicieron:
1. **El respaldo por API está apagado.**
2. **Fuera de miniF2F:** el demostrador solo resuelve **21 de 32** problemas del PDF (66 %; 70 % en miniF2F). No había contaminación.
3. **32 intentos en vez de 8:** 0 de 6 en los difíciles. Más intentos no ayudan.
4. **El plan y los lemas dentro del prompt del demostrador (DSP):** 0 de 7 en los difíciles. En el PDF resuelve `pdf_p33`, que el demostrador no saca ni con 16 intentos, aunque en el total empata (21/32).

Además:
- **2 de los 9 difíciles son falsos** tal como están en miniF2F; se demostró en Lean que su negación es cierta.
- **Dos ideas nuevas resolvieron `amc12b_2002_p3`**, el primer problema que el sistema resuelve y el demostrador solo no:
  - **SKETCH:** el modelo con razonamiento escribe el esqueleto de la prueba en Lean y el demostrador cierra cada paso;
  - **RAG de pruebas:** se recuperan pruebas verificadas de problemas parecidos (Lean Workbook) y se le dan como ejemplo, gratis.
- **SKETCH funcionó a medias por dos fallos del sistema,** ya corregidos (#128):
  - el modelo se quedaba sin tokens antes de escribir el Lean;
  - un paso lento reiniciaba Lean y dejaba sin probar los pasos de los otros hilos.

  Con los arreglos (eval-v4b), SKETCH resuelve **22 de 32** del PDF: es la primera vez que el sistema supera al demostrador solo fuera de miniF2F. Gana `pdf_p41` (lo hizo el esqueleto) y `pdf_p33` (reparado gratis) y pierde `pdf_p58`; no es significativo.
- **Hugging Face:**
  - código, docs, notebook, LICENSE y CITATION en <https://huggingface.co/CamiloS1111/ragsolver>;
  - índices, corridas y caché en <https://huggingface.co/datasets/CamiloS1111/ragsolver-index>.

  La demo de Gradio corre en local, porque el Space ahora cobra (PRO).

Gasto de API de eval-v4 y eval-v4b: **US$0.33**.

## 10. Qué sigue (propuesto, no hecho)

1. Una sola configuración que encadene etapa 0 → RAG de pruebas → SKETCH.
2. Partir en subpasos los pasos del esqueleto que quedan abiertos (descomposición recursiva).
3. Bajar a 0.85 el umbral que excluye "el mismo problema" en el RAG de pruebas: en el PDF hubo vecinos de 0.90.
