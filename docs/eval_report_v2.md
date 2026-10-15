# Evaluación eval-v2 — sistema por goal (`docs/PLAN_MEJORA_RAG.md`), reporte parcial

> 2026-10-15. **Estado final:**
> - Ronda 1 completa. Ronda 2 completa (opción B): `full` 2/15, `no_rag` 2/15, `baseline_auto` 2/15, a US$0.109.
> - Ronda 3 detenida a pedido (#111): 11/15 pares de `full` (2 resueltos), sin `no_rag`.
> - `baseline_auto` completa en los 60.
> - Comparación `full` contra `no_rag` en 30 problemas pareados: 8 frente a 9.
> - Siguiente paso: el rediseño con demostrador local (`docs/PLAN_REDISENO_PROVER.md`, eval-v3).
> El guardia de costo detuvo la cadena tras la ronda 1: la proyección para las rondas 2-4 (US$0.343) superaba el saldo de la key (US$0.289).
> **Falta tu decisión para seguir (sección 6).**
>
> Datos:
> - `data/eval/runs/eval-v2/` (resultados y trazas);
> - `data/eval/results/eval_v2_round1_check.json` (guardia);
> - `eval_v2_analysis.json` y `eval_v2_stats.json` (cifras de este reporte; se regeneran con `data/eval/results/eval_v2_analysis.py`).

---

## 1. Resumen

| Configuración | Ronda 1 (15 problemas) | IC95 Wilson | Los 60 problemas |
|---|---|---|---|
| **`full`** (sistema nuevo con RAG por goal) | **6/15** | 0.20-0.64 | — |
| `no_rag` (sistema nuevo sin premisas recuperadas) | 7/15 | 0.25-0.70 | — |
| `baseline_auto` (sólo tácticas de Lean, 0 LLM) | 5/15 | 0.15-0.58 | **11/60** (0.11-0.30) |
| *eval-v1 `full` (sistema viejo)* | *3/15* | 0.07-0.45 | *9/60* |
| *eval-v1 `no_rag`* | *4/15* | 0.11-0.52 | *9/60* |
| *eval-v1 línea base A (LLM escribe la prueba)* | — | — | *5/60* |

Lo que dicen los datos, de más a menos firme:

1. **El cambio de arquitectura es lo que más mueve el resultado.** Las tácticas de Lean solas, sin una sola llamada al LLM y a US$0, resuelven **11/60**, más que el sistema viejo completo (9/60). En la ronda 1, el sistema nuevo resuelve el doble que el viejo en los mismos problemas: 6-7/15 frente a 3-4/15. Esa comparación no es significativa (McNemar p = 0.375; 4 problemas sólo en v2, 1 sólo en v1) y está confundida por el cambio de proveedor del LLM y de índice.
2. **Las premisas recuperadas (el RAG nuevo) no muestran efecto medible.** `full` 6 frente a `no_rag` 7: un único problema distinto, a favor de `no_rag`. El kernel usó **1 premisa recuperada en 1 problema** de los 6 resueltos. La batería cerró más pasos con premisas (26 frente a 18), pero las premisas no aparecen en los términos de prueba: `grind` cerró esos pasos con lo que ya sabe.
3. **Los agentes aportan poco sobre la automatización en esta ronda.** `full` resuelve 1 problema más que `baseline_auto` (`algebra_sqineq_2unitcircatblt1`) y `no_rag`, 2.
4. **El flujo nuevo no es más barato en problemas difíciles.** US$0.0036 por par en `full` y US$0.0032 en `no_rag`, con 23 600 y 16 700 tokens de entrada: lo mismo que eval-v1. La estimación de US$0.0016 que di antes salía de pares fáciles.

Con 15 problemas pareados ninguna diferencia es significativa. Completar las 4 rondas daría n = 60 para `full` contra `no_rag`.

## 2. Configuración

- **Problemas:** eval-v1 (60), en 4 rondas intercaladas (`eval_v1_round{k}.jsonl` = problemas k, k+4, …). Cada ronda tiene 6 de álgebra, 6 de teoría de números y 3 de combinatoria.
- **Configuraciones:** `full`, `no_rag`, `baseline_auto` (DECISIONS #96). `rag_nl` quedó aislada a pedido del usuario (#106).
- **Modelo:** DeepSeek V4 Flash vía OpenRouter, con 4 hilos. El tope de precio se subió a {0.13, 0.30} durante la ronda 1 (8 proveedores, #107), porque con el anterior sólo calificaba DeepInfra, saturado.
- **Código:** `prompt_version: "3"`; índice `mathlib_full`; canal denso apagado; prior de competición de 778 lemas.
- **Cambios de infraestructura durante la ronda 1 (no afectan a los resultados):**
  - los 429 pasaron a reintentarse hasta 10 veces, porque no se cobran (#104);
  - hubo dos reinicios de la cadena: el cambio de reintentos y la salida de `rag_nl`;
  - los pares interrumpidos se repitieron completos.

## 3. Ronda 1 por problema

| Problema | Área | Dif. | `full` | `no_rag` | `baseline_auto` | v1 `full` | v1 `no_rag` |
|---|---|---|---|---|---|---|---|
| mathd_algebra_209 | álgebra | 1 | ✔ | ✔ | ✔ | – | – |
| algebra_sqineq_2unitcircatblt1 | álgebra | 1 | ✔ | ✔ | – | ✔ | ✔ |
| mathd_algebra_77 | álgebra | 1 | ✔ | ✔ | ✔ | – | – |
| amc12b_2020_p13 | álgebra | 2 | – | – | – | – | – |
| amc12a_2010_p11 | álgebra | 2 | – | – | – | – | – |
| imo_1965_p1 | álgebra | 3 | – | – | – | – | – |
| mathd_numbertheory_728 | t. números | 1 | ✔ | ✔ | ✔ | ✔ | ✔ |
| mathd_numbertheory_403 | t. números | 1 | ✔ | ✔ | ✔ | – | – |
| numbertheory_aneqprodakp4_anmsqrtanp1eq2 | t. números | 1 | – | – | – | – | – |
| amc12a_2020_p10 | t. números | 2 | – | – | – | – | – |
| amc12a_2016_p3 | t. números | 2 | – | – | – | ✔ | ✔ |
| imo_1977_p5 | t. números | 3 | – | – | – | – | – |
| mathd_algebra_170 | combinatoria | 1 | – | – | – | – | – |
| mathd_algebra_104 | combinatoria | 1 | ✔ | ✔ | ✔ | – | ✔ |
| amc12a_2002_p6 | combinatoria | 2 | – | ✔ | – | – | – |

Comparaciones pareadas (McNemar exacto, ronda 1):

| A | B | sólo A | sólo B | p |
|---|---|---|---|---|
| v2 `full` | v2 `no_rag` | 0 | 1 | 1.00 |
| v2 `full` | `baseline_auto` | 1 | 0 | 1.00 |
| v2 `full` | v1 `full` | 4 | 1 | 0.375 |
| v2 `no_rag` | v1 `no_rag` | 4 | 1 | 0.375 |

En los 60: `baseline_auto` contra v1 `full`, 7 sólo auto frente a 5 sólo v1 (p = 0.77); contra la línea base A de v1, 7 frente a 1 (p = 0.07).

## 4. Qué cerró cada paso y qué usó el kernel

- **Pasos cerrados por la batería CLOSE** (sin LLM; los cerrados por el bloque del propio modelo no se cuentan aquí):

  | Config | `grind` (con premisas en `full`) | `try?` | `nlinarith` | abiertos tras CLOSE (van a FILL) |
  |---|---|---|---|---|
  | `full` | 23 | 3 | — | 113 |
  | `no_rag` | 14 | 3 | 1 | 128 |

- **Premisas recuperadas que usa el término aceptado** (`Verdict.used_constants`, #101): **1** en `full`, en un problema. El texto de las pruebas nombra 16 por cada `grind +lax [...]`, pero el kernel no las usa. Los 9 pasos de diferencia en la batería se explican porque cada configuración recibe esqueletos distintos (muestras distintas del LLM), no por las premisas.
- **Llamadas FILL:** 19 en `full` y 21 en `no_rag`, en 15 problemas.
- **Por qué fallan los problemas difíciles:** sobre todo pruebas del modelo que Lean rechaza (`unsolved goals`, `type mismatch`) en pasos que ni la batería ni FILL logran cerrar. Ejemplo, `amc12a_2002_p6` en `full`: el modelo usa `Set.infinite_univ` para `{m | 0 < m}`, y en FILL escribe un bloque en una sola línea con viñetas `·` encadenadas por `;`. No se encontró ningún error de ensamblado: el archivo final contiene exactamente lo que se verificó paso a paso.

## 5. Costo

| Concepto | US$ |
|---|---|
| Smoke (2 corridas, 8 problemas) | 0.0145 |
| Ronda 1 (gasto de la key, incluidos ~US$0.008 de pares interrumpidos por los reinicios) | 0.1143 |
| `baseline_auto`, 60 problemas | 0.0000 |
| **Saldo de la key** | **0.2892** |

Por par: `full` US$0.0036 (23 600 tokens de entrada / 9 300 de salida); `no_rag` US$0.0032 (16 700 / 9 800). `full` lleva un 42 % más de tokens de entrada por las listas de premisas con firma que van a FILL. Ningún par superó el presupuesto de 20 llamadas (máximo 15) y no hubo pares en `error`.

**Proyección para las rondas 2-4:** 90 pares pagados × ~US$0.0034 ≈ **US$0.31-0.34**, frente a US$0.289 de saldo. Por eso el guardia cortó, como estaba previsto.

## 6. Opciones para seguir (decisión del usuario)

| Opción | Qué se obtiene | Costo | Tiempo |
|---|---|---|---|
| **A. Subir el límite de la key** en OpenRouter (≥ US$0.10 más) y correr las rondas 2-4 | `full` contra `no_rag` en los 60 problemas: la única medición con potencia mínima para el aporte del RAG | ~US$0.33 | 2-4 h |
| B. Correr sólo las rondas 2 y 3 con el saldo actual | 45 problemas pareados | ~US$0.21 | 1.5-3 h |
| C. Volver al tope barato (sólo DeepInfra) y correr las rondas 2-4 | 60 problemas; ~17 % más barato por token. La proyección (~US$0.28) queda justo en el límite del saldo y puede quedar incompleta | ~US$0.28 | 13-16 h (429) |
| D. Parar aquí | Ronda 1 + `baseline_auto` en los 60 | 0 | — |

**Recomendación: A**, y si no es posible, **B**. Con la ronda 1 sola no se puede afirmar nada del aporte del RAG: se necesitan los pares de las otras rondas.

## 7. Qué significa para el objetivo del RAG

- El RAG nuevo recupera mucho mejor (recall@16 por goal 0.327 frente a <0.01, Fase 3), pero **todavía no convierte eso en problemas resueltos**:
  - la automatización (`grind`) ya conoce los lemas genéricos que trae el prior (`sq_nonneg`, `mul_pos`…), así que dárselos no le añade nada;
  - FILL los recibe, pero el término final casi nunca los usa.
- Donde el RAG podría aportar es en **lemas específicos** (`Nat.Prime.eq_one_or_self_of_dvd`, `Real.log_rpow`) que ni la automatización ni el modelo conocen bien. El canal que los buscaba (el índice denso) fue el que no funcionó (recall 0.028).
- Siguiente mejora candidata, a medir antes de gastar: recuperar para los goals que quedan **abiertos tras la batería**, en lugar de para todos. Con el dato de qué falló (el error de Lean) se buscarían lemas específicos, y se pasarían a FILL con prioridad sobre los genéricos.
