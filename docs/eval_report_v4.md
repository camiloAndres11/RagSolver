# Evaluación eval-v4 — fuera de miniF2F y los problemas difíciles

> 2026-10-22. Decisiones: `docs/DECISIONS.md` #116-#129. Configuraciones: `docs/reproduce.md` §4b.
> Problemas:
> - `pdf_set`: 32 problemas del PDF de olimpiadas, formalizados a mano (no se versiona, SEC-2);
> - `hard7`: los 7 demostrables de los 9 que eval-v3 no resolvió (`eval_v3_unsolved_provable.jsonl`).
>
> Datos:
> - `data/eval/runs/eval-v4-{pdf,unsolved}/` y `eval-v4b-{pdf,unsolved}/`;
> - `data/eval/results/eval_v4_analysis.json`, que se regenera con `eval_v4_analysis.py`.
>
> **Estado:** completo. eval-v4b, que repite SKETCH con sus dos arreglos (#128) y añade el control de `dsp8`, terminó el 2026-10-22 a las 09:02.

---

## 1. ¿El demostrador generaliza fuera de miniF2F?

Sí. `baseline_prover` (Goedel-Prover-V2-8B, 8 muestras + 1 corrección, 0 API) resuelve:
- **21/32 en `pdf_set`** (66 %, IC95 Wilson 0.48-0.80);
- 21/30 en miniF2F (eval-v3).

El resultado de eval-v3 no se explica por contaminación del benchmark. Una salvedad: las versiones informales de estos clásicos pueden estar en internet, aunque no se conoce una formalización en Lean.

## 2. Los difíciles: qué suma algo sobre el demostrador

Dos de los 9 problemas son **falsos** tal como están en miniF2F-v2s: `mathd_algebra_437` y `numbertheory_notequiv2i2jasqbsqdiv8`. Sus negaciones verifican en Lean (`data/eval/results/false_statements/`). Ningún sistema puede demostrarlos.

| Config (hard7) | Resueltos | API | Qué resolvió |
|---|---|---|---|
| `baseline_prover16` (control: 16 muestras) | 0/7 | US$0 | — |
| `baseline_prover32` | 0/4* | US$0 | — |
| `dsp` (plan + prueba informal en el prompt) | 0/7 | US$0.017 | — |
| `repair` (casi-pruebas → pasos `have`) | 0/2* | US$0 | — |
| `sketch` v4 (esqueleto Lean del modelo con razonamiento) | **1/7** | US$0.038 | `amc12b_2002_p3` |
| `rag_proofs` (pruebas verificadas de problemas parecidos) | **1/7** | **US$0** | `amc12b_2002_p3` |
| `sketch` v4b (con los arreglos de #128) | **1/7** | US$0.057 | `amc12b_2002_p3` (por REPAIR) |

\* Corridas cortadas antes de cubrir los 7: 32 muestras tardaban ~65 min por problema sin resolver ninguno (#123), y REPAIR solo actúa si hay casi-pruebas.

**`amc12b_2002_p3` es el primer problema que el sistema resuelve y el demostrador solo no.** Lo resolvieron dos caminos independientes:
- SKETCH: el modelo escribió el esqueleto y el demostrador cerró los pasos;
- `rag_proofs`: las pruebas de problemas parecidos de Lean Workbook iban en el prompt. Sus similitudes fueron 0.69-0.80, ninguna era el mismo problema.

Las dos pruebas pasan el verificador completo: enunciado intacto, kernel y axiomas estándar.

En eval-v4b `amc12b_2002_p3` cayó una tercera vez, ahora por REPAIR, la etapa gratis, en 74 s y antes de llamar al modelo. Esta vez sí había una casi-prueba que reparar: los arreglos de #128 dejaron elaborar sus pasos. Los otros 6 difíciles siguen sin resolverse con todas las variantes.

Con n = 7, 1 de 7 frente a 0 de 7 no es significativo (McNemar p = 1.0). Es una señal, no un resultado.

## 3. `pdf_set`: los 11 fallos del demostrador

| Config (pdf_set) | Resueltos | Frente al control | API |
|---|---|---|---|
| `baseline_prover` (control) | 21/32 | — | US$0 |
| `baseline_prover16` (16 muestras) | 21/32 | los mismos 21 | US$0 |
| `dsp8` | 21/32 | +1 (`pdf_p33`, `prover_draft`) −1 (`pdf_p58`) | US$0.031 |
| `rag_proofs` | 20/32 | −1 (`pdf_p58`) | US$0 |
| `sketch` v4 | 20/32 | −1 (`pdf_p58`) | US$0.066 |
| **`sketch` v4b** | **22/32** (IC95 0.51-0.82) | **+2** (`pdf_p33` por REPAIR, `pdf_p41` por SKETCH) −1 (`pdf_p58`) | US$0.107 |

**El −1 de `pdf_p58` no viene de la etapa nueva.** `baseline_prover` lo resuelve en la ronda de corrección. Las configs con RAG añaden a esa ronda sugerencias de nombres de lemas, así que el prompt cambia y las muestras también, y esas no lo resolvieron. Es ruido de muestreo de la ronda de corrección, no un efecto de EXAMPLES ni de SKETCH. Aun así, es la segunda vez que las sugerencias de nombres no ayudan: en eval-v3 el kernel no usó ninguna.

**`pdf_p33` es mérito del borrador, no de más muestras.** El control con 16 muestras no lo resuelve.

**`sketch` v4b es la primera configuración que supera al demostrador solo en `pdf_set`: 22 frente a 21.**
- `pdf_p41` lo resolvió SKETCH, en 40 min: el modelo escribió el esqueleto y el demostrador cerró los pasos. No lo resuelve ninguna otra configuración, ni `sketch` antes de los arreglos.
- `pdf_p33` lo resolvió REPAIR, en 2 min.
- Las dos pruebas pasan el verificador completo con axiomas estándar. Sus citas son solo de Mathlib: nada del corpus informal, que es el mismo PDF.

Con 2 a favor y 1 en contra, McNemar da p = 1.0: la diferencia no es significativa.

## 4. Por qué SKETCH rindió poco en eval-v4 (y qué se arregló)

Los esqueletos eran buenos. Por ejemplo, el invariante aₙ₊₁ = (aₙ − 2)² en `numbertheory_aneqprod…` y el periodo 12 con suma 60 en `amc12a_…`. Fallaron dos defectos del sistema (#128):

1. **El modelo se quedaba sin presupuesto.** 32 de 38 llamadas se cortaron en 16 000 tokens antes de escribir el bloque Lean. Ahora SKETCH tiene su propio tope de 40 000 tokens.
2. **Pasos "stale" sin probar.** Un bloque de varias líneas no llevaba límite de heartbeats, agotaba el timeout de reloj y reiniciaba el REPL. Con 3 hilos compartiendo un REPL, todos los pasos pendientes quedaban inválidos: en un problema, 9 de 11. Se corrigió en tres puntos:
   - el bloque va dentro de `·` con `set_option maxHeartbeats N in`;
   - CLOSE vuelve a elaborar el esqueleto si el REPL se reinició y reintenta el paso;
   - todos los pasos llevan el límite.

   Antes de eval-v4b se comprueba con Lean real (`tests/test_goal_mode_slow.py`).

**Resultado de eval-v4b:**
- Las llamadas cortadas bajaron de 32/38 a 12/34.
- No hubo ningún paso "stale".
- SKETCH pasó de 0 a 2 problemas del PDF fuera del alcance del demostrador solo (uno por SKETCH, otro por su REPAIR).
- En los difíciles se quedó en 1/7.

Que un 35 % de las llamadas siga llegando al tope de 40 000 tokens indica que el modelo razona de más en los problemas largos.

## 5. Costo y tiempo

- **API:** US$0.33 en total entre eval-v4 y eval-v4b, contando DRAFT, SKETCH y los fallos.
- **GPU:**
  - unos 14 min por problema del PDF con `baseline_prover`;
  - en los difíciles, 30-40 min por problema para cada config, porque agotan todas las muestras.
- **Ahorro:** las configs comparten la caché de muestras de la etapa 0 (`data/eval/prover_cache.jsonl`), así que solo pagan los fallos.

## 6. Validez y limitaciones

- **Muestras muy pequeñas.** n = 7 y 11 fallos; ninguna diferencia es significativa.
- **Formalización no revisada.** `pdf_set` lo formalizó el asistente y nadie lo revisó; las respuestas se comprobaron por fuerza bruta.
- **La caché no es aleatoriedad nueva.** Comparar con el control mide la etapa añadida, no una muestra independiente.
- **El efecto de un problema está dentro del ruido.** `pdf_p58` muestra que un prompt de corrección distinto basta para ganar o perder uno.

## 7. Lectura

El demostrador especializado decide el resultado y generaliza (21/32 fuera de miniF2F). Lo único que le añadió algo en los difíciles fue darle **estructura**:
- un esqueleto de pasos del modelo que razona (SKETCH);
- pruebas verificadas de problemas parecidos (RAG de pruebas, gratis).

Más muestras no sumaron (32 en los difíciles; 16 en el PDF, con los mismos 21). El plan informal en el prompt (DRAFT) sumó un problema del PDF, el mismo que REPAIR saca gratis después de los arreglos.

Juntando lo que aportó cada etapa sobre el demostrador solo:
- `pdf_set`: 21 → 23 de 32;
- los difíciles: 0 → 1 de 7.

Una configuración que encadene etapa 0 → `rag_proofs` → `sketch` es lo que falta medir como un solo sistema.

Es el papel del RAG que el proyecto buscaba: recuperar **cómo se demostró algo parecido**, no nombres de lemas que el demostrador ya conoce.
