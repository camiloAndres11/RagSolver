# QA de ingesta — `edd3436ca148` (problemas resueltos olimpiadas.pdf)

Generado por `scripts/qa_ingest_report.py`; criterios en `docs/PLAN_RAG.md` Fase 2, tarea 10.

## Resumen

- Páginas: 77 · cards: 100 · **emparejadas problema↔solución: 100/100** · chunks: 153 (cards + figuras)
- Figuras recortadas: 53 · con descripción VLM: 53 · avisos red de seguridad vectorial: 0
- Huecos del layout transcritos por VLM: 7 regiones
- QA de fórmulas: 76 correcciones aplicadas, 133 propuestas rechazadas por las guardas
- Llamadas al VLM/LLM en esta corrida (sin caché): 0
- Áreas (cards): {'geometry': 46, 'number_theory': 45, 'algebra': 31, 'combinatorics': 18, 'inequalities': 4, 'analysis': 1}

## Gate 1 — emparejamiento problema↔solución (objetivo 100/100)

Resultado: **100/100**. Avisos: ninguno.

Notas del split por LLM: 1. Contenido derivado del documento redactado (`redistributable=false`); disponible localmente en `data/parsed/` (gitignored), nunca en git/HF.

## Gate 2 — figuras de los problemas 8, 16, 17, 22, 24, 37, 99

_Contenido derivado del documento redactado (`redistributable=false`); disponible localmente en `data/parsed/` (gitignored), nunca en git/HF. (las descripciones se generan a partir de las figuras del documento)._

| Problema | Figuras | detected_by |
|---|---|---|
| 8 | `edd3436ca148:fig:10:2` | layout |
| 8 | `edd3436ca148:fig:29:11` | both |
| 16 | `edd3436ca148:fig:32:4` | both |
| 17 | `edd3436ca148:fig:11:7` | layout |
| 17 | `edd3436ca148:fig:33:4` | layout |
| 22 | `edd3436ca148:fig:12:3` | both |
| 24 | `edd3436ca148:fig:12:6` | both |
| 24 | `edd3436ca148:fig:36:6` | both |
| 37 | `edd3436ca148:fig:45:2` | both |
| 37 | `edd3436ca148:fig:45:4` | both |
| 37 | `edd3436ca148:fig:45:6` | both |
| 37 | `edd3436ca148:fig:45:8` | both |
| 37 | `edd3436ca148:fig:45:10` | both |
| 37 | `edd3436ca148:fig:45:12` | both |
| 99 | `edd3436ca148:fig:24:2` | both |
| 99 | `edd3436ca148:fig:76:8` | layout |

## Rúbrica manual — 15 descripciones (sí/no: ¿nombra todos los puntos?, ¿relaciones correctas?, ¿valores dados?)

| # | Figura | Puntos | Relaciones | Valores | Observación |
|---|---|---|---|---|---|
| 1 | `edd3436ca148:fig:24:8` |  |  |  |  |
| 2 | `edd3436ca148:fig:26:9` |  |  |  |  |
| 3 | `edd3436ca148:fig:27:6` |  |  |  |  |
| 4 | `edd3436ca148:fig:9:9` |  |  |  |  |
| 5 | `edd3436ca148:fig:10:2` |  |  |  |  |
| 6 | `edd3436ca148:fig:29:11` |  |  |  |  |
| 7 | `edd3436ca148:fig:30:3` |  |  |  |  |
| 8 | `edd3436ca148:fig:32:4` |  |  |  |  |
| 9 | `edd3436ca148:fig:11:7` |  |  |  |  |
| 10 | `edd3436ca148:fig:33:4` |  |  |  |  |
| 11 | `edd3436ca148:fig:12:3` |  |  |  |  |
| 12 | `edd3436ca148:fig:12:6` |  |  |  |  |
| 13 | `edd3436ca148:fig:36:6` |  |  |  |  |
| 14 | `edd3436ca148:fig:13:2` |  |  |  |  |
| 15 | `edd3436ca148:fig:13:6` |  |  |  |  |

## Huecos del layout de MinerU recuperados por VLM

7 regiones recuperadas (páginas: 6, 23, 25, 28, 50, 64, 66). Contenido derivado del documento redactado (`redistributable=false`); disponible localmente en `data/parsed/` (gitignored), nunca en git/HF.

## QA de fórmulas (6b) — correcciones aplicadas

76 correcciones en 33 páginas (páginas: 9, 10, 11, 14, 15, 17, 18, 21, 22, 25, 26, 27, 31, 39, 40, 43, 44, 46, 47, 50, 52, 53, 54, 57, 58, 59, 60, 61, 64, 65, 66, 67, 70). Contenido derivado del documento redactado (`redistributable=false`); disponible localmente en `data/parsed/` (gitignored), nunca en git/HF.

Rechazadas por las guardas (cambio sólo cosmético o absorción de texto vecino): 133 — detalle en `data/parsed/edd3436ca148/ingest_report.json` (local, gitignored).

## Gold de OCR (10 páginas transcritas a mano)

Pendiente: requiere transcripción manual humana (no se fabrica). Sustituto disponible hoy: las 5 correcciones reales detectadas por el VLM en 2 páginas de muestra (docs/DECISIONS.md #19).
