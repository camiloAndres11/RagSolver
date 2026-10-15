"""Cliente LLM/VLM único para agentes (Fase 4) y enriquecimiento (Fase 2).

Proveedores (configs/versions.yaml, secciones `llm` / `vlm_enrichment`):
- `gemini`: REST `generativelanguage.googleapis.com` (key en GEMINI_API_KEY).
- `openai_compatible`: `/chat/completions` con `base_url` (OpenRouter, OpenAI,
  Ollama, vLLM...); key en la variable que diga `api_key_env`.

Un solo método: `generate(prompt, schema=None, images=(), system=None)` ->
`LLMResponse(text, model, tokens_in, tokens_out)`. Con `schema` (JSON Schema
estándar) la salida es JSON validado; para Gemini se traduce al subconjunto
OpenAPI que acepta `responseSchema`.

Cadena de modelos: `model` + `fallback_models`; al agotar la cuota diaria de uno
(429 con quotaId *PerDay*) se pasa al siguiente. Reintentos (LLM-1, hasta 4,
`_MAX_ATTEMPTS`): error de transporte o 429/5xx -> 3 s / exponencial; **nunca**
un timeout de lectura (el proveedor puede haber generado — y cobrado — la
respuesta antes de que el timeout cortara la conexión; reintentarlo la
duplicaría). Sin caché aquí (la tiene el Enricher).
"""

from __future__ import annotations

import base64
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml
from tenacity import Retrying, retry_if_exception

_GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# ponytail: fijo, no por instancia — si algún día hace falta variar el número de reintentos entre
# secciones (llm/vlm_enrichment), pasar `max_attempts` al constructor y usarlo en el `Retrying` de abajo.
_MAX_ATTEMPTS = 4
# Un 429 es una solicitud RECHAZADA (no se cobra): con el proveedor saturado ("engine_overloaded",
# DECISIONS #103-#104) conviene esperar más en vez de perder el par con lo ya gastado.
_MAX_ATTEMPTS_429 = 10


class DailyQuotaExhausted(Exception):
    """429 con quotaId *PerDay*: el modelo no vuelve hasta mañana -> cambiar de modelo."""


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 1  # >1 cuando una respuesta agrega varias llamadas (self-consistency)
    cost_usd: float = 0.0  # OpenRouter `usage.cost` (exacto, por proveedor); 0 si el backend no lo informa
    attempts: int = 1  # LLM-1: cuántos intentos hizo _generate_with (reintentos de transporte/429/5xx)

    def json(self) -> dict:
        return json.loads(self.text)


def _is_daily_quota(response: httpx.Response) -> bool:
    try:
        details = response.json()["error"].get("details", [])
    except Exception:
        return False
    return any("PerDay" in v.get("quotaId", "") for d in details for v in d.get("violations", []))


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    if isinstance(exc, httpx.ReadTimeout):
        # LLM-1: un timeout de LECTURA puede significar que el proveedor ya generó (y cobró) la
        # respuesta y el corte fue nuestro; reintentar la duplicaría. ConnectTimeout y el resto de
        # TransportError sí son seguros de reintentar (nada se envió o el proveedor no respondió nada).
        return False
    # JSON truncado/degenerado NO se reintenta aquí: cada intento cuesta `max_tokens` de salida y
    # el reintento con `stop_after_attempt` alto no los contaba — fue la causa del sobrecosto de
    # eval_v1 (DECISIONS #59). El único reintento (con el doble de tokens) lo hace `Agents._call`,
    # y se contabiliza.
    return isinstance(exc, httpx.TransportError)


def _wait(retry_state) -> float:
    exc = retry_state.outcome.exception()
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        return min(90.0, 4.0 * 2 ** (retry_state.attempt_number - 1))
    return 3.0


def _stop(retry_state) -> bool:
    exc = retry_state.outcome.exception()
    is_429 = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
    return retry_state.attempt_number >= (_MAX_ATTEMPTS_429 if is_429 else _MAX_ATTEMPTS)


def _log_retry(retry_state) -> None:
    exc = retry_state.outcome.exception()
    print(f"[llm retry] intento {retry_state.attempt_number} falló: {type(exc).__name__}: {str(exc)[:150]}", flush=True)


