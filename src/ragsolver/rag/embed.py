"""Wrapper de embeddings (Qwen3-Embedding) usado por ambos corpus.

docs/PLAN_RAG.md §9.1: un solo modelo para Mathlib e informal. Documentos se
embeben sin instrucción; las consultas llevan instrucción (recomendación del
model card de Qwen3-Embedding — mejora retrieval sin tocar el índice).

Backends (`RAGSOLVER_EMBED_BACKEND`):
- `torch` (default): sentence-transformers en CPU (bf16), ~5 textos/s.
- `llama`: `llama-server` (llama.cpp, Vulkan) corriendo del lado de Windows con el
  GGUF f16 del mismo modelo — usa la RX 9060 XT esquivando el bug de ROCm en WSL2
  (docs/DECISIONS.md #48): ~48 textos/s, vectores idénticos (cos ≥ 0.9998).
  URL en `RAGSOLVER_EMBED_URL` (default: gateway de WSL, puerto 8081).
"""

from __future__ import annotations

import os
import threading
from functools import lru_cache

# Un solo hilo carga y usa el modelo de torch: cargas concurrentes (lru_cache no lo impide) duplican
# la RAM y dejan el modelo a medio convertir a bf16 ("expected m1 and m2 to have the same dtype")
_TORCH_LOCK = threading.Lock()

QUERY_INSTRUCTION = "Instruct: Given a mathematical query, retrieve relevant Mathlib declarations or worked problems.\nQuery: "


@lru_cache(maxsize=1)
def _get_model(model_name: str):
    # Import perezoso: torch/sentence-transformers son pesados y no hacen falta
    # para el resto del módulo (ingesta, esquema, índice sin embeddings).
    import torch
    from sentence_transformers import SentenceTransformer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    # ponytail: sin flash_attention_2 (CUDA-only, no compila en ROCm —
    # docs/PLAN_RAG.md §9.4); se usa la implementación de atención por defecto.
    # bfloat16 en CPU: mitad de RAM (1.2 GB vs 2.4 GB para 0.6B); la máquina tiene 7.5 GB
    # y el pipeline de Fase 4 convive con el REPL de Lean (docs/DECISIONS.md #40).
    dtype = torch.bfloat16 if os.environ.get("RAGSOLVER_EMBED_BF16", "1") == "1" else torch.float32
    return SentenceTransformer(model_name, device=device, model_kwargs={"torch_dtype": dtype})


def _backend() -> str:
    return os.environ.get("RAGSOLVER_EMBED_BACKEND", "torch")


def _llama_url() -> str:
    url = os.environ.get("RAGSOLVER_EMBED_URL")
    if url:
        return url
    import subprocess

    gw = subprocess.run(["sh", "-c", "ip route show default | awk '{print $3}'"], capture_output=True, text=True).stdout.strip() or "127.0.0.1"
    return f"http://{gw}:8081/v1/embeddings"


def _llama_embed(texts: list[str], batch_size: int = 64, workers: int = 8) -> list[list[float]]:
    """Lotes concurrentes contra llama-server (8 slots). Textos truncados a 6000 caracteres
    (ventana por slot de 2048 tokens); el servidor normaliza L2."""
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    url = _llama_url()
    client = httpx.Client(timeout=900)
    batches = [[t[:6000] for t in texts[i : i + batch_size]] for i in range(0, len(texts), batch_size)]

    def one(batch):
        for attempt in range(5):
            try:
                r = client.post(url, json={"input": batch})
                r.raise_for_status()
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                return [d["embedding"] for d in data]
            except Exception:
                if attempt == 4:
                    raise
        return []

    with ThreadPoolExecutor(workers) as ex:
        out = []
        for vecs in ex.map(one, batches):
            out.extend(vecs)
    return out


def embed_documents(texts: list[str], model_name: str, batch_size: int = 32) -> list[list[float]]:
    """Embebe texto de documento (sin instrucción). Usado en la ingesta (offline)."""
    if _backend() == "llama":
        return _llama_embed(texts)
    with _TORCH_LOCK:
        model = _get_model(model_name)
        vectors = model.encode(texts, batch_size=batch_size, show_progress_bar=False, normalize_embeddings=True)
    return vectors.tolist()


def embed_queries(texts: list[str], model_name: str, batch_size: int = 8, instruction: str | None = None) -> list[list[float]]:
    """Embebe texto de consulta (con instrucción). Usado por el retriever (online).

    `instruction`: texto tras "Instruct:" (configs/retrieval.yaml `query_instructions`);
    por defecto la instrucción con la que se midió la Fase 1.
    """
    prefix = f"Instruct: {instruction}\nQuery: " if instruction else QUERY_INSTRUCTION
    with _QUERY_CACHE_LOCK:
        found = {t: _QUERY_CACHE.get((prefix, t, model_name)) for t in texts}
    missing = [t for t, v in found.items() if v is None]
    if missing:
        if _backend() == "llama":
            vectors_list = _llama_embed([prefix + t for t in missing])
        else:
            with _TORCH_LOCK:
                model = _get_model(model_name)
                vectors_list = model.encode([prefix + t for t in missing], batch_size=batch_size, show_progress_bar=False, normalize_embeddings=True).tolist()
        with _QUERY_CACHE_LOCK:  # BUG-6: dict mutado desde varios hilos (--workers) sin lock podía romper la evicción a mitad de iteración
            for t, v in zip(missing, vectors_list):
                found[t] = v
                _QUERY_CACHE[(prefix, t, model_name)] = v
            while len(_QUERY_CACHE) > _QUERY_CACHE_MAX:  # ponytail: LRU aproximado (FIFO)
                _QUERY_CACHE.pop(next(iter(_QUERY_CACHE)), None)
    return [found[t] for t in texts]


# Caché de embeddings de consulta (docs/PLAN_RAG.md Fase 3, tarea 1): el orquestador
# repite consultas entre intentos (mismo subobjetivo, re-recuperación tras error).
_QUERY_CACHE: dict[tuple[str, str, str], list[float]] = {}
_QUERY_CACHE_MAX = 512
_QUERY_CACHE_LOCK = threading.Lock()
