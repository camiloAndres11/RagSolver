# Evaluación del RAG y del pipeline — RagSolver

Generado por `scripts/eval_report.py`. Metodología: `docs/PLAN_RAG.md` §10-§11; decisiones y correcciones: `docs/DECISIONS.md`.

## 1. Retriever sobre Mathlib (MathlibQR, LeanSearch v2)

Tabla de muestra: gold de MathlibQR/MPR + premisas de miniF2F + 3000 distractores. Cobertura del gold en la tabla: **793/946 (83.8%)**. Métricas crudas (el recall condicionado a cobertura = crudo ÷ cobertura).

### 1a. 946 consultas, sin rerank

| Variante | recall@10 | ndcg@10 | mrr |
|---|---|---|---|
| denso | 0.662 | 0.568 | 0.542 |
| bm25 | 0.595 | 0.481 | 0.449 |
| hibrido | 0.655 | 0.543 | 0.512 |

### 1b. Muestra aleatoria de 100 consultas (semilla fija), 4 variantes

| Variante | recall@10 | ndcg@10 | mrr |
|---|---|---|---|
| denso | 0.760 | 0.631 | 0.591 |
| bm25 | 0.560 | 0.437 | 0.403 |
| hibrido | 0.660 | 0.533 | 0.501 |
| hibrido_rerank | 0.710 | 0.617 | 0.592 |

### 1c. Misma muestra de 100 consultas sobre el **índice completo de Mathlib** (`mathlib_full`, 278 160 declaraciones; DECISIONS #48, #56)

| Variante | recall@10 | ndcg@10 | mrr |
|---|---|---|---|
| denso | 0.410 | 0.297 | 0.268 |
| bm25 | 0.250 | 0.196 | 0.184 |
| hibrido | 0.380 | 0.276 | 0.250 |
| hibrido_rerank | 0.450 | 0.339 | 0.307 |

Los números de la tabla de muestra (1a-1b) están inflados por el tamaño del pool (3 903 candidatos); 1c es la medida realista del retriever en producción.

