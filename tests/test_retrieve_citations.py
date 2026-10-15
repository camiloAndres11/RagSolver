from __future__ import annotations

from ragsolver.rag.retrieve import (
    _informal_citation_label,
    _informal_locator,
    _mathlib_citation_label,
    _mathlib_locator,
)


def test_mathlib_citation_label_and_locator():
    row = {"decl_name": "Nat.Coprime", "module_path": "Mathlib.Data.Nat.GCD.Basic", "docs_url": "https://x/y#Nat.Coprime"}
    assert _mathlib_citation_label(row) == "Nat.Coprime — Mathlib.Data.Nat.GCD.Basic"
    locator = _mathlib_locator(row)
    assert locator["decl_name"] == "Nat.Coprime"
    assert locator["docs_url"].endswith("#Nat.Coprime")


def test_informal_citation_label_includes_item_and_page():
    row = {"doc_id": "olimpiadas-2002", "item_number": 22, "page_start": 11}
    assert _informal_citation_label(row) == "olimpiadas-2002, ítem 22, p. 11"


def test_informal_citation_label_degrades_gracefully_without_item_or_page():
    row = {"doc_id": "paper-xyz"}
    assert _informal_citation_label(row) == "paper-xyz"


def test_informal_locator_carries_figure_ids():
    row = {"doc_id": "d1", "page_start": 5, "page_end": 5, "item_number": 3, "figure_ids": ["d1:fig:5:0"]}
    locator = _informal_locator(row)
    assert locator["figure_ids"] == ["d1:fig:5:0"]
    assert locator["page_start"] == 5
