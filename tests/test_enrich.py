"""Lógica del enriquecedor que no depende de la red: cadena de modelos por cuota
diaria, caché con modelo, y guardas de aceptación de la QA de fórmulas."""

from __future__ import annotations

import json

import pytest

from ragsolver.rag import enrich
from ragsolver.rag.enrich import DailyQuotaExhausted, Enricher


@pytest.fixture
def enricher(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    e = Enricher(tmp_path / "enrich")
    e.llm.models = ["model-a", "model-b", "model-c"]
    e.llm._model_idx = 0
    return e


def _fake(enricher, monkeypatch, fn):
    """Reemplaza la llamada HTTP: fn(model, prompt, schema, images, system, max_tokens) -> texto."""
    from ragsolver.llm import LLMResponse

    monkeypatch.setattr(enricher.llm, "_generate_with", lambda model, prompt, schema, images, system, max_tokens: LLMResponse(fn(model, prompt, schema), model))


def test_daily_quota_moves_to_next_model_and_records_it(enricher, monkeypatch):
    seen = []

    def fake(model, prompt, schema):
        seen.append(model)
        if model == "model-a":
            raise DailyQuotaExhausted(model)
        return json.dumps({"first_line": "x = 1"})

    _fake(enricher, monkeypatch, fake)
    assert enricher.split_solution(55, "texto", "p54", "p55") == "x = 1"
    assert seen == ["model-a", "model-b"]
    assert enricher.exhausted == ["model-a"] and enricher.model == "model-b"
    # la caché guarda el modelo que produjo el resultado y se reutiliza sin llamar
    rec = json.loads((enricher.cache_dir / "split.jsonl").read_text().splitlines()[0])
    assert rec["model"] == "model-b"
    assert enricher.split_solution(55, "texto", "p54", "p55") == "x = 1" and seen == ["model-a", "model-b"]


def test_all_models_exhausted_raises(enricher, monkeypatch):
    _fake(enricher, monkeypatch, lambda m, p, s: (_ for _ in ()).throw(DailyQuotaExhausted(m)))
    with pytest.raises(DailyQuotaExhausted):
        enricher.transcribe_region(_png(enricher.cache_dir))
    assert enricher.exhausted == ["model-a", "model-b"]


def test_qa_formulas_guards(enricher, monkeypatch):
    """Casos reales del PDF de ejemplo (docs/DECISIONS.md #19): qué acepta y qué no."""
    cases = [  # (original, propuesta, inline, se_acepta)
        ("6 0 ^ { \\circ } . ~ { \\dot { \\omega } } \\mathrm { C u a l }", "60^{\\circ}", True, True),  # basura absorbida
        ("^ { 1 , 4 , 9 , 1 6 , \\ldots \\ldots }", "1,4,9,16,......", True, True),  # superíndice espurio
        ("x ^ { 2 } y ^ { 5 } z ^ { \\hat { 1 0 } } ?", "x ^ { 2 } y ^ { 5 } z ^ { 1 0 }", True, True),  # acento espurio
        ("r ^ {2} + (r _ {l}) ^ {2}", "r ^ {2} + (r _ {1}) ^ {2}", False, True),  # l -> 1: arreglo local
        ("= \\left( = a \\cdot 10^{3} + b", "abcd = \\left( = a \\cdot 10^{3} + b", False, True),  # display puede agregar
        ("a = 9 ,", "a = 9,,", True, False),  # sólo puntuación
        ("A B = 3", "AB = 3 \\text{ cm}", True, False),  # inline absorbe texto vecino
        ("C _ { 1 0 0 } ^ { 2 0 0 } ~ \\mathrm { e s } ~ 6 1", "C _ { 1 0 0 } ^ { 2 0 0 }", True, False),  # pierde la respuesta
        ("a ^ { 2 } + b ^ { 2 } \\mathrm { ~ y ~ } a + b ,", "a ^ { 2 } + b ^ { 2 }", True, False),  # pierde matemática
        ("9 ( b c d a )", "( b c d a )", False, False),  # borrar dígitos: conservador
        ("n \\in \\mathbb { Z }", "n ∈ ℤ", True, False),  # unicode en vez de LaTeX
        ("\\underset { n } { ( 9 9 9 ) ^ { 2 } }", "\\raw { ( 9 9 9 ) ^ { 2 } }", True, False),  # LaTeX destrozado
        ("S = n + \\dots + k", "S = n + ... + k", False, False),  # cosmético
    ]
    _fake(enricher, monkeypatch, lambda m, p, s: json.dumps({"items": [{"i": i, "latex": c[1], "changed": True} for i, c in enumerate(cases)]}))
    out = enricher.qa_formulas(_png(enricher.cache_dir), [c[0] for c in cases], inline=[c[2] for c in cases])
    assert [o["changed"] for o in out] == [c[3] for c in cases]
    assert out[6]["rejected"] == "AB = 3 \\text{ cm}"
    assert "rejected" not in out[5]  # cosmético: ni cambio ni rechazo registrado


def test_is_daily_quota_parses_google_error():
    class R:
        def json(self):
            return {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}

    class R2:
        def json(self):
            return {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}]}]}}

    from ragsolver import llm

    assert llm._is_daily_quota(R()) is True
    assert llm._is_daily_quota(R2()) is False


def _png(d):
    p = d / "x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    return p
