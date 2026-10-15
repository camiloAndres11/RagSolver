# Rediseño dirigido — demostrador especializado local (2026-10-15)

> Origen: diagnóstico de eval-v2 (`docs/eval_report_v2.md`). Decisiones: `DECISIONS.md` #110-#114.
> Autorizado por el usuario: descargar y correr el modelo en su GPU (RX 9060 XT, 16 GB) y gastar el saldo de la key (~US$0.12).
> **Resultado (eval-v3, `docs/eval_report_v3.md`): 21/30 frente a 8/30 del sistema anterior (p = 0.0002).**

## 1. Qué fallaba (medido en eval-v2, rondas 1-2)

| Falla | Evidencia | Arreglo |
|---|---|---|
| El modelo general (DeepSeek V4 Flash, sin razonamiento) no demuestra los pasos difíciles | Dificultad 1: 8/16; dificultad 2-3: 0-1/14. En el último intento quedaban abiertos 5.2 de 5.7 pasos tras la batería | **Demostrador especializado local**: Goedel-Prover-V2-8B (Apache-2.0, 84.6 % pass@32 en miniF2F según su paper), k muestras y la primera que verifica gana |
| Fallos de formato | 12 de 43 fallos fueron `llm_output_invalid` (Crítico 6, FILL 4) | El Crítico ilegible pasa a reintento con los errores de Lean; FILL ilegible pasa a "sin propuestas". Ninguno mata ya el problema (#110) |
| Las premisas del RAG no llegaban a las pruebas | El kernel usó 1 premisa recuperada en eval-v2 | Nuevo rol del RAG donde el demostrador lo necesita de verdad (§3) |

## 2. Flujo

1. **Etapa 0, sin API.** El demostrador recibe el teorema completo en su formato de prompt nativo. Genera hasta `k_root` = 8 muestras en lotes de 4, con salida temprana.
   - De cada muestra se toman **sólo las tácticas** del teorema pedido, que se empalman en el enunciado del problema: el modelo no puede debilitarlo.
   - Cada candidato pasa por `LeanChecker.check` (validador, kernel y auditoría de axiomas).
2. **Autocorrección.** Si ninguna muestra verifica, hay una ronda de corrección sobre las 2 mejores, con los errores de Lean.
3. **Si la etapa 0 no resuelve:** sigue el flujo por goal ya existente (plan → esqueleto → CLOSE → FILL). En CLOSE, los pasos que la batería no cierra se le dan al demostrador como **lemas autónomos**, hasta `max_goals` = 2 por intento con `k_goal` = 2 muestras (receta de DeepSeek-Prover-V2). Cada bloque se acepta sólo si cierra el paso en su contexto, con `rename_i` para los nombres inaccesibles.
4. **Costo de API:** sólo para los problemas que la etapa 0 no resuelve.

## 3. Rol del RAG en el rediseño

El demostrador se entrenó con Lean 4.9 y su Mathlib; el verificador usa v4.28, así que usa nombres renombrados o borrados.

En la autocorrección, ante `unknown identifier/constant X`, el RAG busca declaraciones parecidas (índice, modo `error`) y **las valida contra el entorno de Lean** (`#check`). Al demostrador sólo le llegan nombres que existen, con su firma. Esas sugerencias se registran como citas `M*`, y `used_in_proof` mide si el kernel las usó.

`no_rag` y `baseline_prover` corren la autocorrección sólo con los errores de Lean.

## 4. GPU (RX 9060 XT, 16 GB, Vulkan, `tools/llama-vulkan/llama-server.exe`, lanzado desde WSL)

| Configuración | 1 muestra | En paralelo |
|---|---|---|
| Q8_0, 4 slots × 16k, KV q8_0, flash attention | 36 tok/s | 69 tok/s con 4 |
| **Q6_K, 8 slots × 12k, KV q8_0, flash attention** (la elegida) | **45 tok/s** | 67 tok/s con 8 |

La GPU se satura en unos 70 tok/s agregados. Más slots no suben el total, pero dejan correr dos problemas a la vez sin cola.

- El servidor escucha sólo en la red virtual WSL↔Windows (172.31.112.1:8082), no en la LAN.
- Muestras en caché en disco (`data/eval/prover_cache.jsonl`): `baseline_prover` y la etapa 0 de `full` comparten exactamente las mismas muestras. No se generan dos veces y la comparación queda pareada.

## 5. Evaluación (eval-v3)

- **Problemas:** las rondas 1-2 de eval-v1 (30 problemas), pareados con eval-v2 (`full`, `no_rag`, `baseline_auto`).
- **Configuraciones:** `baseline_prover` (0 API) y `full` (rediseño completo).
- **Guardia:** `round_check.py` después de cada ronda.
- **Fuera de alcance por saldo:** `no_rag` del rediseño.

**Criterios:**
- (a) `full` (v3) > `full` (v2) en los 30 pareados;
- (b) cuántos problemas resuelve la etapa 0 frente a los agentes;
- (c) cuántas sugerencias de nombres del RAG usa el kernel.
