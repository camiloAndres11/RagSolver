# Reproducir el índice y las evaluaciones

Todo con las versiones fijadas en `configs/versions.yaml`. Tiempos medidos en CPU (Ryzen 5 9600X, 7.5 GB RAM, repo en `/mnt/c`).

## 1. Corpus A — Mathlib

```bash
# 1. Corpus JSONL de LeanSearch v2 (Apache 2.0, 332 MB) -> data/corpus/_raw/
huggingface-cli download FrenzyMath/lsv2-mathlib-v4.28.0-rc1-jsonl --repo-type dataset --local-dir data/corpus/_raw
# 2. Normalizar (310 579 -> 278 160 declaraciones, 27 s)
python -m ragsolver.rag.ingest_mathlib
# 3a. Muestra de evaluación (gold MathlibQR/MPR + premisas miniF2F + 3000 distractores = 3 903 filas)
python scripts/build_eval_sample.py --n-distractors 3000 --extra-gold data/eval/premises_gold.jsonl
python -m ragsolver.rag.embed_corpus --in data/corpus/mathlib_sample.jsonl --out data/corpus/mathlib_sample_embedded.jsonl   # ~13 min
python -m ragsolver.rag.index --in data/corpus/mathlib_sample_embedded.jsonl --table mathlib
# 3b. Corpus completo (278k) en la GPU AMD vía llama.cpp/Vulkan (4.5 h; ~15 h en CPU), tabla `mathlib_full` e índice ANN
bash scripts/embed_full_mathlib.sh      # requiere llama-server.exe (Windows) sirviendo el GGUF del embedder, ver el encabezado del script
python -m ragsolver.rag.index --table mathlib_full --ann-only
```

## 2. Corpus B — PDF de ejemplo

```bash
# MinerU en modo local sobre el PDF -> data/parsed/<doc_id>/mineru/*.json (6m47s en CPU)
python -m ragsolver.rag.ingest_informal --pdf "problemas resueltos olimpiadas.pdf"     # ~230 llamadas VLM (cacheadas en data/parsed/<doc_id>/enrich/)
python -m ragsolver.rag.embed_corpus --in data/corpus/informal.jsonl --out data/corpus/informal_embedded.jsonl   # 215 s
python -m ragsolver.rag.index --in data/corpus/informal_embedded.jsonl --table informal
python -m ragsolver.rag.index --in data/corpus/figures.jsonl --table figures
python scripts/qa_ingest_report.py --doc-id edd3436ca148        # docs/qa_ingest_<doc_id>.md
```

## 3. Evaluación del retriever

```bash
python -m ragsolver.rag.evaluate --variants denso,bm25,hibrido            # MathlibQR, 946 consultas, ~6 min
python -m ragsolver.rag.evaluate --sample 100                              # + rerank sobre 100 al azar (semilla 13), ~25 min
python scripts/build_premise_gold.py && python scripts/eval_premises.py    # premisas gold miniF2F (DeepSeek-Prover-V2)
python scripts/eval_informal_gold.py                                       # 30 consultas, 15 con figura (~35 min por el rerank en CPU)
python scripts/contamination.py                                            # coseno card <-> miniF2F-v2s
```

