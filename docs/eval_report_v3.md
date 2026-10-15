# Evaluación eval-v3 — rediseño con demostrador especializado local

> 2026-10-15. Rediseño: `docs/PLAN_REDISENO_PROVER.md` (DECISIONS #110-#115).
> Problemas: las rondas 1-2 de eval-v1 (30 problemas), **pareados** con eval-v2 (`docs/eval_report_v2.md`).
>
> Datos:
> - `data/eval/runs/eval-v3/` (resultados y trazas);
> - `data/eval/results/eval_v3_analysis.json` (se regenera con `data/eval/results/eval_v3_analysis.py`);
> - `eval_v3_round{1,2}_check.json` (guardia de costo).

---

## 1. Resultado

| Sistema | Resueltos /30 | IC95 Wilson | Dificultad 1 / 2 / 3 | Costo de API |
|---|---|---|---|---|
| **v3 `full`** (rediseño completo) | **21** | 0.52-0.83 | 13/16 · **6/10** · **2/4** | US$0.061 |
| v3 `baseline_prover` (sólo el demostrador local) | 21 | 0.52-0.83 | 13/16 · 6/10 · 2/4 | **US$0** |
| v2 `full` (sistema de anoche) | 8 | 0.14-0.44 | 8/16 · 0/10 · 0/4 | US$0.108 |
| v2 `no_rag` | 9 | 0.17-0.48 | 8/16 · 1/10 · 0/4 | US$0.096 |
| v2 `baseline_auto` (tácticas de Lean, sin LLM) | 7 | 0.12-0.41 | 7/16 · 0/10 · 0/4 | US$0 |

Comparaciones pareadas (McNemar exacto):

| A | B | sólo A | sólo B | p |
|---|---|---|---|---|
| v3 `full` | v2 `full` | **13** | **0** | **0.0002** |
| v3 `full` | v2 `no_rag` | 12 | 0 | 0.0005 |
| v3 `baseline_prover` | v2 `baseline_auto` | 14 | 0 | 0.0001 |
| v3 `full` | v3 `baseline_prover` | 0 | 0 | 1.0 |

**El rediseño resuelve 2.6 veces más problemas (21 frente a 8) y no pierde ninguno de los que resolvía el sistema anterior.** Es la primera diferencia estadísticamente significativa del proyecto. La mejora está en los problemas difíciles: de 0/14 a 8/14 en dificultad 2-3, incluidos `imo_1964_p1_1` e `imo_1965_p1`.

Todas las pruebas pasaron el verificador completo:
- el enunciado del problema intacto: el modelo sólo aporta las tácticas;
- el validador de tácticas;
- el kernel;
- la auditoría de axiomas (sólo `propext`, `Classical.choice`, `Quot.sound`).

Se revisaron a mano las dos de IMO: son demostraciones reales, paso a paso.

## 2. Qué componente aporta qué (respuesta honesta)

- **El demostrador hace todo el trabajo:** los 21 problemas los resolvió la etapa 0 (Goedel-Prover-V2-8B sobre el teorema completo), sin ninguna llamada a la API.
- **Los agentes por API no añadieron ningún problema.** Corrieron en los 9 que la etapa 0 no resolvió (3.4 llamadas de media en `full`), gastaron US$0.06 y no resolvieron ninguno.
- **El RAG no añadió ningún problema.**
  - Por diseño, el canal nuevo (sugerir nombres que existen en el Mathlib fijado cuando el demostrador usa uno viejo) sólo actúa en la autocorrección.
  - Actuó en 2 correcciones, con 8 nombres citados, y el kernel no usó ninguno.
  - Las premisas por goal (Fase 3) no llegan a usarse, porque la etapa 0 resuelve antes de que haya goals.
- **En resumen:** `full` = `baseline_prover` en los 30 problemas. Con este demostrador, lo que decide el resultado es el modelo especializado más la verificación formal. La orquestación de agentes y el RAG, en su forma actual, no suman sobre él en este conjunto.

## 3. Costo y tiempo

- **API:** US$0.0129 (ronda 1) + US$0.0477 (ronda 2) = **US$0.061**. Todo fue a los agentes sobre los problemas no resueltos. Saldo de la key: **US$0.059**.
- **GPU** (RX 9060 XT, Q6_K, ~70 tok/s agregados):
  - unas 4 horas para las dos configuraciones;
  - media de 7.5 muestras por problema en `full`, la mayoría reutilizadas de la caché de `baseline_prover`;
  - los problemas fáciles salen en el primer lote de 4 muestras (1-3 min); los difíciles agotan las 8 y la corrección (~12 min).

## 4. Validez y limitaciones

- **Contaminación posible.** miniF2F es el benchmark público con el que se evalúa (y se compara) Goedel-Prover-V2. Los enunciados son de miniF2F-v2 (corregidos), pero el modelo puede haber visto problemas parecidos en su entrenamiento sintético. Esta evaluación mide el sistema con este demostrador, no la capacidad de generalizar a problemas nuevos. Para eso haría falta un conjunto fuera de miniF2F, como el PDF propio o PutnamBench.
- **n = 30**, un subconjunto (rondas 1-2) del conjunto de 60. Las rondas 3-4 no se evaluaron con el rediseño por saldo.
- **Comparación entre sistemas, no entre modelos con igual presupuesto.** v2 usaba ~8 llamadas a un modelo general por API; v3 usa hasta 8 muestras locales de un modelo especializado, más los agentes como respaldo.
- **El modelo se entrenó con Lean 4.9:** las pruebas que usan nombres renombrados en Mathlib v4.28 fallan. Las 9 no resueltas incluyen problemas donde la autocorrección no logró arreglarlo.
- **Muestreo con semillas fijas y caché:** `full` y `baseline_prover` comparten las mismas muestras de la etapa 0, así que su empate en la etapa 0 es por construcción. Sí son comparables en lo que añaden después.

## 5. Qué sigue (propuestas, sin ejecutar)

1. **Más muestras donde importa.** Pasar los 9 problemas no resueltos a pass@32 en la GPU (0 API, ~2-3 h). Es lo más barato y probablemente lo que más suba el resultado.
2. **Que los agentes y el RAG aporten algo sobre el demostrador.** Hoy sólo entran cuando la etapa 0 falla, y lo hacen con el modelo general.
   - Variante a probar: el plan y el boceto del Planificador, con sus lemas recuperados, **como contexto del prompt del demostrador** (estilo Draft-Sketch-Prove), en vez de un flujo aparte.
   - Es la forma en que el RAG puede influir de verdad en la prueba, y se mide igual: `full` frente a `baseline_prover`.
3. **Conjunto fuera de miniF2F** para medir sin el riesgo de contaminación.
4. **Ahorro:** desactivar el respaldo por API en los problemas que el demostrador no resolvió con 8 muestras. En eval-v3 costó US$0.06 y no aportó ningún problema.
