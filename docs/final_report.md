# RagSolver: A Multi-Agent System for Olympiad-Level Problem Solving with Embeddings-Based Retrieval and Formal Verification in Lean 4

*Course project — Inteligencia Artificial, Ingeniería de Sistemas y Computación, UPTC Sogamoso. Draft v0.4 (2026-10-18). The tables of §6.3 are generated from the 60-problem experiment (`data/eval/runs/eval-v1/`) by `scripts/fill_final_report.py`. §6.4–§6.5 and the corresponding parts of §3, §7, §8 and §10 report the October 2026 iteration (goal-level retrieval and a local specialised prover; `docs/Mejorasuwu.md`, `docs/eval_report_v2.md`, `docs/eval_report_v3.md`); the architecture as it stands today is in `docs/architecture.md` and `docs/diagramas.md`.*

## Abstract

Large language models reason well about competition mathematics in natural language but offer no guarantee of logical validity. We present RagSolver, a multi-agent system that separates the *generation* of proof hypotheses from their *verification*: a planner decomposes the problem into sub-goals, a retriever grounds each sub-goal in Mathlib declarations and worked examples through embeddings-based retrieval-augmented generation (RAG), a generator writes a natural-language proof sketch, an autoformalizer translates it into Lean 4 tactics, the Lean 4 kernel (Mathlib v4.28.0-rc1) verifies them, and a critic classifies failures and drives bounded retries. Retrieval — Qwen3-Embedding-0.6B and BM25 fused by reciprocal rank over LanceDB, with an approximate-nearest-neighbour index over all 278,160 Mathlib declarations — costs zero LLM calls; a second corpus of solved olympiad problems is ingested from PDF with a document-AI pipeline. In the main experiment (60 stratified miniF2F-v2 problems, five configurations, one model: DeepSeek V4 Flash) the full pipeline proves 9/60 problems (15 %, Wilson 95 % CI 8–26 %) with 7.6 LLM calls per problem, against 5/60 for a single LLM writing Lean directly and 4/60 for the same pipeline without its critic; an unverified natural-language baseline claims success on 55/60 problems, of which only 11 could be confirmed by the kernel under any configuration. Retrieval did not change the success rate (9/60 with and without RAG, McNemar p = 1.0) while costing 1.5× the tokens; on the full index the retriever reaches recall@10 = 0.45 on MathlibQR. No pairwise difference survives Bonferroni correction, and run-to-run variance is as large as the effects: two runs of the identical first attempt solved 7 and 4 problems with only 2 in common. We report these results together with seven system defects found while running the experiment; code, index, evaluation sets and per-agent traces are prepared for release pending the maintainer's licensing decision.

