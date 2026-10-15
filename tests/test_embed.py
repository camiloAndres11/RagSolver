"""BUG-6 — la caché de embeddings de consulta (`rag/embed.py::embed_queries`) se
lee y escribe desde varios hilos (`orchestrator/run.py --workers N`); sin lock,
la evicción FIFO podía romper a mitad de iteración con `RuntimeError: dictionary
changed size during iteration`."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ragsolver.rag import embed as E


def test_embed_queries_is_safe_under_concurrent_access(monkeypatch):
    monkeypatch.setattr(E, "_backend", lambda: "llama")
    monkeypatch.setattr(E, "_QUERY_CACHE", {})
    monkeypatch.setattr(E, "_QUERY_CACHE_MAX", 8)  # fuerza evicciones frecuentes durante el test

    def fake_llama_embed(texts, **kw):
        return [[float(len(t))] for t in texts]

    monkeypatch.setattr(E, "_llama_embed", fake_llama_embed)

    def worker(i: int) -> list[float]:
        text = f"query {i % 20}"  # 20 textos distintos, más de _QUERY_CACHE_MAX: fuerza reuso + evicción
        return E.embed_queries([text], model_name="m")[0]

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(worker, range(400)))  # sin lock, esto suele lanzar RuntimeError antes de terminar

    for i, vec in enumerate(results):
        expected = float(len(E.QUERY_INSTRUCTION + f"query {i % 20}"))  # la clave de caché incluye el prefijo de instrucción
        assert vec == [expected], f"worker {i}: se coló el resultado de otra consulta (carrera en la caché)"
