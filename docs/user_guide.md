# Guía de usuario

Tres formas de usar RagSolver: CLI, Python y demo web. Requisitos en `install.md`.

## 1. Buscar (0 llamadas LLM)

```bash
ragsolver search "prove that 2^2001 + 3^2001 is divisible by 7" --corpus informal --mode exemplar --k 3
ragsolver search "gcd is commutative" --corpus mathlib --k 8
ragsolver search "unknown identifier 'Nat.Prime.two_le'" --mode error        # identificador exacto en el top-1
ragsolver search "gcd commutes" "greatest common divisor symmetric" --corpus mathlib   # varias consultas -> RRF
```

Modos: `nl` (subobjetivo en lenguaje natural), `formal` (goal/enunciado Lean), `error` (mensaje de error de Lean), `exemplar` (enunciado completo → problemas resueltos). Cada hit trae su cita (`M1`, `I1`…), localizador (`decl_name`/`docs_url` o `doc_id`/página/figuras) y, para el corpus informal, las figuras adjuntas con descripción.

## 2. Resolver un problema

Formato de problema (`Problem`): enunciado informal + enunciado formal Lean 4 terminado en `:= by sorry` (el sistema **nunca** reescribe el enunciado; sólo produce las tácticas).

```bash
python -m ragsolver.orchestrator.run --problems data/eval/problems/dev_trivial.jsonl --configs full
python -m ragsolver.orchestrator.run --problems mis_problemas.jsonl --configs full,no_rag --run-id mi-corrida
python -m ragsolver.orchestrator.run --problems mis_problemas.jsonl --configs full --run-id mi-corrida --workers 6   # varios problemas a la vez
```

Con el mismo `--run-id` la corrida se reanuda (salta los pares ya resueltos). `RAGSOLVER_MATHLIB_TABLE=mathlib_full` usa el índice completo de Mathlib si está construido (`scripts/embed_full_mathlib.sh`).

Salida en `data/eval/runs/<run_id>/`: `results.jsonl` (un `SolutionReport` por problema × configuración: estado, intentos, llamadas LLM, tokens, código Lean aceptado, citas clasificadas en verificadas / usadas en el boceto / consultadas, taxonomía de errores), `summary.json` (pass@1 con IC de Wilson, pass por intento, medias con IC bootstrap, precisión de uso de lemas) y `traces/` (un JSONL por (config, problema) con un evento por agente, intento y búsqueda del retriever: modelo, tokens, latencia y **decisión** tomada).

Antes de resolver, levanta el demostrador local en la GPU: `bash scripts/prover_server.sh start` (`docs/install.md` §7). Sin él el sistema corre igual, pero sólo con los agentes por API (resultados mucho más bajos).

Configuraciones (`configs/agents.yaml`):
- `full`: el demostrador local (etapa 0, 0 API) y, si `prover.api_fallback: true`, los agentes por API cuando no resuelve. Por defecto el respaldo está apagado: en eval-v3 costó y no resolvió nada.
- `dsp`: etapa 0 + **DRAFT**: si el demostrador no resuelve, el modelo de la API (`llm_draft`, con razonamiento) escribe plan y prueba informal, y el demostrador los recibe en su prompt con lemas del RAG (2 llamadas por problema).
- `baseline_prover` (0 API, sólo el demostrador, 8 muestras), `baseline_prover32` (32 muestras: el control de `dsp`), `baseline_auto` (0 LLM, sólo tácticas de Lean), `no_rag`, `no_critic`, `rag_nl`, `baseline_a` (Lean directo, 1 llamada), `baseline_b` (razonamiento sin verificación).

Presupuesto: `llm_budget: 20` llamadas por problema (RNF-3); `k_max: 3` reintentos de los agentes.

Desde Python:

