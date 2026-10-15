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

Configuraciones (`configs/agents.yaml`): `full`, `no_rag`, `no_critic`, `baseline_a` (Lean directo, 1 llamada), `baseline_b` (razonamiento sin verificación). Presupuesto: `llm_budget: 20` llamadas por problema (RNF-3); `k_max: 3` reintentos.

Desde Python:

```python
from ragsolver.agents.agents import Agents, load_agents_config
from ragsolver.agents.schemas import Problem
from ragsolver.lean.verify import LeanChecker
from ragsolver.llm import LLM
from ragsolver.orchestrator.graph import Orchestrator
from ragsolver.orchestrator.run import effective_config
from ragsolver.rag.retrieve import Retriever

base = load_agents_config()
orch = Orchestrator(Agents(LLM(section="llm"), base), Retriever(), LeanChecker(imports=base["lean"]["imports"]))
report = orch.solve(Problem(problem_id="p1", informal_statement="n + 0 = n", formal_statement="theorem p1 (n : ℕ) : n + 0 = n := by sorry"), effective_config(base, "full"), run_id="p1")
print(report.status, report.lean_code, [c.label for c in report.citations if c.used_in_proof])
```

## 3. Demo web

```bash
python demo/app.py   # http://127.0.0.1:7860 — pestañas "Buscar" y "Resolver"
```

## 4. Ingerir un PDF nuevo

1. `mineru parse` en modo local → `data/parsed/<doc_id>/mineru/*.json`.
2. `ragsolver ingest --pdf <archivo>` (huecos del layout, QA de fórmulas, cards, figuras, resúmenes; llamadas cacheadas).
3. Embeber e indexar (`docs/reproduce.md` §2).

Limitación actual: el parser estructural está calibrado para colecciones "Problemario / Soluciones" con ítems numerados; para papers y apuntes está el camino `paper_section` (`python -m ragsolver.rag.ingest_informal --pdf <archivo> --sections all`, DECISIONS #49).

## 5. Cambiar de proveedor LLM

`configs/versions.yaml` → `llm.provider: openai_compatible`, `base_url`, `api_key_env`, `model` (p. ej. OpenRouter; también sirve un servidor local compatible con OpenAI, como `llama-server` u Ollama, con `base_url: http://<host>:<puerto>/v1`). La cadena `fallback_models` cubre cuotas agotadas; en OpenRouter, `provider_routing` fija un tope de precio (US$/M tokens) y el orden entre proveedores.

## Publicación en Hugging Face (Objetivo 7)

```bash
HF_TOKEN=... python scripts/publish_hf.py --repo <usuario>/ragsolver-index      # índice (muestra + completo con ANN), evaluación y corridas
HF_TOKEN=... python scripts/publish_space.py --repo <usuario>/ragsolver --dataset <usuario>/ragsolver-index   # Space de Gradio
```

Ambos aceptan `--dry-run` para ver qué se sube. El corpus informal (PDF de olimpiadas) **no** se publica por licencia; el Space ofrece la búsqueda sobre Mathlib y deja el modo *Resolver* (agentes + Lean) para instalaciones locales.

`publish_hf.py` sube `data/eval/runs/` (resultados de las corridas) desde una copia saneada cuando el documento informal no es redistribuible — vacía el `snippet` de las citas del corpus informal (`Citation.snippet` puede llevar hasta 300 caracteres del PDF) y verifica con `scripts/check_no_leaked_content.py` que no quede texto del documento en nada de lo que se sube, incluido `--dry-run`; si encuentra algo, el publish se aborta. La misma comprobación conviene correrla antes del primer commit: `python scripts/check_no_leaked_content.py` (0 coincidencias esperadas contra `data/corpus/informal.jsonl`).
