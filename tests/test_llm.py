"""ragsolver.llm — traducción de JSON Schema al subconjunto de Gemini, cadena de
modelos y política de reintentos (LLM-1)."""

from __future__ import annotations

import json

import httpx
import pytest

from ragsolver.agents.schemas import CriticOutput, PlannerOutput, SolutionReport
from ragsolver.llm import LLM, DailyQuotaExhausted, LLMResponse, _retryable, _gemini_schema


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_gemini_schema_resolves_refs_uppercases_types_and_marks_nullable():
    s = _gemini_schema(PlannerOutput.model_json_schema())  # anidado: Subgoal vía $ref
    assert s["properties"]["subgoals"]["items"]["properties"]["statement_nl"] == {"type": "STRING"}
    assert s["properties"]["subgoals"]["maxItems"] == 6
    forbidden = {"$ref", "$defs", "title", "default", "additionalProperties"}
    assert not any(forbidden & set(n.keys()) for n in _walk(s))
    r = _gemini_schema(SolutionReport.model_json_schema())  # int | None, Verdict | None, Literal
    assert r["properties"]["self_assessed_correct"] == {"type": "BOOLEAN", "nullable": True}
    assert r["properties"]["verdict"]["nullable"] is True and r["properties"]["verdict"]["type"] == "OBJECT"
    assert r["properties"]["status"]["enum"] == ["done", "fail", "error"]
    assert _gemini_schema(CriticOutput.model_json_schema())["properties"]["error_type"]["type"] == "STRING"


def test_llm_model_chain_switches_on_daily_quota(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    llm = LLM(config={"provider": "gemini", "model": "a", "fallback_models": ["b"]})
    seen = []

    def fake(model, prompt, schema, images, system, max_tokens):
        seen.append(model)
        if model == "a":
            raise DailyQuotaExhausted(model)
        return LLMResponse(json.dumps({"x": 1}), model, 3, 2)

    monkeypatch.setattr(llm, "_generate_with", fake)
    r = llm.generate("hi", schema={"type": "object"})
    assert r.model == "b" and seen == ["a", "b"] and llm.exhausted == ["a"] and llm.model == "b"
    with pytest.raises(DailyQuotaExhausted):
        monkeypatch.setattr(llm, "_generate_with", lambda *a: (_ for _ in ()).throw(DailyQuotaExhausted("b")))
        llm.generate("hi")


def _req(url="https://x/y"):
    return httpx.Request("POST", url)


def test_retryable_excludes_read_timeout_but_allows_connect_timeout_and_5xx():
    """LLM-1: un timeout de LECTURA puede significar que el proveedor ya generó (y
    cobró) la respuesta — no se reintenta. ConnectTimeout y 429/5xx sí."""
    assert _retryable(httpx.ReadTimeout("timed out reading", request=_req())) is False
    assert _retryable(httpx.ConnectTimeout("timed out connecting", request=_req())) is True
    assert _retryable(httpx.ConnectError("dns failure", request=_req())) is True
    resp503 = httpx.Response(503, request=_req())
    assert _retryable(httpx.HTTPStatusError("x", request=_req(), response=resp503)) is True
    resp400 = httpx.Response(400, request=_req())
    assert _retryable(httpx.HTTPStatusError("x", request=_req(), response=resp400)) is False


def test_generate_with_counts_attempts_and_retries_on_503(monkeypatch):
    """`resp.attempts` cuenta cuántas veces se llamó al backend dentro de una sola
    `generate()`, incluidos los reintentos por 503 (LLM-1)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    llm = LLM(config={"provider": "openai_compatible", "base_url": "https://x", "api_key_env": "OPENROUTER_API_KEY", "model": "m"})
    calls = {"n": 0}
    resp503 = httpx.Response(503, request=_req())

    def flaky(model, prompt, schema, images, system, max_tokens):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.HTTPStatusError("x", request=_req(), response=resp503)
        return LLMResponse("{}", model, 1, 1)

    monkeypatch.setattr(llm, "_openai", flaky)
    monkeypatch.setattr("ragsolver.llm._wait", lambda retry_state: 0.0)  # no dormir 3s de verdad en el test
    resp = llm._generate_with("m", "hi", None, (), None, None)
    assert calls["n"] == 3 and resp.attempts == 3


def test_generate_with_does_not_retry_a_read_timeout(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    llm = LLM(config={"provider": "openai_compatible", "base_url": "https://x", "api_key_env": "OPENROUTER_API_KEY", "model": "m"})
    calls = {"n": 0}

    def always_times_out(model, prompt, schema, images, system, max_tokens):
        calls["n"] += 1
        raise httpx.ReadTimeout("timed out reading", request=_req())

    monkeypatch.setattr(llm, "_openai", always_times_out)
    with pytest.raises(httpx.ReadTimeout):
        llm._generate_with("m", "hi", None, (), None, None)
    assert calls["n"] == 1  # un solo intento: no se reintenta un timeout de lectura


@pytest.mark.parametrize("status,failures,expected_calls,ok", [(429, 8, 9, True), (429, 10, 10, False), (503, 4, 4, False)])
def test_429_gets_more_retries_than_billable_errors(monkeypatch, status, failures, expected_calls, ok):
    """DECISIONS #104: un 429 es un rechazo que no se cobra -> hasta 10 intentos; 5xx/transporte siguen en 4."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    llm = LLM(config={"provider": "openai_compatible", "base_url": "https://x", "api_key_env": "OPENROUTER_API_KEY", "model": "m"})
    calls = {"n": 0}

    def flaky(model, prompt, schema, images, system, max_tokens):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise httpx.HTTPStatusError("x", request=_req(), response=httpx.Response(status, request=_req()))
        return LLMResponse("{}", model, 1, 1)

    monkeypatch.setattr(llm, "_openai", flaky)
    monkeypatch.setattr("ragsolver.llm._wait", lambda retry_state: 0.0)
    if ok:
        assert llm._generate_with("m", "hi", None, (), None, None).attempts == expected_calls
    else:
        with pytest.raises(httpx.HTTPStatusError):
            llm._generate_with("m", "hi", None, (), None, None)
        assert calls["n"] == expected_calls