```python
from ragsolver.agents.agents import Agents, load_agents_config
from ragsolver.agents.schemas import Problem
from ragsolver.lean.verify import LeanChecker
from ragsolver.llm import LLM
from ragsolver.orchestrator.graph import Orchestrator
from ragsolver.orchestrator.run import drafter_agents, effective_config
from ragsolver.prover import local_prover
from ragsolver.rag.retrieve import Retriever

base = load_agents_config()
prover = local_prover(base["prover"])   # None si el servidor de la GPU no responde
orch = Orchestrator(Agents(LLM(section="llm"), base), Retriever(), LeanChecker(imports=base["lean"]["imports"]), prover, drafter_agents(base, "data/eval/traces"))
report = orch.solve(Problem(problem_id="p1", informal_statement="n + 0 = n", formal_statement="theorem p1 (n : ℕ) : n + 0 = n := by sorry"), effective_config(base, "full"), run_id="p1")
print(report.status, report.solved_by, report.lean_code, [c.label for c in report.citations if c.used_in_proof])
```

## 3. Demo web

```bash
bash scripts/demo_up.sh   # demostrador en la GPU + demo en http://127.0.0.1:7860 — pestañas "Buscar" y "Resolver"
```

La demo precalienta Lean (`import Mathlib`, ~5 min) y el embedder en segundo plano: lánzala unos minutos antes de usarla. La pestaña "Resolver" trae cuatro problemas de eval-v3 como ejemplos; sus muestras del demostrador están en la caché de disco, así que salen en segundos, y la verificación del kernel corre en vivo. Apagar el demostrador al terminar: `bash scripts/prover_server.sh stop`.

## 4. Ingerir un PDF nuevo

1. `mineru parse` en modo local → `data/parsed/<doc_id>/mineru/*.json`.
2. `ragsolver ingest --pdf <archivo>` (huecos del layout, QA de fórmulas, cards, figuras, resúmenes; llamadas cacheadas).
3. Embeber e indexar (`docs/reproduce.md` §2).

Limitación actual: el parser estructural está calibrado para colecciones "Problemario / Soluciones" con ítems numerados; para papers y apuntes está el camino `paper_section` (`python -m ragsolver.rag.ingest_informal --pdf <archivo> --sections all`, DECISIONS #49).

## 5. Cambiar de proveedor LLM

`configs/versions.yaml` → `llm.provider: openai_compatible`, `base_url`, `api_key_env`, `model` (p. ej. OpenRouter; también sirve un servidor local compatible con OpenAI, como `llama-server` u Ollama, con `base_url: http://<host>:<puerto>/v1`). La cadena `fallback_models` cubre cuotas agotadas; en OpenRouter, `provider_routing` fija un tope de precio (US$/M tokens) y el orden entre proveedores.

## Publicación en Hugging Face (Objetivo 7)

```bash
HF_TOKEN=... python scripts/publish_hf.py --repo <usuario>/ragsolver-index --space <usuario>/ragsolver      # índices, evaluación, corridas y caché del demostrador
HF_TOKEN=... python scripts/publish_space.py --repo <usuario>/ragsolver --dataset <usuario>/ragsolver-index --model   # código, docs, notebook, LICENSE, CITATION
```

Ambos aceptan `--dry-run` para ver qué se sube. Publicado: [código](https://huggingface.co/CamiloS1111/ragsolver) y [datos](https://huggingface.co/datasets/CamiloS1111/ragsolver-index). Sin `--model` se sube lo mismo como Space de Gradio, pero desde 2026 Hugging Face pide suscripción PRO para alojarlo en `cpu-basic` (HTTP 402); con `--model` la demo se corre en local (`bash scripts/demo_up.sh`). El corpus informal (PDF de olimpiadas) **no** se publica por licencia; el Space ofrece la búsqueda sobre Mathlib y deja el modo *Resolver* (agentes + Lean) para instalaciones locales.

`publish_hf.py` sube `data/eval/runs/` (resultados de las corridas) desde una copia saneada cuando el documento informal no es redistribuible — vacía el `snippet` de las citas del corpus informal (`Citation.snippet` puede llevar hasta 300 caracteres del PDF) y verifica con `scripts/check_no_leaked_content.py` que no quede texto del documento en nada de lo que se sube, incluido `--dry-run`; si encuentra algo, el publish se aborta. La misma comprobación conviene correrla antes del primer commit: `python scripts/check_no_leaked_content.py` (0 coincidencias esperadas contra `data/corpus/informal.jsonl`).