**Índice ANN** (`IVF_SQ`, 256 particiones, nprobes 32, refine ×5; DECISIONS #60): recall@10 respecto de la búsqueda exacta **0.989**, recall@50 0.9769 (200 consultas sintéticas); latencia densa p50 2.713 s → **0.045 s** (p95 2.887 → 0.078 s).

### 1d. Misma muestra sobre el índice completo **con el índice ANN** (el que usan la demo y los agentes con `RAGSOLVER_MATHLIB_TABLE=mathlib_full`)

| Variante | recall@10 | ndcg@10 | mrr |
|---|---|---|---|
| denso | 0.420 | 0.302 | 0.270 |
| bm25 | 0.250 | 0.196 | 0.184 |
| hibrido | 0.380 | 0.277 | 0.251 |
| hibrido_rerank | 0.450 | 0.339 | 0.307 |

## 2. Premisas gold de miniF2F (pruebas verificadas de DeepSeek-Prover-V2)

Problemas: 352 (con ≥1 premisa en la tabla: 352); cobertura de premisas en la tabla: 100.0%; rerank: False.

| Consulta | recall@8 | recall@20 | ndcg@10 | mrr |
|---|---|---|---|---|
| enunciado informal (tabla de muestra) | 0.080 | 0.153 | 0.076 | 0.129 |
| enunciado formal (Lean) (tabla de muestra) | 0.118 | 0.180 | 0.106 | 0.176 |
| enunciado informal (índice completo) | 0.006 | 0.010 | 0.006 | 0.016 |
| enunciado formal (Lean) (índice completo) | 0.009 | 0.018 | 0.007 | 0.015 |

## 3. Gold informal (30 consultas sobre el PDF de ejemplo)

- Recall@5: **1.000** (Wilson 95 %: [0.886, 1.0]); MRR: 1.000
- Consultas dependientes de figura (n=15): Recall@5 = 1.000; cobertura de entidades en las descripciones de figura = 0.911

## 4. Contaminación corpus informal ↔ miniF2F-v2

- Pares con similitud > 0.9: **0** de 100×488; similitud máxima observada: 0.818
- Top-3 más parecidos (card, problema, sim): [[23, 'imo_1959_p1', 0.8181180953979492], [58, 'induction_divisibility_3div2tooddnp1', 0.8117700815200806], [76, 'algebra_abpbcpcageq3_sumaonsqrtapbgeq3onsqrt2', 0.7985193729400635]]

## 5. Ablaciones end-to-end (`eval-v1`)

| Config | n | pass@1 | IC95 Wilson | llamadas LLM (media) | tokens (media) | wall s (mediana) | precisión uso de lemas | re-recuperación OK | taxonomía de errores |
|---|---|---|---|---|---|---|---|---|---|
| full | 60 | 0.15 | [0.081, 0.261] | 7.62 | 35190 | 287.3 | 0.002 | 0.0 | {'planning': 49, 'lean_syntax': 6, 'exhausted': 33, 'misformalization': 21, 'missing_lemma': 20, 'unknown': 3, 'abandon': 5, 'llm_output_invalid:generator': 2, 'llm_output_invalid:formalizer': 9, 'llm_output_invalid:planner': 1, 'llm_output_invalid:critic': 1} |
| no_rag | 60 | 0.15 | [0.081, 0.261] | 7.83 | 23360 | 152.8 | None | None | {'missing_lemma': 25, 'planning': 41, 'exhausted': 29, 'lean_syntax': 5, 'llm_output_invalid:critic': 4, 'misformalization': 17, 'llm_output_invalid:formalizer': 15, 'unknown': 4, 'abandon': 2, 'llm_output_invalid:generator': 1} |
| no_critic | 60 | 0.067 | [0.026, 0.159] | 3.28 | 16433 | 62.4 | 0.001 | None | {'unknown': 44, 'exhausted': 44, 'llm_output_invalid:formalizer': 7, 'llm_output_invalid:generator': 4, 'llm_output_invalid:planner': 1} |
| baseline_a | 60 | 0.083 | [0.036, 0.181] | 1.13 | 3012 | 5.2 | None | None | {'unknown': 47, 'llm_output_invalid:baseline_a': 8} |
| baseline_b | 60 | 0.0 | [0.0, 0.06] | 1.05 | 2214 | 5.9 | None | None | {'llm_output_invalid:baseline_b': 3} |

### Resultado por problema (✔ = prueba aceptada por el kernel)

| Problema | full | no_rag | no_critic | baseline_a | baseline_b |
|---|---|---|---|---|---|
| `mathd_algebra_67` | ✔ | ✔ | – | – | – |
| `algebra_sqineq_2unitcircatblt1` | ✔ | ✔ | ✔ | – | – |
| `mathd_algebra_28` | ✔ | ✔ | – | ✔ | – |
| `mathd_algebra_125` | ✔ | ✔ | – | ✔ | – |
| `mathd_algebra_342` | – | – | – | – | – |
| `induction_prod1p1onk3le3m1onn` | – | – | – | – | – |
| `mathd_algebra_214` | – | – | – | – | – |
| `aime_1983_p2` | – | – | – | – | – |
| `amc12a_2019_p12` | – | – | – | – | – |
| `amc12b_2003_p17` | ✔ | – | – | – | – |
| `mathd_algebra_437` | – | – | – | – | – |
| `mathd_algebra_77` | – | – | – | – | – |
| `amc12b_2003_p6` | – | – | – | – | – |
| `amc12b_2020_p13` | – | – | – | – | – |
| `imo_1962_p2` | – | – | – | – | – |
| `amc12a_2010_p11` | – | – | – | – | – |
| `imo_1978_p5` | – | – | – | – | – |
| `mathd_numbertheory_728` | ✔ | ✔ | – | ✔ | – |
| `mathd_numbertheory_229` | – | ✔ | ✔ | – | – |
| `mathd_numbertheory_233` | ✔ | – | – | – | – |
| `mathd_numbertheory_403` | – | – | – | – | – |
| `mathd_numbertheory_101` | ✔ | ✔ | ✔ | ✔ | – |
| `mathd_numbertheory_109` | – | – | – | – | – |
| `numbertheory_aneqprodakp4_anmsqrtanp1eq2` | – | – | – | – | – |
| `numbertheory_sqmod3in01d` | – | – | – | – | – |
| `amc12a_2003_p23` | – | – | – | – | – |
| `amc12a_2020_p10` | – | – | – | – | – |
| `amc12a_2008_p15` | – | – | – | – | – |
| `amc12a_2016_p3` | ✔ | ✔ | – | – | – |
| `amc12a_2002_p21` | – | – | – | – | – |
| `amc12b_2002_p3` | – | – | – | – | – |
| `imo_1977_p5` | – | – | – | – | – |
| `mathd_algebra_24` | – | – | – | – | – |
| `amc12_2000_p6` | – | – | – | – | – |
| `mathd_algebra_170` | – | – | – | – | – |
| `mathd_algebra_455` | – | – | – | – | – |
| `mathd_algebra_104` | – | ✔ | – | ✔ | – |
| `amc12a_2002_p6` | – | – | – | – | – |
| `amc12a_2003_p1` | – | – | – | – | – |
| `mathd_algebra_209` | – | – | ✔ | – | – |
| `imo_1987_p6` | – | – | – | – | – |
| `amc12a_2003_p25` | – | – | – | – | – |
| `amc12_2001_p5` | – | – | – | – | – |
| `amc12a_2020_p9` | – | – | – | – | – |
| `imo_1965_p1` | – | – | – | – | – |
| `imo_1992_p1` | – | – | – | – | – |
| `aime_1990_p2` | – | – | – | – | – |
| `numbertheory_notequiv2i2jasqbsqdiv8` | – | – | – | – | – |
| `imo_1981_p6` | – | – | – | – | – |
| `imo_2006_p3` | – | – | – | – | – |
| `imo_1993_p5` | – | – | – | – | – |
| `aime_1997_p11` | – | – | – | – | – |
| `mathd_numbertheory_155` | – | – | – | – | – |
| `mathd_algebra_398` | – | – | – | – | – |
| `imo_1974_p3` | – | – | – | – | – |
| `imo_1990_p3` | – | – | – | – | – |
| `mathd_algebra_185` | – | – | – | – | – |
| `imo_1964_p1_1` | – | – | – | – | – |
| `mathd_algebra_224` | – | – | – | – | – |
| `amc12b_2002_p11` | – | – | – | – | – |

### Comparación pareada (McNemar exacto, α = 0.05; n pequeño → poca potencia)

| A | B | sólo A | sólo B | p |
|---|---|---|---|---|
| full | no_rag | 2 | 2 | 1.000 |
| full | no_critic | 7 | 2 | 0.180 |
| full | baseline_a | 5 | 1 | 0.219 |
| no_rag | no_critic | 6 | 1 | 0.125 |
| no_rag | baseline_a | 4 | 0 | 0.125 |
| no_critic | baseline_a | 3 | 4 | 1.000 |

**Falsos positivos evitados** (propuesta §8.3): la línea base B se autoevaluó como correcta en 55/60 problemas; en 50 de ellos la formalización directa (línea base A) **no** pasa el kernel.

### Uso de lemas recuperados (config `full`)

| Problema | verificados en la prueba | usados en el boceto | recuperados |
|---|---|---|---|
| `mathd_algebra_67` | 0 | 0 | 13 |
| `algebra_sqineq_2unitcircatblt1` | 1 | 0 | 25 |
| `mathd_algebra_28` | 0 | 0 | 24 |
| `mathd_algebra_125` | 0 | 0 | 40 |
| `mathd_algebra_342` | 0 | 0 | 27 |
| `induction_prod1p1onk3le3m1onn` | 0 | 0 | 33 |
| `mathd_algebra_214` | 0 | 1 | 41 |
| `aime_1983_p2` | 0 | 0 | 39 |
| `amc12a_2019_p12` | 0 | 0 | 35 |
| `amc12b_2003_p17` | 2 | 0 | 23 |
| `mathd_algebra_437` | 0 | 1 | 37 |
| `mathd_algebra_77` | 0 | 0 | 49 |
| `amc12b_2003_p6` | 0 | 0 | 36 |
| `amc12b_2020_p13` | 0 | 0 | 44 |
| `imo_1962_p2` | 0 | 0 | 15 |
| `amc12a_2010_p11` | 0 | 0 | 34 |
| `imo_1978_p5` | 0 | 0 | 30 |
| `mathd_numbertheory_728` | 0 | 0 | 30 |
| `mathd_numbertheory_229` | 0 | 0 | 22 |
| `mathd_numbertheory_233` | 0 | 0 | 38 |
| `mathd_numbertheory_403` | 0 | 2 | 29 |
| `mathd_numbertheory_101` | 0 | 0 | 19 |
| `mathd_numbertheory_109` | 0 | 0 | 28 |
| `numbertheory_aneqprodakp4_anmsqrtanp1eq2` | 0 | 0 | 51 |
| `numbertheory_sqmod3in01d` | 0 | 0 | 34 |
| `amc12a_2003_p23` | 0 | 0 | 27 |
| `amc12a_2020_p10` | 0 | 0 | 33 |
| `amc12a_2008_p15` | 0 | 0 | 18 |
| `amc12a_2016_p3` | 1 | 0 | 23 |
| `amc12a_2002_p21` | 0 | 0 | 51 |
| `amc12b_2002_p3` | 0 | 4 | 43 |
| `imo_1977_p5` | 0 | 0 | 55 |
| `mathd_algebra_24` | 0 | 0 | 36 |
| `amc12_2000_p6` | 0 | 0 | 50 |
| `mathd_algebra_170` | 0 | 0 | 60 |
| `mathd_algebra_455` | 0 | 0 | 38 |
| `mathd_algebra_104` | 0 | 0 | 53 |
| `amc12a_2002_p6` | 0 | 0 | 50 |
| `amc12a_2003_p1` | 0 | 0 | 31 |
| `imo_1987_p6` | 0 | 0 | 36 |
| `amc12a_2003_p25` | 0 | 0 | 31 |
| `amc12_2001_p5` | 0 | 0 | 34 |
| `amc12a_2020_p9` | 0 | 0 | 25 |
| `imo_1965_p1` | 0 | 0 | 35 |
| `imo_1992_p1` | 0 | 0 | 29 |
| `mathd_algebra_209` | 0 | 0 | 22 |
| `numbertheory_notequiv2i2jasqbsqdiv8` | 0 | 0 | 31 |
| `imo_1981_p6` | 0 | 0 | 42 |
| `imo_2006_p3` | 0 | 0 | 37 |
| `aime_1990_p2` | 0 | 0 | 37 |
| `imo_1993_p5` | 0 | 0 | 36 |
| `aime_1997_p11` | 0 | 0 | 44 |
| `mathd_numbertheory_155` | 0 | 0 | 50 |
| `mathd_algebra_398` | 0 | 0 | 25 |
| `imo_1974_p3` | 0 | 0 | 0 |
| `imo_1990_p3` | 0 | 0 | 32 |
| `mathd_algebra_185` | 0 | 0 | 49 |
| `imo_1964_p1_1` | 0 | 0 | 30 |
| `mathd_algebra_224` | 0 | 0 | 34 |
| `amc12b_2002_p11` | 0 | 0 | 32 |

Pruebas aceptadas (`full`):

- `mathd_algebra_67`: `rw [h₀, h₁] ·   norm_num`
- `algebra_sqineq_2unitcircatblt1`: `have h1 : 0 ≤ (a - b) ^ 2 := sq_nonneg (a - b) ·   have h2 : 2 * (a * b) ≤ a ^ 2 + b ^ 2 := by ·     nlinarith [h1] ·   `
- `mathd_algebra_28`: `constructor ·   · rw [hS] ·     refine ⟨-5/4, ?_⟩ ·     norm_num ·   · intro c hc ·     rw [hS] at hc ·     rcases hc wi`
- `mathd_algebra_125`: `-- SG1: derive 6 * s = 36 ·   have h₃ : 6 * s = 36 := by ·     -- rewrite f using h₁ ·     rw [← h₁] at h₂ ·     -- simp`
- `amc12b_2003_p17`: `have hx_ne : x ≠ 0 := by ·     intro hx0 ·     have hx0' : x * y ^ 3 = 0 := by simp [hx0] ·     have hlog0 : Real.log (x`
- `mathd_numbertheory_728`: `norm_num [ZMod.natCast_zmod_val, Int.ModEq]`
- `mathd_numbertheory_233`: `norm_num at h₀ ⊢ ·   exact h₀`
- `mathd_numbertheory_101`: `norm_num`
- `amc12a_2016_p3`: `norm_num [h₀, Int.floor_eq_iff]`

Modelos LLM usados en la corrida (cadena por cuota diaria): ['deepseek/deepseek-v4-flash-0731']. Re-recuperaciones tras `lema faltante` en `full`: 1 (exitosas: 0).

### Desglose por estrato (pass@1, n) — `eval_v1.jsonl`

**Por partición**

| partición | full | no_rag | no_critic | baseline_a | baseline_b |
|---|---|---|---|---|---|
| dev | 3/24 (0.12) [0.043, 0.31] | 3/24 (0.12) [0.043, 0.31] | 2/24 (0.08) [0.023, 0.258] | 2/24 (0.08) [0.023, 0.258] | 0/24 (0.00) [0.0, 0.138] |
| test | 6/36 (0.17) [0.079, 0.319] | 6/36 (0.17) [0.079, 0.319] | 2/36 (0.06) [0.015, 0.181] | 3/36 (0.08) [0.029, 0.218] | 0/36 (0.00) [0.0, 0.096] |

**Por área**

| área | full | no_rag | no_critic | baseline_a | baseline_b |
|---|---|---|---|---|---|
| algebra | 5/24 (0.21) [0.092, 0.405] | 4/24 (0.17) [0.067, 0.359] | 2/24 (0.08) [0.023, 0.258] | 2/24 (0.08) [0.023, 0.258] | 0/24 (0.00) [0.0, 0.138] |
| combinatorics | 0/12 (0.00) [0.0, 0.243] | 1/12 (0.08) [0.015, 0.354] | 0/12 (0.00) [0.0, 0.243] | 1/12 (0.08) [0.015, 0.354] | 0/12 (0.00) [0.0, 0.243] |
| number_theory | 4/24 (0.17) [0.067, 0.359] | 4/24 (0.17) [0.067, 0.359] | 2/24 (0.08) [0.023, 0.258] | 2/24 (0.08) [0.023, 0.258] | 0/24 (0.00) [0.0, 0.138] |

**Por nivel de dificultad**

| nivel de dificultad | full | no_rag | no_critic | baseline_a | baseline_b |
|---|---|---|---|---|---|
| 1 | 7/27 (0.26) [0.132, 0.447] | 8/27 (0.30) [0.159, 0.485] | 4/27 (0.15) [0.059, 0.325] | 5/27 (0.19) [0.082, 0.367] | 0/27 (0.00) [0.0, 0.125] |
| 2 | 2/21 (0.10) [0.027, 0.289] | 1/21 (0.05) [0.008, 0.227] | 0/21 (0.00) [0.0, 0.155] | 0/21 (0.00) [0.0, 0.155] | 0/21 (0.00) [0.0, 0.155] |
| 3 | 0/12 (0.00) [0.0, 0.243] | 0/12 (0.00) [0.0, 0.243] | 0/12 (0.00) [0.0, 0.243] | 0/12 (0.00) [0.0, 0.243] | 0/12 (0.00) [0.0, 0.243] |


### Lectura (métricas de la propuesta §8.3)

- **Contribución del RAG** = pass@1(full) − pass@1(sin RAG) = 0.15 − 0.15 = **0.0** (pares discordantes (2, 2)); el contexto recuperado multiplica los tokens por 1.5×; precisión de uso de lemas = 0.002.
- **Robustez del refinamiento** = pass@1(full) − pass@1(sin Crítico) = **0.083** (pares discordantes (7, 2)); dentro de `full`, pass@1 tras el 1.er intento = 0.117 y final = 0.15: la ganancia atribuible a los reintentos es la diferencia entre ambos; el resto de la brecha con `sin Crítico` (mismo primer intento) es variación entre corridas.
- **Eficiencia**: 7.62 llamadas LLM por problema en `full` (tope RNF-3: 20), 1.13 en la línea base A.
- **Verificación formal vs. autoevaluación**: la línea base B se declaró correcta en 55/60 problemas; el kernel confirma 11 de esos en alguna configuración verificada.
