"""Fase 2, tarea 6 — enriquecimiento offline con el VLM/LLM (docs/PLAN_RAG.md §6.2).

Proveedor: el que diga configs/versions.yaml `vlm_enrichment` (ragsolver.llm.LLM:
Gemini u OpenAI-compatible). Todas las llamadas son offline (indexación); el
retriever sigue en 0 llamadas (RNF-3).

Operaciones (prompts versionados en configs/ingest.yaml `enrich:`):
- transcribe_region: Plan B acotado — transcribe una región que MinerU se saltó.
- describe_figure  (6a): recorte + enunciado -> descripción estructurada.
- qa_formulas      (6b): imagen de página + LaTeX extraído -> LaTeX corregido.
- summarize_card   (6c): problem card -> context_summary, math_area, techniques, ...
- split_solution: casos ambiguos del emparejamiento (numeral perdido por el OCR).

Caché JSONL por operación (data/parsed/<doc_id>/enrich/<op>.jsonl) con clave =
sha256(prompt_version + entradas): re-ejecutar la ingesta no repite llamadas.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from ragsolver.llm import LLM, DailyQuotaExhausted  # noqa: F401  (re-export para tests/llamadores)

_FIGURE_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "labels": {"type": "array", "items": {"type": "string"}},
        "objects": {"type": "array", "items": {"type": "string"}},
        "relations": {"type": "array", "items": {"type": "string"}},
        "given_values": {"type": "array", "items": {"type": "string"}},
        "figure_type": {"type": "string", "enum": ["geometric_diagram", "grid", "plot", "table_image", "other"]},
    },
    "required": ["description", "labels", "objects", "relations", "given_values", "figure_type"],
}
_FORMULAS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"i": {"type": "integer"}, "latex": {"type": "string"}, "changed": {"type": "boolean"}},
                "required": ["i", "latex", "changed"],
            },
        }
    },
    "required": ["items"],
}
_CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "context_summary": {"type": "string"},
        "math_area": {"type": "array", "items": {"type": "string"}},
        "techniques": {"type": "array", "items": {"type": "string"}},
        "theorems_used": {"type": "array", "items": {"type": "string"}},
        "difficulty": {"type": "integer"},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["context_summary", "math_area", "techniques", "theorems_used", "difficulty", "keywords"],
}
_SPLIT_SCHEMA = {"type": "object", "properties": {"first_line": {"type": "string"}}, "required": ["first_line"]}


class Enricher:
    def __init__(self, cache_dir: str | Path, ingest_yaml: str = "configs/ingest.yaml", versions_yaml: str = "configs/versions.yaml", llm: LLM | None = None):
        cfg = yaml.safe_load(open(ingest_yaml, encoding="utf-8"))["enrich"]
        self.llm = llm or LLM(versions_yaml, section="vlm_enrichment")
        self.prompts = cfg
        self.version = str(cfg["prompt_version"])
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()

    @property
    def models(self) -> list[str]:
        return self.llm.models

    @property
    def model(self) -> str:
        return self.llm.model

    @property
    def exhausted(self) -> list[str]:
        return self.llm.exhausted

    @property
    def calls(self) -> int:
        return self.llm.calls

    # -- caché ---------------------------------------------------------------

    def _cache_file(self, op: str) -> Path:
        return self.cache_dir / f"{op}.jsonl"

    def _load(self, op: str) -> dict:
        if op not in self._cache:
            self._cache[op] = {}
            f = self._cache_file(op)
            if f.exists():
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        rec = json.loads(line)
                        self._cache[op][rec["key"]] = (rec["result"], rec.get("model", self.llm.models[0]))
        return self._cache[op]

    def _key(self, op: str, *parts: str) -> str:
        # El prompt de la operación entra en la clave: editar un prompt invalida sólo su caché.
        # El modelo NO entra: un resultado ya calculado por cualquier modelo de la cadena se reutiliza.
        h = hashlib.sha256(("\x00".join([self.version, op, self.prompts.get(op, ""), *parts])).encode("utf-8"))
        return h.hexdigest()[:24]

    def _cached(self, op: str, key: str, compute) -> tuple:
        """Devuelve (resultado, modelo que lo produjo)."""
        with self._lock:
            cache = self._load(op)
            if key in cache:
                return cache[key]
        result, model = compute()  # fuera del lock: las llamadas HTTP corren en paralelo
        with self._lock:
            cache[key] = (result, model)
            with self._cache_file(op).open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "model": model, "result": result}, ensure_ascii=False) + "\n")
        return result, model

    def map(self, fn, items, workers: int = 3) -> list:
        """Aplica `fn` a `items` en paralelo (pocos hilos: la cuota es por minuto)."""
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(fn, items))

    # -- llamadas (delegadas a ragsolver.llm.LLM) --------------------------------

    def _generate(self, parts: list[dict], schema: dict | None) -> tuple[str, str]:
        """(texto, modelo). `parts`: [{"text": ...}] y/o [{"image": path}]."""
        prompt = "\n".join(p["text"] for p in parts if "text" in p)
        images = [p["image"] for p in parts if "image" in p]
        r = self.llm.generate(prompt, schema=schema, images=images)
        return r.text, r.model

    def _json(self, parts: list[dict], schema: dict) -> tuple[dict, str]:
        text, model = self._generate(parts, schema)
        return json.loads(text), model

    # -- operaciones -----------------------------------------------------------

    def transcribe_region(self, png_path: str | Path) -> str:
        img = _image_part(png_path)
        key = self._key("transcribe", _sha(png_path))
        text, _ = self._cached("transcribe", key, lambda: (lambda t, m: (t.strip(), m))(*self._generate([{"text": self.prompts["transcribe"]}, img], None)))
        return text

    def describe_figure(self, png_path: str | Path, statement: str, solution: str = "") -> dict:
        img = _image_part(png_path)
        prompt = f"{self.prompts['figure']}\n\nProblem statement (Spanish):\n{statement}\n\nSolution excerpt (Spanish):\n{solution[:2500]}"
        key = self._key("figure", _sha(png_path), statement, solution[:2500])
        d, model = self._cached("figure", key, lambda: self._json([{"text": prompt}, img], _FIGURE_SCHEMA))
        return {**d, "model": model}

    def qa_formulas(self, page_png: str | Path, latex_list: list[str], inline: list[bool] | None = None) -> list[dict]:
        """Devuelve [{i, latex, changed}] alineado con `latex_list`; `inline[i]` marca fórmulas en línea."""
        if not latex_list:
            return []
        inline = inline or [False] * len(latex_list)
        listing = "\n".join(f"[{i}] {s}" for i, s in enumerate(latex_list))
        prompt = f"{self.prompts['formulas']}\n\n{listing}"
        key = self._key("formulas", _sha(page_png), listing)
        res, _ = self._cached("formulas", key, lambda: self._json([{"text": prompt}, _image_part(page_png)], _FORMULAS_SCHEMA))
        out = [{"i": i, "latex": s, "changed": False} for i, s in enumerate(latex_list)]
        for it in res.get("items", []):
            i = it.get("i")
            if not (isinstance(i, int) and 0 <= i < len(out) and it.get("changed")):
                continue
            if _alnum(it["latex"]) == _alnum(latex_list[i]):
                continue  # cambio sólo de puntuación/espaciado: ruido, se ignora
            if _acceptable_fix(latex_list[i], it["latex"], inline[i]):
                out[i] = {"i": i, "latex": it["latex"], "changed": True}
            else:
                out[i]["rejected"] = it["latex"]
        return out

    def summarize_card(self, content_md: str, figure_descriptions: list[str]) -> dict:
        prompt = self.prompts["card"].format(math_areas=", ".join(self.prompts["math_areas"]), techniques=", ".join(self.prompts["techniques"]))
        figs = "\n".join(f"- {d}" for d in figure_descriptions) or "(none)"
        body = f"{prompt}\n\n## Problem and solution (Spanish, Markdown+LaTeX)\n{content_md}\n\n## Figure descriptions\n{figs}"
        key = self._key("card", content_md, figs)
        res, model = self._cached("card", key, lambda: self._json([{"text": body}], _CARD_SCHEMA))
        res = {**res, "model": model}
        res["math_area"] = [a for a in res.get("math_area", []) if a in self.prompts["math_areas"]] or ["other"]
        res["difficulty"] = min(5, max(1, int(res.get("difficulty", 3))))
        return res

    def split_solution(self, n: int, merged_text: str, statement_prev: str, statement_n: str) -> str:
        prompt = self.prompts["split"].format(prev=n - 1, n=n)
        body = f"{prompt}\n\n## Problem {n-1}\n{statement_prev}\n\n## Problem {n}\n{statement_n}\n\n## Text\n{merged_text}"
        key = self._key("split", str(n), merged_text)
        res, _ = self._cached("split", key, lambda: self._json([{"text": body}], _SPLIT_SCHEMA))
        return res.get("first_line", "").strip()


def _alnum(latex: str) -> str:
    return re.sub(r"[^0-9A-Za-z^_{}]", "", latex)


_CONTENT_RE = re.compile(r"[0-9+\-=<>/*]|\\(?:frac|sqrt|sum|prod|int|binom|cdot|times|le|ge|leq|geq|neq|equiv|in|pm|infty|angle|underbrace|overbrace)")


def _garbage(chunk: str) -> bool:
    """Trozo de LaTeX que se puede borrar sin perder matemática: envoltorios
    (\\mathrm{...}, \\hat{...}) o texto absorbido por el OCR ("¿Cuál", "y"), sin
    dígitos ni operadores."""
    if _CONTENT_RE.search(chunk):
        return False
    letters = re.sub(r"[^A-Za-z]", "", re.sub(r"\\[A-Za-z]+", "", chunk))
    return len(letters) <= 6


def _canon(latex: str) -> str:
    """Normaliza diferencias cosméticas antes de comparar (espacios, puntos suspensivos, espaciado LaTeX)."""
    s = re.sub(r"\\[lc]?dots", "...", latex)
    s = re.sub(r"\\[,;!:]|\\q?quad|~", "", s)
    return re.sub(r"\s+", "", s)


def _acceptable_fix(old_latex: str, new_latex: str, inline: bool) -> bool:
    """Medido sobre el PDF de ejemplo (docs/DECISIONS.md #19): el VLM propone ruido de
    puntuación, absorbe texto vecino en fórmulas inline, recorta contenido real cuando
    el OCR fusionó texto+matemática, y a veces devuelve LaTeX destrozado (`\\underset`
    -> `\\raw` con caracteres de control; `\\mathbb{R}` -> `ℒ`). Reglas:
    (a) caracteres de control o no-ASCII nuevos -> rechazo;
    (b) borrado puro -> aceptar sólo si cada trozo borrado es basura/envoltorio;
    (c) otro cambio -> similitud >= 0.8 (arreglo local) y, en inline, no alargar."""
    if re.search(r"[\x00-\x1f]", new_latex) or (set(new_latex) - set(old_latex)) & {c for c in new_latex if ord(c) > 127}:
        return False
    old, new = _canon(old_latex), _canon(new_latex)
    if new == old or _alnum(new) == _alnum(old):
        return False
    ops = SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
    inserted = "".join(new[j1:j2] for tag, _, _, j1, j2 in ops if tag in ("insert", "replace"))
    removed = [old[i1:i2] for tag, i1, i2, _, _ in ops if tag in ("delete", "replace")]
    if not inserted:
        return all(_garbage(chunk) for chunk in removed)
    if inline and len(_alnum(new)) > len(_alnum(old)):
        return False
    return SequenceMatcher(None, old, new, autojunk=False).ratio() >= 0.8


def _sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def _image_part(path: str | Path) -> dict:
    return {"image": str(path)}
