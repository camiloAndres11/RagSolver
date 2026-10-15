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

## 5. Ablaciones end-to-end (`f5-ablations`)

| Config | n | pass@1 | IC95 Wilson | llamadas LLM (media) | tokens (media) | wall s (mediana) | precisión uso de lemas | re-recuperación OK | taxonomía de errores |
|---|---|---|---|---|---|---|---|---|---|
| full | 13 | 0.692 | [0.424, 0.873] | 5.69 | 13740 | 8.6 | 0.011 | 0.0 | {'lean_syntax': 8, 'exhausted': 4, 'planning': 1, 'misformalization': 1, 'missing_lemma': 4} |
| no_rag | 13 | 0.769 | [0.497, 0.918] | 5.31 | 4709 | 5.6 | None | None | {'missing_lemma': 4, 'lean_syntax': 8, 'exhausted': 3} |
| no_critic | 13 | 0.462 | [0.232, 0.709] | 3.0 | 7231 | 6.1 | 0.004 | None | {'unknown': 7, 'exhausted': 7} |
| baseline_a | 13 | 0.615 | [0.355, 0.823] | 1.0 | 410 | 1.1 | None | None | {'unknown': 5} |
| baseline_b | 13 | 0.0 | [0.0, 0.228] | 1.0 | 412 | 1.5 | None | None | {} |

### Resultado por problema (✔ = prueba aceptada por el kernel)

| Problema | full | no_rag | no_critic | baseline_a | baseline_b |
|---|---|---|---|---|---|
| `triv_add_zero` | ✔ | ✔ | ✔ | ✔ | – |
| `triv_add_comm` | ✔ | ✔ | – | ✔ | – |
| `triv_square_expand` | ✔ | ✔ | ✔ | ✔ | – |
| `triv_even_square` | ✔ | ✔ | ✔ | ✔ | – |
| `triv_dvd_trans` | ✔ | ✔ | ✔ | ✔ | – |
| `mathd_numbertheory_127` | ✔ | ✔ | ✔ | ✔ | – |
| `mathd_algebra_160` | – | ✔ | – | – | – |
| `mathd_numbertheory_1124` | – | – | – | – | – |
| `induction_1pxpownlt1pnx` | – | – | – | – | – |
| `mathd_algebra_440` | ✔ | ✔ | – | ✔ | – |
| `mathd_numbertheory_495` | – | – | – | – | – |
| `mathd_numbertheory_342` | ✔ | ✔ | ✔ | ✔ | – |
| `mathd_numbertheory_222` | ✔ | ✔ | – | – | – |

### Comparación pareada (McNemar exacto, α = 0.05; n pequeño → poca potencia)

| A | B | sólo A | sólo B | p |
|---|---|---|---|---|
| full | no_rag | 0 | 1 | 1.000 |
| full | no_critic | 3 | 0 | 0.250 |
| full | baseline_a | 1 | 0 | 1.000 |
| no_rag | no_critic | 4 | 0 | 0.125 |
| no_rag | baseline_a | 2 | 0 | 0.500 |
| no_critic | baseline_a | 0 | 2 | 0.500 |

**Falsos positivos evitados** (propuesta §8.3): la línea base B se autoevaluó como correcta en 13/13 problemas; en 5 de ellos la formalización directa (línea base A) **no** pasa el kernel.

### Uso de lemas recuperados (config `full`)

| Problema | verificados en la prueba | usados en el boceto | recuperados |
|---|---|---|---|
| `triv_add_zero` | 1 | 1 | 11 |
| `triv_add_comm` | 2 | 2 | 18 |
| `triv_square_expand` | 0 | 3 | 19 |
| `triv_even_square` | 0 | 1 | 13 |
| `triv_dvd_trans` | 0 | 2 | 18 |
| `mathd_numbertheory_127` | 0 | 0 | 24 |
| `mathd_algebra_160` | 0 | 0 | 28 |
| `mathd_numbertheory_1124` | 0 | 0 | 48 |
| `induction_1pxpownlt1pnx` | 0 | 3 | 19 |
| `mathd_algebra_440` | 0 | 0 | 17 |
| `mathd_numbertheory_495` | 0 | 1 | 25 |
| `mathd_numbertheory_342` | 0 | 0 | 14 |
| `mathd_numbertheory_222` | 0 | 0 | 21 |

