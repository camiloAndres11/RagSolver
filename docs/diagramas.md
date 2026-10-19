# Diagramas del sistema actual (RagSolver, 2026-10-18)

Estado: `prompt_version: "3"`, demostrador local activado (`prover.enabled: true`), `goal_mode: true`. Fuente: `src/ragsolver/orchestrator/graph.py` (aristas de `build`), `prover.py`, `lean/verify.py`, `configs/agents.yaml`. Texto de apoyo: `architecture.md` (componentes y contratos) y `Mejorasuwu.md` (qué cambió y por qué). Resultado medido: 21/30 en eval-v3 frente a 8/30 del sistema anterior (`eval_report_v3.md`).

Los diagramas son Mermaid: se ven en GitHub, VS Code (extensión Markdown Preview Mermaid) y en la mayoría de visores de Markdown.

## 1. Vista general

Quién habla con quién. Todo lo que cuesta dinero (API) está en el borde derecho; el resto es local.

```mermaid
flowchart LR
  USER["Problema\nenunciado informal + theorem Lean ... := by sorry"] --> ORQ

  subgraph LOCAL["Máquina local — WSL2"]
    ORQ["Orquestador LangGraph\norchestrator/graph.py"]
    RET["Retriever 0 LLM\nrag/retrieve.py + rag/premises.py"]
    IDX[("LanceDB\nmathlib_full 278 160\ninformal · figures")]
    PRIOR[("premise_prior.jsonl\n778 lemas validados")]
    LEAN["Verificador\nlean/verify.py\nREPL lean-interact\nMathlib v4.28.0-rc1"]
    CACHE[("prover_cache.jsonl\nmuestras del demostrador")]
    TRZ[("Trazas JSONL\nun archivo por par config-problema")]
    ORQ --> RET
    RET --> IDX
    RET --> PRIOR
    ORQ --> LEAN
    ORQ --> TRZ
    ORQ --> CACHE
  end

  subgraph GPU["GPU local — Windows, RX 9060 XT 16 GB, Vulkan"]
    PROVER["llama-server\nGoedel-Prover-V2-8B Q6_K\n8 slots · 172.31.112.1:8082"]
    EMB["llama-server\nQwen3-Embedding-0.6B\npuerto 8081"]
  end

  subgraph API["API de pago — OpenRouter"]
    LLM["DeepSeek V4 Flash\nPlanificador · Generador\nAutoformalizador · FILL · Crítico"]
  end

  ORQ -- "Etapa 0 y goals abiertos, US$0" --> PROVER
  RET -- "embeddings de consulta" --> EMB
  ORQ -- "sólo si la etapa 0 falla" --> LLM
  ORQ --> REP["SolutionReport\nestado · lean_code · citas\nsolved_by · llamadas · tokens · costo"]
```

## 2. Grafo del orquestador (aristas reales)

