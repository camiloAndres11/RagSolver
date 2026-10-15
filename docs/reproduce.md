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

## 5. Datos de evaluación

- `data/eval/MathlibQR.json`, `MathlibMPR.json`: benchmarks de LeanSearch v2.
- `data/eval/minif2f_v2/`: miniF2F-v2 (Apache 2.0) + `dsp2/` pruebas verificadas de DeepSeek-Prover-V2.
- `data/eval/premises_gold.jsonl`, `informal_gold.jsonl`, `problems/*.jsonl`.