A second iteration (October 2026) acted on those findings. Retrieval now queries with the Lean *goal* of each proof step instead of the problem statement, is consumed by Lean's own automation (a tactic battery with zero LLM calls) before any LLM sees the step, and a competition-lemma prior recovers 33 % of the premises each step needs (recall@16 per goal) against 3 % for the dense index. Tactics alone, with no LLM, then close 11/60 problems — more than the whole first pipeline (9/60) — but retrieved premises still do not show up in the kernel's proof terms. Replacing the general-purpose model by a locally served Lean prover (Goedel-Prover-V2-8B on the project's own GPU, zero API cost) as a stage 0 raises success on 30 paired problems from 8 to **21** (McNemar 13–0, p = 0.0002), including two IMO problems; all 21 are solved by the prover alone, so neither the agents nor retrieval add problems on top of it in their current form. Because miniF2F is that prover's public benchmark, contamination cannot be excluded.

## 1. Introduction

Frontier LLMs reach gold-medal scores on informal olympiad problems, yet a plausible derivation can be logically wrong; proof assistants such as Lean 4 close this gap with a small trusted kernel that checks every step. The engineering problem is to combine flexible generation with rigid verification in an auditable pipeline. The research question (proposal §4.1) is: *to what extent does a multi-agent architecture that separates hypothesis generation, supported by embeddings-based retrieval, from formal verification improve success rate, efficiency and reliability over (a) a single LLM reasoning in natural language and (b) a single LLM emitting Lean directly?*

Contributions: (1) an implemented and tested multi-agent pipeline (LangGraph) with typed JSON-Schema contracts, a per-problem LLM budget and JSONL traces per agent, attempt and search (RF-1–RF-7, RNF-1–RNF-5); (2) a hybrid retrieval module over Mathlib and over PDF-derived solved problems, with figure-aware ingestion and a citation model tied to kernel verification; (3) an evaluation protocol with isolated retrieval benchmarks (MathlibQR, gold premises extracted from published verified proofs, an annotated informal gold set, a contamination check) and a five-configuration ablation with paired statistics; (4) a 60-problem, five-configuration experiment with a single model, reported with its null results (retrieval), its non-significant ones (critic, baselines) and the run-to-run variance that bounds what a single run can show, plus the corrected methodology behind it; (5) a goal-level retrieval and consumption design, measured without LLM calls, and a locally served specialised prover integrated as stage 0 and as lemma solver for open goals, evaluated on paired problems with a cost guard (§6.4–§6.5).

## 2. Related Work

*Transformers and LLMs.* Self-attention [1] underlies both the generators and the embedding encoder. Reasoning techniques — chain-of-thought [2], tree-of-thought [3], ReAct [4] and Self-Refine [5] — motivate the planner, the critic and the retry loop.

*Formal theorem proving with LLMs.* GPT-f [6] and LeanDojo/ReProver [7] established LLM tactic generation with premise retrieval; DeepSeek-Prover-V2 [8] reaches 88.9 % pass@1 on miniF2F-test through recursive sub-goal decomposition, validating the planner→sub-goal→proof pattern; AlphaProof [9] shows reinforcement learning over Lean at IMO level. DeepSeek-Prover-V2 also publishes its verified miniF2F proofs, which we reuse as gold premises [8]; Kimina-Prover and Goedel-Prover release comparable artifacts. Goedel-Prover-V2 [25] (Apache-2.0, 8B) reports 84.6 % pass@32 on miniF2F and is the specialised prover used in §6.5; DeepSeek-Prover-V2's recipe of proving each sub-goal as a stand-alone lemma [8] is reused for the goals that automation leaves open. LeanHammer [26] shows that premise selection conditioned on the proof state beats statement-level selection (recall@16 63.5 vs 35.1 for ReProver), against 38.4 for MePo-style symbol-based selection; Lean 4.28 already ships such a selector (Sine Qua Non), which `grind +suggestions` consumes here; Lean Workbook proofs [27] provide the competition-lemma prior.

*Premise retrieval for Mathlib.* LeanSearch v2 [10] informalizes every Mathlib declaration hierarchically with an LLM and retrieves with Qwen3-Embedding-8B plus a reranker (nDCG@10 = 0.62 on MathlibQR); Lean Finder [11] fine-tunes a prover model for user intents; TheoremGraph [12] bridges informal and formal statements. We reuse the LeanSearch v2 corpus and its benchmarks with the 0.6B models of the same family.

*Retrieval-augmented generation.* RAG [13] with hybrid dense/lexical search and reciprocal-rank fusion [14], cross-encoder reranking and *contextual retrieval* [15] (LLM-generated context prepended before indexing) inform the retriever design; Qwen3-Embedding [16] provides multilingual, instruction-aware embeddings.

*Document AI.* Parsing scientific PDFs with correct formulas and figure–text links is the main residual error source for RAG [17]; MinerU [18] and OmniDocBench [19] set the current standard; multimodal RAG by figure summaries [20] and late-interaction visual retrieval (ColPali) are the two options for figures.

*Benchmarks.* miniF2F [21], its revision miniF2F-v2 [22] (formal statements corrected to match informal ones) and PutnamBench [23] are the standard formal competition benchmarks; contamination control against test sets is a methodological requirement [24].

## 3. System Design

Figure 1 (`docs/architecture.md`) shows the component and sequence diagrams. The orchestrator is a LangGraph state graph PLAN → RETRIEVE → GENERATE → FORMALIZE → VERIFY → {DONE | CRITIQUE → retry/re-plan/fail}.

**Agents (Role A).** Each agent is a pure function with a Pydantic contract (JSON Schema exported to `docs/interfaces/`): *Planner* (sub-goals with natural-language statement, Lean-flavoured hint, 2–3 search queries, area), *Generator* (proof sketch + `used_citations`), *Autoformalizer* (only the tactic block after `:= by` — the theorem statement is spliced from the problem, so the LLM cannot weaken it), *Critic* (error type ∈ {lean_syntax, missing_lemma, misformalization, timeout, planning, unknown}, action ∈ {retry, replan, abandon}, missing identifiers). Two single-LLM baselines implement proposal §8.2: *A* (direct Lean proof, verified) and *B* (natural-language chain-of-thought with self-assessment, unverified).

**Retriever (Role B).** `RetrievalRequest → RetrievalResult` with modes `nl`, `formal`, `error` and `exemplar` (query-instruction templates per mode), SQL pre-filters, RRF over dense and BM25 channels and over multiple queries, optional cross-encoder reranking, de-duplication (figure chunks collapse into their problem card), and citations with locators (Mathlib: `decl_name`, module, docs URL; informal: document, pages, item, figures). In `error` mode the Lean identifiers quoted in the message are matched exactly and pinned at rank 1. The retriever makes **zero** LLM calls (RNF-3); every search emits a trace event.

**Corpora.** *A — Mathlib*: 278,160 declarations of the LeanSearch v2 corpus (tag v4.28.0-rc1, same tag as the verifier), normalized to `MathlibChunk`. Two tables are served: a 3,903-declaration *sample* (all benchmark gold + 3,000 random distractors) used for development and the main experiment, and the *full* corpus (278,160 declarations, embedded on the GPU through llama.cpp/Vulkan in 4.5 h instead of ~15 h on CPU) with an IVF_SQ approximate-nearest-neighbour index (256 partitions, 32 probes, exact re-ranking of the 5·k best candidates) whose top-10 agrees with exact search on 98.9 % of the neighbours at 60× lower latency (2.7 s → 0.045 s per dense search on CPU). *B — informal*: a 100-problem Spanish olympiad collection parsed with MinerU (local mode); layout gaps are detected by cross-checking the PDF text layer word by word and transcribed with a VLM; formula LaTeX is reviewed by the VLM under conservative acceptance rules; 53 figures are cropped and described with the problem statement as context; each problem card receives an English contextual summary, area, techniques and difficulty. 100/100 problem–solution pairs were recovered (92 by deterministic parsing, the rest by numeral recovery and one LLM split).

**Verifier.** A persistent Lean REPL (`lean-interact`) imports Mathlib once (~5 min) and checks each candidate in ~0.3 s; `sorry`, `admit`, `axiom` and `native_decide` are forbidden; a proof counts as solved only if the kernel accepts it (RNF-1).

**LLM client.** One client for Gemini and OpenAI-compatible endpoints (OpenRouter, Ollama…), JSON-Schema-constrained outputs, fixed temperature and seed (RNF-2), per-call token and **exact cost** accounting (OpenRouter `usage.cost`), a price-capped provider routing policy, and a model chain that switches models when a quota is exhausted; the model actually used is recorded per call. Malformed JSON gets exactly one retry with a doubled output budget; if it is still invalid the attempt ends as a *system failure* inside the graph (`llm_output_invalid:<agent>`), keeping every call already spent in the problem's accounting.

**October 2026 redesign (current system; the description above is the pipeline evaluated in §6.2–§6.3).**

*Goal-level proving.* The Autoformalizer's proof is split mechanically into `have … := by <block>` steps; each step becomes a `sorry` and the Lean REPL returns one goal and one proof state per `sorry` (`LeanChecker.goals`, `step`). For each goal, a **CLOSE** stage with zero LLM calls tries, in order: the model's own block for that step; a configurable tactic battery (`grind +lax [premises]`, `nlinarith [hints]`, `grind +suggestions`, `try?` whose concrete script replaces the `?`); and, if still open, the local prover as a stand-alone lemma. Only the goals that remain open reach **FILL** (one LLM call, exact goal plus retrieved premises with signatures plus the last error). Proved goals are kept across attempts and re-plans, and the Planner receives the Critic's feedback, its previous strategy and the proved goals on a re-plan. The final file is reassembled (`fill_sorries`) and goes through the unchanged verifier.

*Premise retrieval per goal.* Channel A is a competition prior: the frequency of each lemma in 29,750 Lean Workbook proofs [27] (MIT licence), validated against the Lean environment itself with `#check` (778 lemmas, including core lemmas such as `Nat.mul_mod` and `eq_comm` that the LeanSearch corpus lacks), filtered by the symbols of the goal. Channel B is Lean's native selection, consumed through `grind +suggestions`. Channel C, the dense index queried with the goal, is switched off (§6.4). "Lemma used" now means that the constant appears in the term the kernel accepted (`Verdict.used_constants`), not in the proof text.

*Local specialised prover (stage 0).* Before planning, Goedel-Prover-V2-8B (Q6_K GGUF) is queried with the whole theorem in its native prompt format through `llama-server` (Vulkan, AMD RX 9060 XT, 8 slots of 12,288 tokens, about 45 tok/s per sample and 70 tok/s aggregate). Up to 8 samples are drawn in batches of 4 with early exit; only the *tactic block* of the requested theorem is extracted and spliced into the problem's own statement, so the model cannot weaken it, and every candidate passes the same validator → kernel → axiom audit. If none verifies, one correction round shows the two best candidates their Lean errors. Because the prover was trained on Lean 4.9 and the verifier is v4.28, retrieval is used here to repair renamed lemmas: for each `unknown identifier/constant`, the `error`-mode retriever proposes similar declarations that are checked with `#check` against the pinned Mathlib, and only names that exist are shown to the model. Samples are cached on disk, so configurations that share a stage 0 share exactly the same samples. Unreadable output from the Critic or FILL no longer ends a problem (retry with Lean's errors / no proposals).

