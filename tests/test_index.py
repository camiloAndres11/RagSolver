from __future__ import annotations

from ragsolver.rag.index import rrf_fuse


def test_rrf_fuse_favors_docs_ranked_high_in_multiple_lists():
    dense = ["a", "b", "c"]
    sparse = ["b", "a", "d"]
    fused = rrf_fuse([dense, sparse])
    ids = [doc_id for doc_id, _ in fused]
    # "a" y "b" aparecen arriba en ambas listas -> deben ir antes que "c"/"d" (sólo en una)
    assert set(ids[:2]) == {"a", "b"}
    assert ids[-1] in {"c", "d"}


def test_rrf_fuse_empty_lists_returns_empty():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []


def test_rrf_fuse_single_list_preserves_order():
    fused = rrf_fuse([["x", "y", "z"]])
    assert [doc_id for doc_id, _ in fused] == ["x", "y", "z"]
