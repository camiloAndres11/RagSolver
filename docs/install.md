# Instalación

Probado en WSL2 (Ubuntu 26.04), Python 3.12, CPU (7.5 GB RAM). La GPU AMD (RX 9060 XT, 16 GB) se usa desde Windows vía llama.cpp/Vulkan para dos cosas: los embeddings (`RAGSOLVER_EMBED_BACKEND=llama`, `DECISIONS.md` #48) y el **demostrador local** Goedel-Prover-V2-8B (§7, #112). ROCm dentro de WSL2 sigue sin funcionar para esta tarjeta (#11-12), pero no hace falta. Sin la GPU el sistema arranca igual: pon `prover.enabled: false` en `configs/agents.yaml` y vuelve al flujo por agentes (resultados de eval-v2, mucho más bajos; `Mejorasuwu.md`).

## 1. Python y dependencias

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv sync --extra dev --extra ingest --extra demo    # SIEMPRE con los extras: `uv sync` a secas deja fuera pytest, MinerU y Gradio (#29)
```

## 2. Lean 4 + Mathlib (tag fijada)

```bash
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh
source "$HOME/.elan/env" && elan default leanprover/lean4:v4.28.0-rc1
cd lean_project && lake exe cache get && lake build   # oleans precompilados de Mathlib
```

Recomendado en WSL2 (el repo en `/mnt/c` es 10× más lento para cargar Mathlib): copia de la caché en ext4, que el verificador detecta solo (`RAGSOLVER_LEAN_PROJECT` la sobreescribe):

```bash
mkdir -p ~/ragsolver_cache/lean_project && cp lean_project/{lakefile.toml,lake-manifest.json,lean-toolchain,LeanProject.lean} ~/ragsolver_cache/lean_project/
cd ~/ragsolver_cache/lean_project && lake exe cache get      # ~2 min, 6.9 GB
```

## 3. Claves y proveedor LLM

`.env` en la raíz (ignorado por git):

```
OPENROUTER_API_KEY=...      # agentes (Fase 4-5): DeepSeek V4 Flash vía OpenRouter; conviene fijar un tope de gasto en la key
GEMINI_API_KEY=...          # enriquecimiento VLM de la ingesta (Fase 2): tier gratis de AI Studio, 20 req/día por modelo (#21)
```

Proveedor y cadena de modelos en `configs/versions.yaml` (`llm`, `vlm_enrichment`). La sección `llm` ya apunta a OpenRouter (`provider: openai_compatible`, `base_url`, `api_key_env`, `model`) con `reasoning_effort: "off"`, `max_tokens` y `provider_routing` (tope de precio por M tokens; ver `DECISIONS.md` #62-#64). Cada llamada registra su costo exacto en la traza (`request.cost_usd`).

## 4. MinerU (sólo para ingerir PDFs nuevos)

`mineru-kit models download --tier standard` descarga los modelos locales; usar **modo local** (`parse_server.local.mode`), nunca el remoto por defecto (#4). La salida (`*.json` del esquema `docvortex.middle`) va en `data/parsed/<doc_id>/mineru/`.

## 5. Variables de entorno

| Variable | Para qué | Por defecto |
|---|---|---|
| `OPENROUTER_API_KEY`, `GEMINI_API_KEY` | claves (ver §3) | — |
| `RAGSOLVER_MATHLIB_TABLE` | qué tabla LanceDB sirve el corpus `mathlib` (`mathlib` muestra / `mathlib_full` completo) | `mathlib` |
| `RAGSOLVER_EMBED_BACKEND` | `torch` (CPU) o `llama` (GPU vía `llama-server`, ver §6) | `torch` |
| `RAGSOLVER_EMBED_URL` | URL del `llama-server` cuando `RAGSOLVER_EMBED_BACKEND=llama` | gateway de WSL, puerto 8081 |
| `RAGSOLVER_PROVER_URL` | URL del `llama-server` del demostrador (§7) | gateway de WSL, puerto 8082 |
| `RAGSOLVER_EMBED_BF16` | `1` = bf16 (mitad de RAM), `0` = fp32 | `1` |
| `RAGSOLVER_LEAN_PROJECT` | ruta del proyecto Lean del verificador (si no, usa la caché en ext4 o `lean_project/` del repo) | detección automática |
| `RAGSOLVER_DB` | ruta del índice LanceDB que usa la demo | `data/index/lancedb` |
| `RAGSOLVER_HF_DATASET` | dataset de HF del que la demo descarga el índice si no lo encuentra local | — |
| `GRADIO_SERVER_NAME` | host de la demo; sólo `127.0.0.1`/`localhost` habilita la pestaña "Resolver" (SEC-1: Lean sin sandbox) | `127.0.0.1` |
| `HF_TOKEN` | publicación en HF (`scripts/publish_hf.py`, `publish_space.py`) | — |

## 6. GPU vía llama.cpp/Vulkan (opcional; embedder y reranker)

`tools/llama-vulkan/llama-server.exe` (Windows) sirve el GGUF por HTTP; WSL lo consume con `RAGSOLVER_EMBED_BACKEND=llama` (`docs/DECISIONS.md` #48). **SEC-3**: `--host 0.0.0.0` expone el servidor a toda la LAN sin autenticación mientras corre — usar la IP de WSL en vez de `0.0.0.0` cuando sea posible, y apagarlo al terminar (`scripts/embed_full_mathlib.sh`).

## 7. Demostrador local en la GPU (Goedel-Prover-V2-8B)

Es la etapa 0 del sistema (`docs/PLAN_REDISENO_PROVER.md`): resolvió 21/30 en eval-v3 sin una llamada a la API. El modelo Q6_K pesa 6.7 GB; con 8 slots de 12 288 tokens cabe en los 16 GB de la RX 9060 XT.

1. **Modelo** (Apache-2.0): GGUF `mradermacher/Goedel-Prover-V2-8B-GGUF`, cuantización **Q6_K** (6.7 GB), en `tools/models/Goedel-Prover-V2-8B.Q6_K.gguf` (no versionado). La Q8_0 también funciona (36 tok/s por muestra en vez de 45, 4 slots).
2. **Servidor**, desde WSL en la raíz del repo; escucha **sólo** en la red virtual WSL↔Windows, no en la LAN (SEC-3):
   ```bash
   ./tools/llama-vulkan/llama-server.exe -m tools/models/Goedel-Prover-V2-8B.Q6_K.gguf -ngl 99 --host 172.31.112.1 --port 8082 -c 98304 --parallel 8 -fa on -ctk q8_0 -ctv q8_0 --jinja
   ```
   La IP es la del gateway de WSL (`ip route show default`); si difiere, pásala en `--host` y define `RAGSOLVER_PROVER_URL`.
3. **Apagar al terminar** (libera la GPU): `pgrep -f "llama-server[.]exe" | xargs kill`.
4. Parámetros del demostrador en `configs/agents.yaml: prover` (`k_root`, `k_goal`, `max_goals`, `max_tokens`, `correction_rounds`, `rag_names`). Las muestras se guardan en `data/eval/prover_cache.jsonl`: repetir una corrida no vuelve a gastar GPU.

El servidor de embeddings (§6, puerto 8081) y el del demostrador (8082) son procesos distintos; con 16 GB de VRAM caben juntos, pero la GPU satura en ~70 tok/s agregados.

## 8. Comprobación

```bash
uv run pytest -q --deselect tests/test_retriever_integration.py -m "not slow"   # 156 tests sin cargar modelos ni Lean real
uv run pytest -q -m slow      # 11 tests con Lean real (~1-2 min, requiere el paso 2): verificador endurecido (SEC-1/VER-1), goals/`step`/`signatures`, términos aceptados
ragsolver search "gcd is commutative" --corpus mathlib --k 5       # carga el embedder (~2 min en /mnt/c)
```

15 tests (ingesta del PDF: `test_ingest_pdf.py`, `test_mineru_blocks.py`, parte de `test_parse_structure.py`, `test_figures.py`) se saltan solos si no hay una corrida previa de ingesta en `data/parsed/edd3436ca148/` (no versionada en git, licencia del PDF de ejemplo — `docs/AUDIT_2026-09-23.md` SEC-2).

## Memoria

Pipeline de agentes ≈ 4.2 GB de RAM (embedder bf16 1.8 + REPL Lean 1.9 + resto). No correr dos procesos con modelos a la vez en 7.5 GB; `RAGSOLVER_EMBED_BF16=0` vuelve a fp32 (el doble de RAM). El demostrador local vive en la VRAM de la GPU (Windows), no en la RAM de WSL.