## 4. Methodology

Design-science iteration by component (proposal §7): the retriever was validated on reference query–lemma sets *before* integration (risk §11), each agent has unit tests with simulated LLM/verifier/retriever responses, contract tests check the JSON Schemas, and the full pipeline runs on five reference trivial theorems as an integration test. Every deviation from the plan is logged with evidence in `docs/DECISIONS.md` (115 entries; #1–#88 cover the first experiment, #89–#115 the October iteration), including corrections that changed reported numbers: a Mathlib ingestion bug (name components mistaken for aliases, inflating Phase-1 recall) and an IR-metrics library returning wrong values in long runs (replaced by verified in-house metrics). Running the main experiment surfaced seven further system defects that no unit test had caught, each fixed with a regression test before the affected pairs were re-run with the final code (Appendix E): a tactic-indentation bug in the proof splicer, a stray leading `by` emitted by the model, a thread race while loading the embedder, a hidden 20× retry loop on malformed JSON that also hid its token cost, loss of accumulated accounting when an agent's output stayed malformed, a table-building bug that only appears above 5,000 rows, and a baseline output schema whose optional fields let the model put its answer in the wrong field.

## 5. Experimental Setup

**Retrieval benchmarks.** MathlibQR (946 queries over 200 declarations, LeanSearch v2); gold premises for 352 miniF2F problems extracted from DeepSeek-Prover-V2's published verified proofs (402 unique lemmas, 5.3 per problem), queried by informal and by formal statement; an informal gold set of 30 English queries (15 figure-dependent, with expected figure entities) over the PDF corpus; a contamination check (cosine similarity between every informal card and every miniF2F-v2 statement).

**Problems.** Development: 5 trivial reference theorems + 8 easy miniF2F-v2s problems (n = 13). Main experiment: 60 miniF2F-v2s problems stratified in 3 areas × 3 difficulty levels (level 1 = textbook `mathd`, 2 = AMC/AIME, 3 = IMO), strict dev (24, split `valid`) / test (36, split `test`) partition; prompts and thresholds are tuned only on dev. PutnamBench, which the proposal lists as a secondary source, was not included: its Lean statements are maintained against their own Lean/Mathlib pin, and porting and re-checking them against the v4.28.0-rc1 pin shared by the index and the verifier was out of scope for this iteration; the IMO-level stratum of miniF2F-v2 plays the role of the hardest tier.

**Configurations** (proposal §8.2): full; without RAG; without critic (k_max = 1); baseline A; baseline B. k_max = 3, LLM budget = 20 calls/problem, temperature 0.2, seed 13. Development runs used `gemini-3.5-flash` (free tier, with a fallback chain of smaller models when the daily quota ran out). The main experiment uses a **single model for all five configurations**, `deepseek/deepseek-v4-flash-0731` through OpenRouter, with the chain-of-thought disabled (`reasoning: off`; 24 s vs 76–92 s per planner call with reasoning on) and 4 000 output tokens, so that model and configuration are not confounded; six problems are solved concurrently (the Lean REPL is serialized). Requests are routed by OpenRouter to providers priced at most US$0.10 / 0.20 per M input / output tokens (StreamLake, DeepInfra, Makora; mostly fp8 serving), cheapest first; earlier pairs were served by OpenRouter's default routing and, briefly, by an input-price sort, so the provider mix is not constant across pairs (§8). The experiment ran under a hard spending cap on the API key; exact costs are reported in §6.3.

*October 2026 evaluations.* Configurations added: `no_rag` redefined as the same CLOSE battery and prover without retrieved premises, exemplars or name suggestions (so the comparison isolates retrieval from Lean automation); `baseline_auto` (zero LLM, battery only on the root goal); `baseline_prover` (zero API, local prover only); `rag_nl` (the eval-v1 flow, defined but not run). eval-v2 used the same 60 problems in four interleaved rounds of 15 with a cost guard after each round that stops the chain on any alert (more than 20 calls in a pair, more than US$0.006 per pair, more than 10 % errors, an LLM call in a zero-LLM baseline, or a projection above the key's balance); it stopped after round 1 and, by the user's choice, continued with round 2 only. eval-v3 reuses rounds 1–2 (30 problems) so that every comparison is paired with eval-v2. Provider price cap raised from {0.10, 0.20} to {0.13, 0.30} US$/M tokens (eight providers) during eval-v2 because only one provider qualified under the old cap and it was saturated.

**Metrics** (§8.3): pass@1 with Wilson 95 % intervals; false positives avoided (baseline B self-assessed correct ∧ kernel rejects); LLM calls, retrieval calls, tokens and wall time with bootstrap intervals; marginal gain per retry (pass by attempt); RAG contribution (full − no-RAG); lemma-use precision (verified / retrieved); re-retrieval success; error taxonomy. Paired comparisons use exact McNemar tests (α = 0.05, Bonferroni over the 6 pairs of kernel-verified configurations; baseline B is unverified and enters only through the false-positive metric).

**Hardware.** Retrieval, reranking and the Lean REPL run on CPU (Ryzen 5 9600X, 7.5 GB RAM, WSL2). The AMD RX 9060 XT is not usable from WSL2 through ROCm (open issue, `DECISIONS.md` #11–12); it is used from the Windows side through llama.cpp's Vulkan backend (`DECISIONS.md` #48) to embed the full Mathlib corpus (278 160 declarations; vectors match the PyTorch embedder with cosine ≥ 0.9996). All heavy steps are reproducible from `docs/reproduce.md`.

## 6. Results

### 6.1 Retriever (isolated)

| Benchmark | Setting | Metric | Value |
|---|---|---|---|
| MathlibQR (946 q, 3.9k-row table, coverage 83.8 %) | dense / BM25 / hybrid | recall@10 | 0.662 / 0.595 / 0.655 |
| | | nDCG@10 | 0.565 / 0.472 / 0.538 |
| MathlibQR (100 random q, with reranker) | dense / BM25 / hybrid / hybrid+rerank | recall@10 | 0.760 / 0.560 / 0.660 / 0.710 |
| | | nDCG@10 | 0.631 / 0.437 / 0.533 / 0.617 |
| **MathlibQR, same 100 q, full Mathlib index (278 160 rows)** | dense / BM25 / hybrid / hybrid+rerank | recall@10 | **0.410 / 0.250 / 0.380 / 0.450** |
| | | nDCG@10 | 0.297 / 0.196 / 0.276 / 0.339 |
| Gold premises, 352 miniF2F problems (3.9k-row table) | informal query / formal query | recall@8 | 0.080 / 0.118 |
| | | recall@20 | 0.153 / 0.180 |
| Gold premises, full Mathlib index | informal query / formal query | recall@20 | 0.010 / 0.018 |
| MathlibQR, same 100 q, full index **with the ANN index** | dense / BM25 / hybrid / hybrid+rerank | recall@10 | 0.420 / 0.250 / 0.380 / 0.450 |
| | | nDCG@10 | 0.302 / 0.196 / 0.277 / 0.339 |
| ANN index on the full corpus (IVF_SQ, 200 synthetic queries) | agreement with exact search | recall@10 / @50 | 0.989 / 0.977 |
| | dense search latency p50 (CPU) | exact → ANN | 2.71 s → 0.045 s |
| Informal gold, 30 queries (15 figure-dependent) | hybrid + rerank | recall@5, MRR | **1.000**, 1.000 |
| | figure-entity coverage in descriptions | | 0.911 |
| Contamination, 100 cards × 488 miniF2F | cosine > 0.9 | pairs | **0** (max 0.818) |

Dense retrieval alone is the strongest single channel on MathlibQR with correct declaration ids; BM25 fusion costs about one point of recall but is kept because the `error` mode and identifier-bearing queries depend on the lexical channel; the reranker recovers most of the fusion loss. The sample-table figures are inflated by the size of the candidate pool (3 903 rows): on the full index (278 160 declarations) recall@10 drops to 0.41 (dense) and 0.45 (hybrid + rerank), which is the realistic operating point of the retriever; the ranking of the variants is unchanged and the reranker becomes the most valuable component. The approximate index costs nothing measurable on real queries: every metric matches exact search within one point (dense recall@10 0.42 vs 0.41), while each dense search drops from 2.7 s to 0.045 s. Whole-statement premise retrieval is a lower bound for the pipeline, whose retrieval unit is the planner's sub-goal; on the full index it is close to zero (recall@20 ≈ 0.01–0.02), which motivates sub-goal queries, identifier pinning in `error` mode and the reranker.

### 6.2 End-to-end ablation (development set, n = 13, `gemini-3.5-flash`)

| Config | pass@1 | Wilson 95 % | LLM calls | tokens | wall s (median) | lemma-use precision | errors |
|---|---|---|---|---|---|---|---|
| full | **0.692** (9/13) | [0.42, 0.87] | 5.7 | 13,740 | 8.6 | 0.011 | lean_syntax 8, missing_lemma 4, planning 1, misformalization 1 |
| no RAG | 0.769 (10/13) | [0.50, 0.92] | 5.3 | 4,709 | 5.6 | – | lean_syntax 8, missing_lemma 4 |
| no critic | 0.462 (6/13) | [0.23, 0.71] | 3.0 | 7,231 | 6.1 | 0.004 | unknown 7 |
| baseline A | 0.615 (8/13) | [0.36, 0.82] | 1.0 | 410 | 1.1 | – | unknown 5 |
| baseline B | 0 verified; self-assessed correct 13/13 | – | 1.0 | 412 | 1.5 | – | – |

Paired McNemar: full vs no-critic +3/−0 (p = 0.25); full vs no-RAG 0/−1 (p = 1.0); full vs baseline A +1/−0 (p = 1.0). False positives avoided: 5/13. The five trivial reference theorems are solved by every verified configuration; `mathd_numbertheory_1124`, `induction_1pxpownlt1pnx` and `mathd_numbertheory_495` are solved by none. Accepted proofs are short tactic scripts (`ring`, `omega`, `norm_num`, `decide`, `linarith`, `exact dvd_trans h1 h2`, a `Nat.gcd_mul_lcm` rewrite); retrieved lemmas appear by name in only 2 accepted proofs.

### 6.3 Main experiment (`eval-v1`, 60 problems × 5 configurations)

Single model for every configuration (`deepseek/deepseek-v4-flash-0731`, reasoning off, temperature 0.2, seed 13); 3 903-row Mathlib table; k_max = 3; budget 20 calls. Tables generated by `scripts/fill_final_report.py` from `summary.json` and `results.jsonl`.

| Configuration | n | pass@1 | Wilson 95 % | LLM calls (mean, bootstrap 95 %) | Retrieval calls | Tokens (mean) | Wall s (median) | Lemma-use precision |
|---|---|---|---|---|---|---|---|---|
| full | 60 | **0.150** | [0.081, 0.261] | 7.62 [6.8, 8.48] | 8.03 | 35190 | 287.3 | 0.002 |
| no RAG | 60 | **0.150** | [0.081, 0.261] | 7.83 [7.02, 8.6] | 0.0 | 23360 | 152.8 | – |
| no critic | 60 | **0.067** | [0.026, 0.159] | 3.28 [3.13, 3.43] | 5.2 | 16433 | 62.4 | 0.001 |
| baseline A | 60 | **0.083** | [0.036, 0.181] | 1.13 [1.05, 1.22] | 0.0 | 3012 | 5.2 | – |
| baseline B (unverified) | 60 | n/a (unverified) | – | 1.05 [1.0, 1.1] | 0.0 | 2214 | 5.9 | – |

**Pass by attempt** (cumulative pass@1 after k attempts; marginal gain of the critic loop, §8.3):

| Configuration | ≤ 1 attempt(s) | ≤ 2 attempt(s) | ≤ 3 attempt(s) |
|---|---|---|---|
| full | 0.117 | 0.15 | 0.15 |
| no RAG | 0.15 | 0.15 | 0.15 |
| no critic | 0.067 | – | – |

**Stratified results** (`eval_v1.jsonl`; dev = prompts/thresholds tuned on it, test = held out):

*Split* — pass@1 (solved/n)

| Split | full | no RAG | no critic | baseline A |
|---|---|---|---|---|
| dev | 3/24 (0.12) | 3/24 (0.12) | 2/24 (0.08) | 2/24 (0.08) |
| test | 6/36 (0.17) | 6/36 (0.17) | 2/36 (0.06) | 3/36 (0.08) |

*Area* — pass@1 (solved/n)

| Area | full | no RAG | no critic | baseline A |
|---|---|---|---|---|
| algebra | 5/24 (0.21) | 4/24 (0.17) | 2/24 (0.08) | 2/24 (0.08) |
| combinatorics | 0/12 (0.00) | 1/12 (0.08) | 0/12 (0.00) | 1/12 (0.08) |
| number_theory | 4/24 (0.17) | 4/24 (0.17) | 2/24 (0.08) | 2/24 (0.08) |

*Level* — pass@1 (solved/n)

| Level | full | no RAG | no critic | baseline A |
|---|---|---|---|---|
| 1 | 7/27 (0.26) | 8/27 (0.30) | 4/27 (0.15) | 5/27 (0.19) |
| 2 | 2/21 (0.10) | 1/21 (0.05) | 0/21 (0.00) | 0/21 (0.00) |
| 3 | 0/12 (0.00) | 0/12 (0.00) | 0/12 (0.00) | 0/12 (0.00) |

**Paired comparisons**

Exact McNemar tests on paired outcomes (α = 0.05, Bonferroni over 6 pairs → 0.0083).

| A | B | only A | only B | p | significant |
|---|---|---|---|---|---|
| full | no RAG | 2 | 2 | 1.000 | no |
| full | no critic | 7 | 2 | 0.180 | no |
| full | baseline A | 5 | 1 | 0.219 | no |
| no RAG | no critic | 6 | 1 | 0.125 | no |
| no RAG | baseline A | 4 | 0 | 0.125 | no |
| no critic | baseline A | 3 | 4 | 1.000 | no |

**False positives avoided** (proposal §8.3): baseline B self-assessed its proof as correct on 55/60 problems; on 50 of them the direct formalization (baseline A) is rejected by the kernel.

**Cost.** Total: 300 (problem, configuration) pairs, 1255 LLM calls, 2.91 M input / 1.90 M output tokens. Exact provider cost recorded for 793 of 1102 traced calls: US$0.295 (earlier calls predate per-call cost logging).

### 6.4 Goal-level retrieval and consumption (October 2026; `docs/PLAN_MEJORA_RAG.md`, `docs/eval_report_v2.md`)

*Why eval-v1 retrieval failed (measured with zero LLM calls).* Premise recall against the gold extracted from verified proofs, on the full index: informal statement as query 0.012 (recall@8), formal statement 0.000, a static competition prior 0.20. In `eval-v1` the first error of a rejected attempt was an unknown identifier in only about 6 % of cases; most were tactics written without seeing the goal (`unsolved goals`, `rewrite failed`, `linarith failed`). Passing the top-16 of the old retriever to `grind` *lowered* the number of root goals closed from 7 to 6, whereas the prior or Lean's native selection raised it to 9, and `try?` alone closed 12/60 — more than the whole eval-v1 pipeline (9/60). 28 % of the premise uses in verified miniF2F proofs are core-Lean lemmas that the LeanSearch corpus does not contain, so the published gold had been truncated to the corpus.

| Measurement (no LLM) | Result |
|---|---|
| Premise recall@16 per goal (1,295 goals of 230 problems, gold validated against Lean) | prior filtered by goal symbols **0.327**; static prior 0.261; dense index with the goal 0.028; prior + dense fused 0.097 |
| Premise recall per problem (gold validated against Lean) | NL query 0.006, formal query 0.004, prior 0.229 |
| Battery on the root goal of the 60 `eval-v1` problems | 11/60 without premises, 12/60 with them; `grind` closes 6 → 8 with premises; 4 problems that no `eval-v1` configuration solved |

The dense channel is therefore off by default (fusing it removes 23 points), and Lean's native selection is used through `grind +suggestions` rather than as a list of names.

*End-to-end (eval-v2, `deepseek/deepseek-v4-flash`, `mathlib_full`, cost guard).* Round 1 (15 problems): `full` 6, `no_rag` 7, `baseline_auto` 5, against 3 and 4 for the `eval-v1` `full` and `no_rag` on the same problems (McNemar p = 0.375, confounded by provider and index). Round 2: 2/15 in all three. Over the 30 paired problems `full` solved 8 and `no_rag` 9. `baseline_auto` on all 60 problems: **11/60 with no LLM at all**, against 9/60 for the `eval-v1` full pipeline. The kernel's accepted terms used **one** retrieved premise in one of the six problems `full` solved in round 1: `grind` already knows the generic lemmas the prior supplies, so giving them to it adds nothing, and the premises shown to FILL are rarely used. In harder problems, 5.2 of 5.7 steps were still open after the battery; 12 of 43 failures were `llm_output_invalid` (Critic 6, FILL 4), which stopped being fatal afterwards. Cost per pair was US$0.0036 (`full`) and US$0.0032 (`no_rag`), the same as `eval-v1`. Round 3 was stopped (11/15 `full` pairs, 2 solved) in favour of the redesign of §6.5.

### 6.5 Local specialised prover (October 2026; `docs/PLAN_REDISENO_PROVER.md`, `docs/eval_report_v3.md`)

30 problems (rounds 1–2), paired with eval-v2. `baseline_prover` is the local prover alone; `full` is the complete redesign.

| System | Solved /30 | Wilson 95 % | Difficulty 1 / 2 / 3 | API cost |
|---|---|---|---|---|
| **v3 `full`** | **21** | 0.52–0.83 | 13/16 · 6/10 · 2/4 | US$0.061 |
| v3 `baseline_prover` | 21 | 0.52–0.83 | 13/16 · 6/10 · 2/4 | US$0 |
| v2 `full` | 8 | 0.14–0.44 | 8/16 · 0/10 · 0/4 | US$0.108 |
| v2 `no_rag` | 9 | 0.17–0.48 | 8/16 · 1/10 · 0/4 | US$0.096 |
| v2 `baseline_auto` | 7 | 0.12–0.41 | 7/16 · 0/10 · 0/4 | US$0 |

Paired exact McNemar: v3 `full` vs v2 `full` 13–0 (**p = 0.0002**); vs v2 `no_rag` 12–0 (p = 0.0005); `baseline_prover` vs `baseline_auto` 14–0 (p = 0.0001); `full` vs `baseline_prover` 0–0. At difficulty 2–3 the count goes from 0/14 to 8/14, including `imo_1964_p1_1` and `imo_1965_p1`; the two IMO proofs were read by hand and are genuine step-by-step arguments. Every proof passed the full verifier (statement intact, tactic validator, kernel, axioms ⊆ {`propext`, `Classical.choice`, `Quot.sound`}).

*What contributes what.* All 21 problems were solved in stage 0 (no API call). The API agents ran on the 9 unsolved problems (3.4 calls on average), cost US$0.061 and solved none. Retrieval acted in 2 correction rounds, suggesting 8 existing names, of which the kernel used none; the per-goal premises are never reached because stage 0 resolves the problem before goals exist. On GPU, about 4 hours for both configurations (7.5 samples per problem on average in `full`, most from the shared cache; easy problems finish in the first batch of four, hard ones exhaust the eight samples plus correction in about 12 minutes).

## 7. Discussion

*Formal verification is what makes the numbers meaningful.* Baseline B, reasoning in natural language without a checker, declares its proof correct on 55 of 60 problems; the kernel confirms a proof for only 11 of those 55 under any configuration, and for 5 under the direct-formalization baseline A (the proposal's "false positives avoided" = 50). A failed formalization does not prove the informal argument wrong — the system may simply fail to formalize a correct proof — but on the 12 IMO-level problems, none of which any configuration proves, baseline B still declared 9 proofs correct, which bounds how much trust such self-assessments deserve. Reporting only kernel-accepted proofs (RNF-1) turns an apparent 92 % success rate into 8–15 %. The development ablation with a different model showed the same pattern (13/13 claimed, 5 formalizations rejected).

*The multi-agent loop helps modestly over a single LLM.* The full pipeline proves 9/60 against 5/60 for baseline A (5 vs 1 discordant problems, p = 0.22), gaining on level-1 (7 vs 5) and level-2 problems (2 vs 0), at 7× the LLM calls (7.6 vs 1.1) and 12× the tokens. The direction agrees with the development ablation (9/13 vs 8/13), but neither difference is statistically significant.

*Retrieval does not help at this scale.* Full and no-RAG both prove 9/60 (2 vs 2 discordant problems, p = 1.0); retrieval costs 1.5× the tokens and 1.9× the median wall time. Only 4 of the 2,055 lemmas retrieved in the full configuration appear in kernel-accepted proofs (8 are cited in sketches). Four mechanisms explain the null effect: (i) the solved problems close by tactic automation rather than by named lemmas — `norm_num` appears in 18 of the 27 kernel-accepted proofs across the verified configurations, followed by `rw` with the hypotheses, `nlinarith`/`linarith` and `omega`; (ii) the lemmas that harder proofs need are rarely retrieved — premise recall@20 is 0.15–0.18 on the 3.9k-row sample and 0.01–0.02 on the full index (§6.1); (iii) the critic attributes most failures to planning (49 labels) and misformalization (21) rather than to missing lemmas (20); (iv) with a 3.9k-row sample many useful lemmas are simply absent. The retriever works where it was measured directly (§6.1); the bottleneck is turning retrieved lemmas into proofs.

*Refinement helps, but single runs overstate it.* Within the full pipeline the critic-driven retry adds 2 problems (7 solved at the first attempt, 9 after the second; a third attempt never helped). The comparison against the no-critic configuration, 9 vs 4 (p = 0.18), overstates the effect: no-critic's single attempt is the same computation as full's first attempt, yet the two runs solved 7 and 4 problems with only 2 in common.

*Run-to-run variance is as large as the effects.* The three pipeline configurations together prove 12 distinct problems (20 %, CI 12–32 %) although none proves more than 9, and only 2 problems are proved by all three. With temperature 0.2 and provider-level non-determinism, a single pass@1 run over 60 problems cannot resolve differences of fewer than about five problems. Independent samples (pass@k) and repeated runs, which the proposal lists and the API budget did not cover, are the right instrument; the union figure suggests they would also raise the success rate substantially.

*Failure modes.* No configuration proves any of the 12 IMO-level problems, and level-2 success is 0–10 %. With the model's reasoning mode off (a latency and cost choice), it sometimes "thinks" inside the JSON fields until the output cap: 12–20 of 60 problems per pipeline configuration end with an invalid output after the single retry, and 8–13 % of formalizer calls return an empty proof. Both are counted as failures. Enabling the reasoning mode for the formalizer, or delegating formalization to a Lean-specialised prover model, are the most direct improvements; the second was carried out in October and is the largest effect measured in the project (§6.5, §7 *October iteration*).

*October iteration: the model, not the retrieval, was the bottleneck.* Three findings. (i) Moving retrieval from the statement to the Lean goal changed recall by an order of magnitude (0.03 → 0.33 per goal) and made Lean's automation alone competitive with the whole first pipeline (11/60 vs 9/60 with zero LLM calls), but it did not turn retrieved premises into used premises: the generic lemmas of the prior are already known to `grind`, and the kernel's terms contain at most one retrieved lemma. Where retrieval could still matter is for *specific* lemmas (`Nat.Prime.eq_one_or_self_of_dvd`, `Real.log_rpow`) that neither the automation nor a general model knows, which is the channel that failed (dense recall 0.028). (ii) Replacing the general model with a Lean-specialised prover took 30 paired problems from 8 to 21 (p = 0.0002) at zero API cost; at difficulty 2–3 from 0/14 to 8/14. (iii) With that prover, `full` equals `baseline_prover` in all 30 problems: agents and retrieval add nothing *on top of it* in their present form, since they only act when stage 0 fails and then with the general model. The honest reading is that specialised prover plus formal verification decides the result in this setting; the useful role found for retrieval is repairing lemma names that changed between Lean 4.9 and the pinned Mathlib, and so far the kernel has used none of those suggestions. Whether retrieval or the plan help *inside* the prover's prompt (Draft-Sketch-Prove style) has not been measured.

*Retrieval quality depends on the size of the haystack.* On the 3.9k-row sample, recall@10 on MathlibQR is 0.66–0.76; on the full 278k-declaration corpus the same queries reach 0.41 (dense) and 0.45 (hybrid + rerank), with nDCG@10 = 0.34 — below LeanSearch v2's 0.62 with 8B models, the gap one would expect from a 13× smaller embedder and reranker. The sample numbers are therefore an optimistic bound, and the full-index figures are the realistic operating point. Whole-statement premise retrieval on the full index is nearly zero (recall@20 ≈ 0.01–0.02): a competition problem statement is a poor query for the specific lemmas its proof uses, which supports the design choice of querying per planner sub-goal and pinning identifiers quoted by Lean errors. On the informal side, 30/30 gold queries — including all 15 figure-dependent ones — are answered in the top 5, indicating that figure descriptions generated with the problem statement as context are sufficient for retrieval; the optional visual channel (ColPali) is not triggered.

*Methodological corrections.* Two silent errors would have inflated the report: the ingestion bug (only 60/200 single-component gold names matched, and any `Lattice.*` row counted as the gold `Lattice`) and the IR library returning ~0.1 recall for lists that recompute to 0.76. Both were caught by cross-checks (an integration test comparing the retriever's dense order with raw vector search; recomputation from stored traces) and are documented with evidence. Running the main experiment exposed seven more defects (Appendix E); every pair that exercised a code path changed afterwards was discarded and re-run, so the 300 reported pairs are consistent with the final code (up to the heuristic detection of hidden retries, §8).

## 8. Limitations

*Statistical power.* 60 problems (24 dev / 36 test) give Wilson intervals of about ±9 points at pass@1 = 0.15 (8–26 %), and every paired difference between configurations is within that noise; the development ablation (n = 13) is indicative only. *Index.* The main experiment retrieves from the 3.9k-declaration sample; the full index was evaluated only in isolation (§6.1), and the end-to-end repetition over it (`eval-v1-full`) is prepared but was not run within the API budget. That sample was constructed to include every benchmark-gold declaration by design (§6, Corpora): it seeds the pool the RAG configurations search against with the answers the retrieval benchmarks score against, which plausibly favors the RAG-enabled configurations relative to the full 278k-declaration library and should be weighed against the null retrieval effect reported in §6.3. Separately, the source corpus itself has coverage gaps unrelated to sample size: no declaration whose fully-qualified name's last component starts with `eq_` appears anywhere in the published LeanSearch v2 JSONL (0 of 310,579, verified by exact-prefix scan), so lemmas like `eq_comm` or `Nat.Prime.eq_one_or_self_of_dvd` cannot be retrieved by name at any index size, independently of ranking quality. *Model serving.* One model (DeepSeek V4 Flash) serves every configuration, but OpenRouter routed requests to several providers (mostly fp8), and routing changed once during the run (default → price-capped); provider-level numerical differences are not controlled. With the model's reasoning mode disabled (for latency and cost), it sometimes "thinks" inside the JSON fields on AMC/IMO problems until the output cap and the attempt ends as `llm_output_invalid` — a real failure mode of this configuration, counted as unsolved. *Run history.* Pairs affected by the defects in Appendix E were discarded and re-run with the final code; detection of the hidden-retry pairs is heuristic (latency unexplained by counted output). *Empty outputs.* A present-but-empty proof field is treated as a failed attempt rather than retried (8–13 % of formalizer calls), identically in every configuration. *False positives.* The proposal's metric counts a baseline-B claim as a false positive when the kernel rejects the formalization; a failed formalization of a correct informal proof would be miscounted, so the metric is an upper bound on wrong claims. *Scope.* The informal corpus is a single book whose parser is specialized to its "Problemario/Soluciones" structure; sub-goals are verified inside one proof rather than as independent lemmas (RF-1 partially), and retrieval and retries happen once per problem rather than once per sub-goal (RF-3, RF-6 partially); pass@k with independent samples was not run (k_max retries are not samples); CPU-only reranking makes figure-heavy queries slow (~1 min), so the agents run without the reranker; area labels of the evaluation set are heuristic; the informal gold set was annotated by the assistant, not by an independent human. *Citation bookkeeping.* In `eval-v1`, citation ids were reassigned on every retrieval call within a problem, so a replan or an error-mode re-retrieval could leave stale ids in `used_citations`; `used_in_sketch` is unreliable for the 34/300 reported pairs this affected (`used_in_proof`, which matches by declaration name rather than id, is not affected). Ids are stable per chunk in all later runs (DECISIONS #80); the `eval-v1` rows are not recomputed because its retriever traces cannot be attributed to a configuration.

*October iteration.* (a) **Possible contamination:** miniF2F is the public benchmark on which Goedel-Prover-V2 is evaluated and compared; the statements are miniF2F-v2 (corrected), but the model may have seen similar problems in its synthetic training data. §6.5 measures this system with this prover, not generalisation to unseen problems; that needs a set outside miniF2F (the project's own PDF or PutnamBench). (b) **n = 30**, the first two of four rounds, for eval-v2 and eval-v3; rounds 3–4 were not run with the redesign for lack of balance. (c) **Not an equal-budget comparison between models:** eval-v2 used about 8 calls to a general API model per problem, eval-v3 up to 8 local samples of a specialised model plus the agents as fallback. (d) **Lean 4.9 vs the pinned Lean 4.28:** proofs that use renamed Mathlib lemmas fail; the 9 unsolved problems include cases where the correction round did not repair it. (e) **Shared samples:** `full` and `baseline_prover` use the same cached stage-0 samples, so their tie in stage 0 holds by construction. (f) The eval-v2 vs eval-v1 comparison is confounded by provider, index (`mathlib_full` vs the seeded sample) and prompts. (g) The `no_rag` ablation of the redesign was not run for lack of balance. (h) The retrieved-premise count uses the accepted term's constants; the prior's and the gold's style come from related provers, so recall against that gold may overstate usefulness (the closing metric in the kernel is what counts).

## 9. Ethical Considerations

Only kernel-accepted proofs are reported as solved; every unverified artifact (sketches, baseline B) is labelled as such. Licenses: Mathlib and the LeanSearch v2 corpus (Apache 2.0), miniF2F-v2 (Apache 2.0), DeepSeek-Prover-V2 solutions (their license), Qwen3 models (Apache 2.0); the olympiad PDF is not redistributed (metadata only). Compute footprint: one desktop machine (CPU for retrieval, reranking and Lean; a consumer AMD GPU for the one-off corpus embedding); every LLM call, its tokens and its exact cost are recorded in the traces. Development used a free API tier, which implies the provider may use prompts to improve its models; the main experiment used a paid endpoint under a hard spending cap. The PDF is a public book.

## 10. Conclusion and Future Work

RagSolver implements the proposed architecture end to end with typed contracts, budgets, traces and kernel verification. On 60 stratified competition problems the full system proves 15 %, ahead of a single LLM writing Lean (8 %) and of the same pipeline without its critic (7 %), although none of these differences is significant; retrieval contributes nothing measurable at this scale; and kernel verification shows that an unverified model's claims of success (55/60) can be confirmed for only 11 problems. The main lessons are methodological: single-run differences are within run-to-run noise, retrieval quality measured on a sample overstates quality on the full library, and an agentic evaluation harness needs the same testing discipline as the system it evaluates. The October iteration changes the practical conclusion: replacing the general model with a locally served specialised prover raises success on 30 paired problems from 8 to 21 (p = 0.0002) at zero API cost, while the multi-agent loop and retrieval, in their present form, add no problem on top of it; goal-level retrieval does make Lean's own automation competitive with the first pipeline (11/60 with no LLM). Next steps, in order of expected value: pass@32 on the 9 unsolved problems (GPU only), placing the plan and the retrieved lemmas *inside the prover's prompt* and comparing against `baseline_prover`, evaluation on problems outside miniF2F, and switching off the API fallback that spent US$0.06 for nothing. Future work: repeated runs and pass@k with independent samples; the reasoning mode or a Lean-specialised prover for the formalizer; the reranker for the agents (+7 points of recall@10 on the full index, ~1.2 GB more memory than this machine could spare); sub-goals verified as independent lemmas (full RF-1); the `full`/`no critic` repetition over the full Mathlib index (prepared, `scripts/run_chain.sh`); papers and lecture notes through the `paper_section` chunker; PutnamBench.

## References

1. Vaswani, A. et al. *Attention Is All You Need.* NeurIPS 2017.
2. Wei, J. et al. *Chain-of-Thought Prompting Elicits Reasoning in Large Language Models.* NeurIPS 2022.
3. Yao, S. et al. *Tree of Thoughts: Deliberate Problem Solving with Large Language Models.* NeurIPS 2023.
4. Yao, S. et al. *ReAct: Synergizing Reasoning and Acting in Language Models.* ICLR 2023.
5. Madaan, A. et al. *Self-Refine: Iterative Refinement with Self-Feedback.* NeurIPS 2023.
6. Polu, S., Sutskever, I. *Generative Language Modeling for Automated Theorem Proving.* arXiv:2009.03393, 2020.
7. Yang, K. et al. *LeanDojo: Theorem Proving with Retrieval-Augmented Language Models.* NeurIPS 2023.
8. DeepSeek-AI. *DeepSeek-Prover-V2: Advancing Formal Mathematical Reasoning via Reinforcement Learning for Subgoal Decomposition.* 2025. Verified miniF2F solutions: github.com/deepseek-ai/DeepSeek-Prover-V2.
9. Google DeepMind. *AI achieves silver-medal standard solving International Mathematical Olympiad problems (AlphaProof, AlphaGeometry 2).* 2024.
10. *LeanSearch v2: Global Premise Retrieval for Lean 4 Theorem Proving.* arXiv:2605.13137, 2026. Corpus: huggingface.co/datasets/FrenzyMath/lsv2-mathlib-v4.28.0-rc1-jsonl.
11. *Lean Finder: Semantic Search for Mathlib That Understands User Intents.* arXiv:2510.15940, 2025.
12. *TheoremGraph: Bridging Formal and Informal Mathematics.* arXiv:2606.25363, 2026.
13. Lewis, P. et al. *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks.* NeurIPS 2020.
14. Cormack, G., Clarke, C., Buettcher, S. *Reciprocal Rank Fusion outperforms Condorcet and individual rank learning methods.* SIGIR 2009.
15. Anthropic. *Introducing Contextual Retrieval.* 2024.
16. Qwen Team. *Qwen3 Embedding: Advancing Text Embedding and Reranking Through Foundation Models.* 2025.
17. *When Good OCR Is Not Enough: Benchmarking OCR Robustness for RAG.* arXiv:2605.00911, 2026.
18. OpenDataLab. *MinerU: An Open-Source Solution for Precise Document Content Extraction.* 2024; MinerU2.5-Pro, arXiv:2604.04771, 2026.
19. Ouyang, L. et al. *OmniDocBench: Benchmarking Diverse PDF Document Parsing with Comprehensive Annotations.* CVPR 2025.
20. Faysse, M. et al. *ColPali: Efficient Document Retrieval with Vision Language Models.* ICLR 2025.
21. Zheng, K., Han, J. M., Polu, S. *miniF2F: a cross-system benchmark for formal Olympiad-level mathematics.* ICLR 2022.
22. *miniF2F-Lean Revisited: Reviewing Limitations and Charting a Path Forward (miniF2F-v2).* arXiv:2511.03108, 2025. github.com/roozbeh-yz/miniF2F_v2.
23. Tsoukalas, G. et al. *PutnamBench: Evaluating Neural Theorem-Provers on the Putnam Mathematical Competition.* NeurIPS 2024 (Datasets and Benchmarks).
24. Sainz, O. et al. *NLP Evaluation in trouble: On the Need to Measure LLM Data Contamination for each Benchmark.* Findings of EMNLP 2023.
25. Lin, Y. et al. *Goedel-Prover-V2: Scaling Formal Theorem Proving with Scaffolded Data Synthesis and Self-Correction.* 2025. Apache-2.0 weights; GGUF used: `mradermacher/Goedel-Prover-V2-8B-GGUF`.
26. Zhu, H., Clune, J., Avigad, J., Jiang, A. Q., Welleck, S. *Premise Selection for a Lean Hammer.* arXiv:2506.07477, 2025.
27. Goedel-LM. *Lean-workbook-proofs* (29,750 verified proofs, MIT). huggingface.co/datasets/Goedel-LM/Lean-workbook-proofs.

## Appendix

- **A. Prompts** — `configs/agents.yaml` (planner, generator, formalizer, critic, baselines; version 1 for `eval-v1`, version 3 today with the FILL agent and the `prover` and `close` sections) and `configs/ingest.yaml` (enrichment prompts).
- **B. Agent-to-agent traces** — one JSONL per problem in `data/eval/runs/<run_id>/traces/`: per event `agent`, `attempt`, `latency_ms`, `llm_calls`, `tokens`, `request.decision` (validated output), `versions` pin. Examples: `data/eval/runs/eval-v1/traces/` (main experiment, with per-call `cost_usd`) and `data/eval/runs/f5-ablations/traces/`.
- **C. Reproducibility** — `docs/reproduce.md` (index, evaluations, ablations), `docs/install.md`, `configs/versions.yaml` (Lean/Mathlib tag, embedding and reranker models, LLM model chain, temperature, seed, index version).
- **D. Decision log** — `docs/DECISIONS.md` (115 entries with evidence), `docs/eval_report.md` (generated tables, main experiment), `docs/eval_report_dev.md` (development ablation), `docs/qa_ingest_edd3436ca148.md` (ingestion QA), and for the October iteration `docs/PLAN_MEJORA_RAG.md`, `docs/PLAN_REDISENO_PROVER.md`, `docs/eval_report_v2.md`, `docs/eval_report_v3.md`, `docs/Mejorasuwu.md` (summary), `docs/diagramas.md` (diagrams of the current system), `docs/AUDIT_2026-09-23.md` and `docs/PLAN_CORRECCIONES.md` (audit and its correction plan; Phases 1–4 closed).
- **E. Defects found while running the main experiment** (all fixed with a regression test; affected pairs discarded to `data/eval/runs/_invalid/` and re-run): (1) *indentation* — the splicer re-indented tactics that the model had already indented, so `have … := by` blocks nested wrongly and every attempt failed with `unsolved goals` (`DECISIONS.md` #51); (2) *stray `by`* — 8/30 attempts started with a redundant `by` (#53); (3) *embedder race* — six worker threads loaded the embedding model concurrently and left it half-converted to bf16 (#52); (4) *hidden retries* — the HTTP client retried malformed JSON up to 20 times, each time paying the output cap, and counted only the final response: 15 of the first 54 `full` pairs were affected and the API budget ran out twice (#59, #63); (5) *lost accounting* — a malformed output raised out of the graph and the report kept only the last agent's calls (#58, #61); (6) *chunked table build* — LanceDB ≥ 0.25 returns an `AddResult` from `add`, which broke tables above 5,000 rows (#55); (7) *baseline schema* — both baselines shared one output schema with three optional fields, and the model wrote baseline A's proof in the natural-language field, leaving the Lean field empty in 58/60 problems; each baseline now has its own schema with required fields (#65). An eighth issue was economic rather than logical: OpenRouter's price sort ranks by input price, and routed this output-heavy workload to a provider charging 5× more per output token (#62).