Pruebas aceptadas (`full`):

- `triv_add_zero`: `exact add_zero n`
- `triv_add_comm`: `induction b with ·   | zero => rw [add_zero, zero_add] ·   | succ b ih => rw [Nat.add_succ, ih, Nat.succ_add]`
- `triv_square_expand`: `ring`
- `triv_even_square`: `rcases h with ⟨k, rfl⟩ ·   use 2 * k ^ 2 ·   ring`
- `triv_dvd_trans`: `exact dvd_trans h1 h2`
- `mathd_numbertheory_127`: `decide`
- `mathd_algebra_440`: `linarith`
- `mathd_numbertheory_342`: `norm_num`
- `mathd_numbertheory_222`: `have h2 := Nat.gcd_mul_lcm 120 b ·   rewrite [h₀, h₁] at h2 ·   omega`

Modelos LLM usados en la corrida (cadena por cuota diaria): ['gemini-3.5-flash', 'gemini-3.5-flash-lite']. Re-recuperaciones tras `lema faltante` en `full`: 3 (exitosas: 0).

### Desglose por estrato (pass@1, n) — `dev_all.jsonl`

**Por partición**

| partición | full | no_rag | no_critic | baseline_a | baseline_b |
|---|---|---|---|---|---|
| dev | 9/13 (0.69) [0.424, 0.873] | 10/13 (0.77) [0.497, 0.918] | 6/13 (0.46) [0.232, 0.709] | 8/13 (0.62) [0.355, 0.823] | 0/13 (0.00) [0.0, 0.228] |

**Por área**

| área | full | no_rag | no_critic | baseline_a | baseline_b |
|---|---|---|---|---|---|
| algebra | 4/5 (0.80) [0.376, 0.964] | 5/5 (1.00) [0.566, 1.0] | 2/5 (0.40) [0.118, 0.769] | 4/5 (0.80) [0.376, 0.964] | 0/5 (0.00) [0.0, 0.434] |
| induction | 0/1 (0.00) [0.0, 0.793] | 0/1 (0.00) [0.0, 0.793] | 0/1 (0.00) [0.0, 0.793] | 0/1 (0.00) [0.0, 0.793] | 0/1 (0.00) [0.0, 0.793] |
| number_theory | 5/7 (0.71) [0.359, 0.918] | 5/7 (0.71) [0.359, 0.918] | 4/7 (0.57) [0.25, 0.842] | 4/7 (0.57) [0.25, 0.842] | 0/7 (0.00) [0.0, 0.354] |

**Por nivel de dificultad**

| nivel de dificultad | full | no_rag | no_critic | baseline_a | baseline_b |
|---|---|---|---|---|---|
| 1 | 5/5 (1.00) [0.566, 1.0] | 5/5 (1.00) [0.566, 1.0] | 4/5 (0.80) [0.376, 0.964] | 5/5 (1.00) [0.566, 1.0] | 0/5 (0.00) [0.0, 0.434] |
| 2 | 4/8 (0.50) [0.215, 0.785] | 5/8 (0.62) [0.306, 0.863] | 2/8 (0.25) [0.071, 0.591] | 3/8 (0.38) [0.137, 0.694] | 0/8 (0.00) [0.0, 0.324] |


### Lectura (métricas de la propuesta §8.3)

- **Contribución del RAG** = pass@1(full) − pass@1(sin RAG) = 0.692 − 0.769 = **-0.077** (pares discordantes (0, 1)); el contexto recuperado multiplica los tokens por 2.9×; precisión de uso de lemas = 0.011.
- **Robustez del refinamiento** = pass@1(full) − pass@1(sin Crítico) = **0.23** (pares discordantes (3, 0)); dentro de `full`, pass@1 tras el 1.er intento = None y final = 0.692: la ganancia atribuible a los reintentos es la diferencia entre ambos; el resto de la brecha con `sin Crítico` (mismo primer intento) es variación entre corridas.
- **Eficiencia**: 5.69 llamadas LLM por problema en `full` (tope RNF-3: 20), 1.0 en la línea base A.
- **Verificación formal vs. autoevaluación**: la línea base B se declaró correcta en 13/13 problemas; el kernel confirma 10 de esos en alguna configuración verificada.