Nodos y condiciones tal como están en `Orchestrator.build`. Un nodo que lanza una excepción termina en `status = error` sin perder la contabilidad (`_guard`, #84).

```mermaid
flowchart TD
  S([START]) --> PROVE

  PROVE["PROVE\netapa 0: demostrador local\n0 API"]
  PROVE -- "resolvió: solved_by = prover_root" --> VERIFY
  PROVE -- "no resolvió" --> PLAN
  PROVE -- "error" --> E([FIN: error])

  PLAN["PLAN\nejemplares informales 0 LLM\n+ Planificador 1 llamada"]
  PLAN --> RETRIEVE
  PLAN -- "fail / error" --> F([FIN: fail])

  RETRIEVE["RETRIEVE\nre-recuperación modo error\nsi el Crítico dejó identificadores\n0 LLM"]
  RETRIEVE --> GENERATE

  GENERATE["GENERATE\nboceto en lenguaje natural\n1 llamada"]
  GENERATE --> FORMALIZE
  GENERATE -- "presupuesto agotado" --> F

  FORMALIZE["FORMALIZE\nprueba con pasos have\n1 llamada\nskeletonize: cada have pasa a sorry"]
  FORMALIZE -- "goal_mode" --> CLOSE
  FORMALIZE -- "modo rag_nl: prueba entera" --> VERIFY

  CLOSE["CLOSE\npor goal: candidato del modelo,\nbatería de tácticas con premisas,\ndemostrador local\n0 LLM"]
  CLOSE -- "todos los goals cerrados" --> VERIFY
  CLOSE -- "quedan goals abiertos" --> FILL
  CLOSE -- "el esqueleto no elabora" --> CRITIQUE

  FILL["FILL\ngoals abiertos con su goal exacto,\npremisas con firma y último error\n1 llamada"]
  FILL --> VERIFY

  VERIFY["VERIFY\nvalidador de tácticas → kernel → axiomas\n0 LLM"]
  VERIFY -- "ok" --> DONE([FIN: done])
  VERIFY -- "error" --> CRITIQUE

  CRITIQUE["CRITIQUE\nerror_type + acción\n1 llamada; ilegible → reintento"]
  CRITIQUE -- "retry" --> GENERATE
  CRITIQUE -- "retry + lema faltante" --> RETRIEVE
  CRITIQUE -- "replan, con feedback y goals probados" --> PLAN
  CRITIQUE -- "abandon · k_max · presupuesto 20" --> F
```

Presupuesto de llamadas (RNF-3, ≤ 20): Planificador 1 + por intento Generador 1, Autoformalizador 1, FILL 0-1, Crítico 1 (con `k_max = 3` el peor caso ronda 13). PROVE, CLOSE, RETRIEVE y VERIFY cuestan 0 llamadas a la API.

## 3. Etapa 0: demostrador local (`prover.prove`)

```mermaid
flowchart TD
  A["Teorema completo\nen el prompt nativo de Goedel-Prover"] --> B["Lote de 4 muestras en paralelo\nseed 13 + inicio del lote\ncaché de disco por mensajes+semilla"]
  B --> C{"¿se extraen\ntácticas del teorema pedido?"}
  C -- no --> N
  C -- sí --> D["Se empalman en el enunciado\nDEL PROBLEMA\nel modelo no puede debilitarlo"]
  D --> E{"LeanChecker.check\nvalidador + kernel + axiomas"}
  E -- "ok" --> WIN(["Resuelto\nsolved_in = sample"])
  E -- "falla" --> N["Guardar candidato y errores"]
  N --> G{"¿faltan muestras?\nhasta k_root = 8"}
  G -- sí --> B
  G -- no --> H["Ronda de autocorrección\nsobre las 2 mejores muestras"]
  H --> I["Errores de Lean\n+ sugerencias de nombres del RAG"]
  I --> J["1 muestra nueva con el historial"]
  J --> K{"check"}
  K -- "ok" --> WIN2(["Resuelto\nsolved_in = correction1"])
  K -- "falla" --> LOSE(["No resuelve\nsigue PLAN"])
```

### 3.1 Rol del RAG dentro de la autocorrección

El demostrador se entrenó con Lean 4.9; el verificador es v4.28, así que a veces usa lemas renombrados o borrados.

```mermaid
sequenceDiagram
  participant P as Demostrador
  participant L as Lean REPL
  participant R as Retriever modo error
  P->>L: pruebas candidatas
  L-->>P: unknown identifier "Nat.foo_bar"
  Note over P,L: autocorrección
  P->>R: nombre desconocido
  R->>R: búsqueda léxica + densa en mathlib_full
  R->>L: #check de cada candidato
  L-->>R: sólo los que existen, con su firma
  R-->>P: "Nat.foo_baz : ..." existe; "Nat.foo_bar" no existe
  P->>L: prueba corregida
  L-->>P: veredicto
  Note over R: las sugerencias entran como citas M*;<br/>used_in_proof mide si el término aceptado las usó
```

## 4. CLOSE: un goal, de menos a más costoso

Cada paso es una llamada a `LeanChecker.step` sobre el `proof_state` del goal (sin re-elaborar el teorema). Cada táctica pasa por `find_forbidden_tactic`.

```mermaid
flowchart TD
  G["Goal de Lean\ndevuelto por el REPL en un sorry"] --> R0{"¿ya probado\nen un intento previo?"}
  R0 -- "sí y sigue cerrando" --> OK
  R0 -- no --> C0["1 · Candidato del modelo\nel bloque de ese have"]
  C0 -- cierra --> OK
  C0 -- no --> P["2 · Premisas para ESE goal\ncanal A: prior filtrado por símbolos del goal\ncanal C: apagado"]
  P --> T1["grind +lax con premisas"]
  T1 -- cierra --> OK
  T1 -- no --> T2["nlinarith con hints\nsq_nonneg a b ... generados del goal"]
  T2 -- cierra --> OK
  T2 -- no --> T3["grind +suggestions\ncanal B: selección nativa de Lean"]
  T3 -- cierra --> OK
  T3 -- no --> T4["try?\nse usa su script concreto,\nnunca el ? "]
  T4 -- cierra --> OK
  T4 -- no --> PR["3 · Demostrador local\ngoal como lema autónomo\nk_goal = 2 · máx. 2 goals por intento\nrename_i para nombres inaccesibles"]
  PR -- "cierra EN SU CONTEXTO" --> OK
  PR -- no --> OPEN["Goal abierto → FILL"]
  OK(["Goal cerrado\nse guarda en proved con su bloque"])
```

`no_rag` corre la misma escalera sin premisas, sin hints y sin `grind +suggestions` (columna `plain` de `close.tactics`), de modo que la ablación aísla el efecto del RAG del de la automatización de Lean.

## 5. Recuperación de premisas por goal

```mermaid
flowchart LR
  GOAL["Goal pretty-printed"] --> SYM["Símbolos del goal\nreales ℝ ℕ ℤ · sqrt · % · Finset.sum · abs ..."]
  PRI[("Prior de competición\n29 750 pruebas de Lean Workbook\n778 lemas validados con #check")] --> A
  SYM --> A["Canal A\nlemas anclados a esos símbolos\n+ álgebra ordenada siempre\nrecall@16 por goal 0.327"]
  GOAL --> B["Canal B\nselección nativa de Lean\nse consume con grind +suggestions\nen CLOSE"]
  GOAL -. "apagado: recall@16 0.028\ny hunde la fusión" .-> C["Canal C\níndice denso con el goal"]
  A --> TOP["Top-16 premisas con firma\ny cita M*"]
  TOP --> BAT["Batería de tácticas\ny prompt de FILL"]
  B --> BAT
```

Contrasto con el sistema original: consulta en lenguaje natural sobre el índice denso → recall 0.006 por problema; consulta con el goal sobre el prior → 0.229 por problema.

## 6. Verificación: ¿qué significa "resuelto"?

```mermaid
flowchart TD
  IN["Tácticas generadas\npor un LLM o por el demostrador"] --> L1
  L1["Nivel 1 · find_forbidden_tactic\nantes de tocar Lean\nrechaza #comandos, metaprogramación,\ndeclaraciones, set_option, import,\nsorry, native_decide, decide +native, IO.*"]
  L1 -- rechazada --> NO(["ok = false\nerror legible para el Crítico"])
  L1 -- limpia --> SP["Empalme en el enunciado DEL PROBLEMA\ntheorem_block / build_file\núnica fuente para REPL y reporte"]
  SP --> L2["Nivel 2 · Kernel de Lean\nREPL con import Mathlib\nmaxHeartbeats fijado por el sistema"]
  L2 -- "errores o sorry" --> NO
  L2 -- "sin errores" --> L3["Nivel 3 · #print axioms\nsólo propext, Classical.choice, Quot.sound"]
  L3 -- "otro axioma\nLean.ofReduceBool, sorryAx ..." --> NO
  L3 -- "subconjunto estándar" --> YES(["ok = true\nlean_code = lo verificado\nused_constants = constantes del término"])
```

No cubierto todavía (Fase 10 de `PLAN_CORRECCIONES.md`): sandbox de sistema operativo; el servidor del demostrador sólo escucha en la red virtual WSL↔Windows.

## 7. Secuencia de un problema difícil

El caso en que la etapa 0 no basta y entran los goals y los agentes.

```mermaid
sequenceDiagram
  participant O as Orquestador
  participant D as Demostrador GPU
  participant V as Lean REPL
  participant R as Retriever
  participant A as Agentes API
  O->>D: teorema completo, hasta 8 muestras
  D-->>O: candidatos
  O->>V: check de cada candidato
  V-->>O: todos rechazados
  O->>D: autocorrección con errores y nombres del RAG
  D-->>O: 1 candidato corregido
  O->>V: check
  V-->>O: rechazado
  O->>R: ejemplares informales, 0 LLM
  O->>A: Planificador
  A-->>O: estrategia y subobjetivos
  O->>A: Generador
  A-->>O: boceto
  O->>A: Autoformalizador
  A-->>O: prueba con pasos have
  O->>V: elaborar el esqueleto con sorry
  V-->>O: un goal y un proof_state por sorry
  loop por cada goal
    O->>V: candidato del modelo y batería con premisas
    O->>R: premisas para ese goal
    O->>D: si la batería no cierra, goal como lema
  end
  O->>A: FILL con los goals abiertos
  A-->>O: bloques de tácticas
  O->>V: step por goal, luego check del archivo completo
  alt ok
    V-->>O: aceptado, axiomas estándar
  else rechazado
    O->>A: Crítico
    A-->>O: retry, replan o abandon
    Note over O: los goals ya probados se conservan<br/>entre intentos y replans
  end
```

## 8. Configuraciones de evaluación

```mermaid
flowchart LR
  subgraph SIN["Sin LLM de pago"]
    BA["baseline_auto\nsólo la batería CLOSE\nsin premisas sobre el goal raíz"]
    BP["baseline_prover\nsólo el demostrador local\n+ autocorrección sin RAG"]
  end
  subgraph LLM1["Un LLM"]
    BLA["baseline_a\nprueba Lean directa"]
    BLB["baseline_b\ncadena de razonamiento sin verificar"]
  end
  subgraph SYS["Sistema completo"]
    FULL["full\netapa 0 + agentes + CLOSE + RAG"]
    NR["no_rag\nmisma batería y demostrador,\nsin premisas ni sugerencias"]
    NC["no_critic\nk_max = 1"]
    RN["rag_nl\nel flujo de eval-v1\nsin goal_mode"]
  end
```

| Configuración | LLM de pago | Qué responde |
|---|---|---|
| `full` | sí (respaldo) | sistema completo |
| `baseline_prover` | no | cuánto resuelve el demostrador solo |
| `full` − `baseline_prover` | — | qué suman agentes y RAG sobre el demostrador (0 en eval-v3) |
| `full` − `no_rag` | — | aporte del RAG con la automatización fija |
| `full` − `baseline_auto` | — | aporte de los agentes sobre Lean solo |
| `baseline_a` / `baseline_b` | sí, 1 llamada | líneas base de la propuesta (§8.2) |

## 9. Datos y construcción offline

```mermaid
flowchart TD
  subgraph FORMAL["Corpus A · Mathlib"]
    LS["LeanSearch v2 JSONL\n310 579 declaraciones"] --> NORM["ingest_mathlib\n278 160 chunks"]
    NORM --> E1["Embeddings Qwen3-0.6B\nGPU Vulkan, 4.5 h"]
    E1 --> T1[("mathlib_full\nvector + FTS + ANN IVF_SQ")]
    NORM --> SM[("mathlib\nmuestra de 3 903 filas")]
  end
  subgraph PRIORB["Prior de competición"]
    LW["Lean Workbook proofs\n29 750 pruebas, MIT"] --> CNT["frecuencia por lema"]
    CNT --> VAL["validación con #check\nen el Lean fijado"]
    VAL --> PJ[("premise_prior.jsonl\n778 lemas")]
  end
  subgraph INFORMAL["Corpus B · PDF de olimpiadas"]
    PDF["PDF"] --> MIN["MinerU local"]
    MIN --> GAP["huecos de layout\ncapa de texto vs bloques"]
    GAP --> VLM["VLM: huecos · fórmulas · figuras · resúmenes\ncaché por llamada"]
    VLM --> CARDS["100 problem cards + 53 figuras"]
    CARDS --> T2[("informal · figures")]
  end
  subgraph EVAL["Evaluación"]
    MQ["MathlibQR / MPR"]
    GOLD["Premisas gold por problema y por goal\nvalidadas contra Lean"]
    PB["miniF2F-v2\neval_v1: 60 problemas, 3 áreas × 3 niveles"]
  end
```

## 10. Citas: qué cuenta como "ayudó"

```mermaid
flowchart LR
  H["Hit recuperado\ncitation_id estable por chunk"] --> CON["consultada\nentró al prompt o a la batería"]
  CON --> SK{"¿la declaró el Generador\nen used_citations?"}
  SK -- sí --> US["used_in_sketch"]
  CON --> PF{"¿su decl_name está entre las\nconstantes del TÉRMINO aceptado?\nVerdict.used_constants"}
  PF -- sí --> UP["used_in_proof\nverificada por el kernel"]
```

En eval-v2 el kernel usó 1 premisa recuperada; en eval-v3, 0 de las 8 sugerencias de nombres.

## 11. Dónde está cada cosa

| Qué | Dónde |
|---|---|
| Grafo, CLOSE, FILL, replan, `solved_by` | `src/ragsolver/orchestrator/graph.py` |
| Demostrador local, `goal_to_theorem`, caché | `src/ragsolver/prover.py` |
| Verificador, `goals`, `step`, `skeletonize`, `fill_sorries` | `src/ragsolver/lean/verify.py` |
| Premisas por goal | `src/ragsolver/rag/premises.py`, `data/corpus/premise_prior.jsonl` |
| Retriever | `src/ragsolver/rag/retrieve.py` |
| Ejecución de evaluaciones, líneas base | `src/ragsolver/orchestrator/run.py` |
| Prompts, batería, demostrador, configuraciones | `configs/agents.yaml` |
| Versiones fijadas | `configs/versions.yaml` |