Resultados en `data/eval/results/*.json`; los de referencia están citados en `DECISIONS.md` (#32) y `eval_report.md`.

## 4. Pipeline de agentes y ablaciones

```bash
python -m ragsolver.orchestrator.run --problems data/eval/problems/dev_trivial.jsonl --configs full
python -m ragsolver.orchestrator.run --problems data/eval/problems/dev_all.jsonl --configs full,no_rag,no_critic,baseline_a,baseline_b --run-id f5-ablations
python scripts/eval_report.py --run-id f5-ablations --out docs/eval_report_dev.md   # ablación de desarrollo (n=13)
bash scripts/run_chain.sh                                                  # TODO el experimento principal, reanudable: eval_v1 → índice completo → informe (§6.3)
bash scripts/run_eval_v1.sh                                                # (por partes) sólo eval_v1 × 5 configs, 6 hilos, reanudable
python scripts/eval_report.py --run-id eval-v1 --problems data/eval/problems/eval_v1.jsonl   # docs/eval_report.md
python scripts/fill_final_report.py --run-id eval-v1 --full-index-run-id ""                # tablas de §6.3 de docs/final_report.md
RAGSOLVER_MATHLIB_TABLE=mathlib_full python -m ragsolver.rag.evaluate --table mathlib_full --sample 100   # MathlibQR sobre el índice completo
RAGSOLVER_MATHLIB_TABLE=mathlib_full python scripts/eval_premises.py --out data/eval/results/fase5_premises_full.json
```

Índice ANN del corpus completo: `python -m ragsolver.rag.index --table mathlib_full --ann-only` (IVF_SQ, 67 s) y `python scripts/ann_recall.py` para medir su acuerdo con la búsqueda exacta. `--workers N` resuelve N pares (config, problema) en paralelo (hilos; el REPL de Lean se serializa). `RAGSOLVER_MATHLIB_TABLE=mathlib_full` hace que el corpus `mathlib` se sirva desde la tabla completa (278k) en vez de la muestra. El LLM de los agentes se fija en `configs/versions.yaml: llm` (`reasoning_effort: "off"` para DeepSeek V4 Flash: 24 s por llamada vs 76-92 s razonando; DECISIONS #50).

Cada corrida deja `data/eval/runs/<run_id>/results.jsonl` (un `SolutionReport` por problema×config), `summary.json` y `traces/`: un JSONL por (config, problema) con los eventos de los agentes y de cada búsqueda del retriever del mismo par, nombrado `<run_id>:<config>:<problem_id>.jsonl` (TRZ-1, corregido). `scripts/run_night.sh` encadena todo lo anterior de forma desacoplada (`nohup`).

## 4b. Iteración de octubre: RAG por goal, demostrador local (eval-v2 → eval-v4)

Preparación (una vez):

```bash
python scripts/build_premise_prior.py                                         # prior de competición: 778 lemas de Lean Workbook (MIT), validados con Lean (DECISIONS #96)
python scripts/build_premise_gold_goals.py && python scripts/eval_premises_goals.py   # recall de premisas por goal (informe §6.4)
bash scripts/prover_server.sh start                                           # Goedel-Prover-V2-8B Q6_K en la GPU (install.md §7); `stop` al terminar
```

Corridas (todas reanudables con el mismo `--run-id`; `--workers 3` con el demostrador):

```bash
bash data/eval/results/eval_v2_chain.sh     # eval-v2: goal mode por API, rondas de 15 con guardia de costo (se usaron las rondas 1-2)
bash data/eval/results/eval_v3_chain.sh     # eval-v3: baseline_prover,full en las rondas 1-2 (30 problemas)
python data/eval/results/eval_v3_analysis.py

# eval-v4: fuera de miniF2F y cómo usar el presupuesto extra del demostrador
python -m ragsolver.orchestrator.run --problems data/eval/problems/pdf_set.jsonl --configs baseline_prover --run-id eval-v4-pdf --workers 3
python -m ragsolver.orchestrator.run --problems data/eval/problems/eval_v3_unsolved_provable.jsonl --configs sketch,dsp,baseline_prover16 --run-id eval-v4-unsolved --workers 3
RAGSOLVER_EMBED_BACKEND=llama python -m ragsolver.rag.proofs build          # índice `proofs` (Lean Workbook) con el servidor de embeddings de §1 y el demostrador apagado
python -m ragsolver.rag.proofs preview --problems data/eval/problems/pdf_set.jsonl   # qué recupera cada problema (revisión de contaminación)
python -m ragsolver.orchestrator.run --problems data/eval/problems/eval_v3_unsolved_provable.jsonl --configs rag_proofs --run-id eval-v4-unsolved --workers 3
python -m ragsolver.orchestrator.run --problems data/eval/problems/pdf_set.jsonl --configs rag_proofs,sketch,dsp8,baseline_prover16 --run-id eval-v4-pdf --workers 3
(cd lean_project && lake env lean ../data/eval/results/false_statements/FalseStatements.lean)   # 2 enunciados de miniF2F-v2s son falsos (#126)
```

`data/eval/results/eval_v4_chain.sh` y `eval_v4_part2.sh` encadenan lo mismo con el servidor y el índice de por medio. WSL se reinició a mitad de la parte 2; `eval_v4b.sh` corre lo que faltó (`dsp8`, `baseline_prover16` en `pdf_set`) y SKETCH otra vez con los arreglos de #128, tras comprobarlos con Lean real. Tablas: `python data/eval/results/eval_v4_analysis.py` → `eval_v4_analysis.json` (`docs/eval_report_v4.md`).

| Config | Qué hace | API |
|---|---|---|
| `baseline_prover`, `baseline_prover16`, `baseline_prover32` | El demostrador solo: k muestras (8/16/32) + una ronda de autocorrección con los errores de Lean | 0 |
| `full` | Etapa 0 (el demostrador) y, sólo si `prover.api_fallback: true`, el bucle de agentes. **eval-v3 corrió con `api_fallback: true`**; hoy es `false` por defecto (#116): cámbialo para repetirla | 0 / ~US$0.007 por problema no resuelto |
| `dsp`, `dsp8` | Etapa 0 + DRAFT: plan y prueba informal del modelo con razonamiento (`llm_draft`) + lemas del RAG en el prompt del demostrador (8 + 8). `dsp8` sin ejemplares del PDF | ~US$0.005 por problema no resuelto |
| `repair` | Etapa 0 + REPAIR: las casi-pruebas del demostrador (≤ 2 errores) se parten en sus `have` y se cierran los pasos malos con la batería y el demostrador (#125) | 0 |
| `sketch` | REPAIR + SKETCH: el modelo con razonamiento escribe el esqueleto Lean (`have … := by sorry`) y cada paso va a la batería y al demostrador; 2 rondas (#126) | ~US$0.005 por problema no resuelto |
| `rag_proofs` | Etapa 0 + EXAMPLES: pruebas verificadas de los 3 problemas más parecidos (embeddings, Lean Workbook), primero tal cual y luego como ejemplos en el prompt (8 + 8) (#127) | 0 |

Las muestras del demostrador se guardan en `data/eval/prover_cache.jsonl` por (mensajes, semilla, parámetros): repetir una corrida con la caché reproduce exactamente las mismas muestras, y las configuraciones que comparten la etapa 0 comparten sus muestras. Sin la caché, `llama-server` con varios slots no es determinista bit a bit aunque la semilla esté fijada. `pdf_set.jsonl` no se versiona (deriva de un PDF de licencia desconocida, SEC-2): sus 32 enunciados se formalizaron a mano, con las respuestas comprobadas por fuerza bruta (#119).

## 5. Datos de evaluación

- `data/eval/MathlibQR.json`, `MathlibMPR.json`: benchmarks de LeanSearch v2.
- `data/eval/minif2f_v2/`: miniF2F-v2 (Apache 2.0) + `dsp2/` pruebas verificadas de DeepSeek-Prover-V2.
- `data/eval/premises_gold.jsonl`, `informal_gold.jsonl`, `problems/*.jsonl`.