def _gemini_schema(schema: dict) -> dict:
    """JSON Schema (Pydantic) -> subconjunto OpenAPI de Gemini: resuelve `$ref`/`$defs`,
    tipos en mayúsculas, `anyOf [X, null]` -> X nullable, sin claves no soportadas."""
    defs = schema.get("$defs", {}) if isinstance(schema, dict) else {}

    def conv(node):
        if isinstance(node, list):
            return [conv(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return conv(defs[node["$ref"].rsplit("/", 1)[1]])
        if "anyOf" in node:
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            nullable = len(options) < len(node["anyOf"])
            base = conv(options[0]) if len(options) == 1 else {"anyOf": conv(options)}
            if nullable:
                base["nullable"] = True
            return base
        out = {}
        for k, v in node.items():
            if k in ("additionalProperties", "$schema", "title", "default", "$defs", "definitions", "examples"):
                continue
            if k == "type" and isinstance(v, str):
                out[k] = v.upper()
            elif k == "properties":
                out[k] = {pk: conv(pv) for pk, pv in v.items()}
            elif k == "items":
                out[k] = conv(v)
            else:
                out[k] = v
        return out

    return conv(schema)


def _read_dotenv(name: str, path: str = ".env") -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


class LLM:
    def __init__(self, versions_yaml: str = "configs/versions.yaml", section: str = "llm", config: dict | None = None):
        cfg = config or yaml.safe_load(open(versions_yaml, encoding="utf-8"))[section]
        self.provider = cfg.get("provider", "gemini")
        self.models: list[str] = [cfg["model"], *(cfg.get("fallback_models") or [])]
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        key_env = cfg.get("api_key_env") or ("GEMINI_API_KEY" if self.provider == "gemini" else "OPENROUTER_API_KEY")
        self.api_key = os.environ.get(key_env) or _read_dotenv(key_env)
        if not self.api_key:
            raise RuntimeError(f"{key_env} no está en el entorno ni en .env")
        self.temperature = float(cfg.get("temperature", 0.1))
        self.seed = cfg.get("seed")  # RNF-2: semilla fijada en cada llamada (Gemini `seed`, OpenAI `seed`)
        self.max_tokens = cfg.get("max_tokens")  # tope de salida (incluye el razonamiento en modelos "thinking")
        self.reasoning_effort = cfg.get("reasoning_effort")  # OpenRouter: off | low | medium | high
        self.timeout_s = float(cfg.get("timeout_s", 300))  # LLM-1: bajado de 600-900; 4000 tok de salida al proveedor más lento medido son ~210s (#62)
        self.provider_routing = cfg.get("provider_routing")  # OpenRouter `provider` (max_price, sort, order…), se envía tal cual
        self._model_idx = 0
        self._lock = threading.Lock()
        self.exhausted: list[str] = []
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        self._client = httpx.Client(timeout=self.timeout_s)

    @property
    def model(self) -> str:
        return self.models[self._model_idx]

    def generate(self, prompt: str, schema: dict | None = None, images: tuple[str | Path, ...] | list = (), system: str | None = None, max_tokens: int | None = None) -> LLMResponse:
        while True:
            model = self.model
            try:
                return self._generate_with(model, prompt, schema, tuple(images), system, max_tokens)
            except DailyQuotaExhausted:
                with self._lock:
                    if self.models[self._model_idx] == model:
                        if self._model_idx + 1 >= len(self.models):
                            raise
                        self._model_idx += 1
                        self.exhausted.append(model)

    def _generate_with(self, model: str, prompt: str, schema: dict | None, images: tuple, system: str | None, max_tokens: int | None) -> LLMResponse:
        # `Retrying` en vez del decorador `@retry`: así se cuenta el número de intentos de esta
        # llamada (LLM-1, `resp.attempts`) sin estado compartido entre hilos (el runner usa `--workers`).
        n_attempts = 0
        for attempt in Retrying(retry=retry_if_exception(_retryable), wait=_wait, stop=_stop, before_sleep=_log_retry, reraise=True):
            with attempt:
                n_attempts += 1
                if self.provider == "gemini":
                    resp = self._gemini(model, prompt, schema, images, system, max_tokens)
                else:
                    resp = self._openai(model, prompt, schema, images, system, max_tokens)
        resp.attempts = n_attempts
        with self._lock:
            self.calls += 1
            self.tokens_in += resp.tokens_in
            self.tokens_out += resp.tokens_out
            self.cost_usd += resp.cost_usd
        return resp

    # -- backends ----------------------------------------------------------------

    def _gemini(self, model, prompt, schema, images, system, max_tokens) -> LLMResponse:
        parts = [{"text": prompt}] + [
            {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(Path(p).read_bytes()).decode()}} for p in images
        ]
        gen: dict = {"temperature": self.temperature}
        if self.seed is not None:
            gen["seed"] = int(self.seed)
        if max_tokens or self.max_tokens:
            gen["maxOutputTokens"] = int(max_tokens or self.max_tokens)
        if schema is not None:
            gen.update(responseMimeType="application/json", responseSchema=_gemini_schema(schema))
        body: dict = {"contents": [{"parts": parts}], "generationConfig": gen}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        r = self._client.post(_GEMINI_API.format(model=model), headers={"x-goog-api-key": self.api_key}, json=body)
        if r.status_code == 429 and _is_daily_quota(r):
            raise DailyQuotaExhausted(model)
        r.raise_for_status()
        data = r.json()
        cand = data["candidates"][0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
        usage = data.get("usageMetadata", {})
        return LLMResponse(text, model, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0))

    def _openai(self, model, prompt, schema, images, system, max_tokens) -> LLMResponse:
        content: list[dict] = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(Path(p).read_bytes()).decode()}} for p in images
        ]
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
        body: dict = {"model": model, "messages": messages, "temperature": self.temperature}
        if self.seed is not None:
            body["seed"] = int(self.seed)
        if max_tokens or self.max_tokens:
            body["max_tokens"] = int(max_tokens or self.max_tokens)
        if "openrouter" in self.base_url:
            body["usage"] = {"include": True}  # costo exacto en la respuesta
            if self.provider_routing:
                body["provider"] = self.provider_routing
        if self.reasoning_effort:  # "off" = sin cadena de razonamiento (DeepSeek V4 Flash: 24 s vs 76-90 s por llamada)
            body["reasoning"] = {"enabled": False} if self.reasoning_effort == "off" else {"effort": self.reasoning_effort}
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": False}}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if "openrouter" in self.base_url:
            headers["HTTP-Referer"] = "https://github.com/ragsolver"  # atribución opcional que pide OpenRouter
        r = self._client.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
        if (r.status_code == 429 and "daily" in r.text.lower()) or r.status_code in (402, 403):
            # 402/403 en OpenRouter = key sin crédito o fuera de su tope: se pasa al siguiente modelo y, si
            # todos fallan, el runner corta limpio (status quota_exhausted) en vez de grabar 300 pares "error"
            raise DailyQuotaExhausted(f"{model}: HTTP {r.status_code} {r.text[:120]}")
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {})
        return LLMResponse(text, model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), cost_usd=float(usage.get("cost") or 0.0))
