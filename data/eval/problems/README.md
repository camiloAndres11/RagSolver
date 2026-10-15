# Conjunto de evaluación — RagSolver (propuesta §8.1, objetivo 5)

| Archivo | Contenido | Uso |
|---|---|---|
| `dev_trivial.jsonl` | 5 teoremas triviales de referencia (definidos por el equipo; la propuesta los cita como "Sección 6.5") | pruebas de integración del pipeline (`DECISIONS.md` #37) |
| `dev_minif2f.jsonl` | 8 problemas fáciles de miniF2F-v2s (split `valid`) | desarrollo inicial de prompts |
| `dev_all.jsonl` | los 13 anteriores | ablaciones preliminares (`docs/eval_report.md`, n=13) |
| **`eval_v1.jsonl`** | **60 problemas de miniF2F-v2s**: 24 desarrollo (split `valid`) + 36 prueba (split `test`), estratificados en 3 áreas × 3 niveles | experimento principal (5 configuraciones de ablación) |

## Curación de `eval_v1.jsonl` (`scripts/build_eval_set.py`, semilla 13)

- **Fuente**: miniF2F-v2s (`roozbeh-yz/miniF2F_v2`, Apache 2.0): enunciados formales revisados para coincidir con los informales (evita el riesgo de mal-formalización de la propuesta §11). El enunciado formal se usa **tal cual** con `:= by sorry`; el LLM sólo produce las tácticas, nunca el enunciado.
- **Partición estricta**: `dev` = split `valid` de miniF2F (para ajustar prompts/umbrales); `test` = split `test` (sólo para el reporte final). Nunca se ajusta nada sobre `test`.
- **Niveles de dificultad**: 1 = `mathd_*`/`algebra_*`/`numbertheory_*`/`induction_*` (ejercicios de libro), 2 = AMC 12 / AIME, 3 = IMO. Es el proxy de dificultad estándar de miniF2F (ver tabla 1 del README de miniF2F-v2).
- **Áreas**: heurística sobre nombre + enunciado (`number_theory`: `Nat.Prime`, `% n`, `digits`, `gcd`…; `combinatorics`: `Finset.card`, `choose`, conteos; `algebra`: resto con `Real`, funciones, polinomios). miniF2F tiene poca combinatoria formalizable (12 problemas, 1 de nivel 3), por eso las cuotas por celda no son iguales. Revisable a mano: la etiqueta `area` está en cada fila.
- Cuotas por (área, nivel) → (dev, test): álgebra (4,6)/(3,5)/(2,4); teoría de números igual; combinatoria (3,4)/(3,2)/(0,1). Total 60 (proyecto en equipo; individual: usar sólo `dev` + los 12 primeros de `test` = 36 ≥ 30).
- PutnamBench (secundario en la propuesta) no se incluyó en v1: requiere un proyecto Lean propio con su versión de Mathlib; queda para una v2 si el presupuesto de LLM lo permite.

## Formato

Una línea JSON por problema (`ragsolver.agents.schemas.Problem`): `problem_id`, `source`, `split`, `area`, `difficulty`, `informal_statement`, `formal_statement`, `header` (`open BigOperators Real Nat Topology Rat`, cabecera estándar de miniF2F).
