"""Sonda 0-LLM, reanudable: ¿qué input de recuperación cierra más problemas de eval_v1 en el kernel?
Variantes: grind | grind +suggestions | try? | grind +lax [top-16 NL] | grind +lax [top-16 formal] | grind +lax [prior top-32].
Uso (raíz del repo): RAGSOLVER_MATHLIB_TABLE=mathlib_full .venv/bin/python probe_close.py
Salida persistente: data/eval/results/probe_close/{lwb_prior.json,names.json,probe.jsonl}
"""
import collections, json, os, re, sys, time
from pathlib import Path

OUT = Path("data/eval/results/probe_close")
problems = [json.loads(l) for l in open("data/eval/problems/eval_v1.jsonl")]
sys.path.insert(0, "scripts")
from ragsolver.lean.verify import LeanChecker, find_forbidden_tactic

# 1) prior de competición (Lean Workbook, Goedel-Prover, MIT)
if not (OUT / "lwb_prior.json").exists():
    import pyarrow.parquet as pq
    from huggingface_hub import hf_hub_download
    from build_premise_gold import premises_of
    pq_path = hf_hub_download("Goedel-LM/Lean-workbook-proofs", "data/train-00000-of-00001.parquet", repo_type="dataset", local_dir="data/raw/lean_workbook_proofs")
    names = {json.loads(l)["decl_name"] for l in open("data/corpus/mathlib.jsonl")}
    prior = collections.Counter()
    for r in pq.read_table(pq_path).to_pylist():
        prior.update(set(premises_of(r["full_proof"], names)))
    json.dump(prior.most_common(500), open(OUT / "lwb_prior.json", "w"))
prior32 = [n for n, _ in json.load(open(OUT / "lwb_prior.json")) if not find_forbidden_tactic(n)][:32]
print("prior ok", flush=True)

# 2) recuperación con nuestro índice (embedder en CPU) antes de cargar Lean
if not (OUT / "names.json").exists():
    from ragsolver.rag.retrieve import Retriever
    from ragsolver.rag.schema import RetrievalRequest
    ret = Retriever(); ret.trace_dir = str(OUT / "traces")
    got = {}
    for p in problems:
        formal = re.sub(r":=\s*by\s+sorry\s*$", "", p["formal_statement"].strip())
        for mode, q in (("nl", p["informal_statement"][:2000]), ("formal", formal[:2000])):
            r = ret.search(RetrievalRequest(queries=[q], mode=mode, corpora=["mathlib"], k=16, n_candidates=50, rerank=False, context={"problem_id": p["problem_id"], "agent": "probe"}))
            got[f"{p['problem_id']}|{mode}"] = [h.metadata["decl_name"] for h in r.hits]
    json.dump(got, open(OUT / "names.json", "w"))
    del ret
names = json.load(open(OUT / "names.json"))
print("retrieval ok", flush=True)

lst = lambda ns: ", ".join(n for n in ns if not find_forbidden_tactic(n))
variants = {
    "grind": lambda p: "grind",
    "grind_suggestions": lambda p: "grind +suggestions",
    "try?": lambda p: "try?",
    "grind_rag_nl": lambda p: f"grind +lax [{lst(names[p['problem_id'] + '|nl'])}]",
    "grind_rag_formal": lambda p: f"grind +lax [{lst(names[p['problem_id'] + '|formal'])}]",
    "grind_prior32": lambda p: f"grind +lax [{lst(prior32)}]",
}
done = set()
if (OUT / "probe.jsonl").exists():
    done = {(r["problem_id"], r["variant"]) for r in map(json.loads, open(OUT / "probe.jsonl"))}
checker = LeanChecker(imports=["Mathlib", "Aesop"], timeout_s=240)
with open(OUT / "probe.jsonl", "a") as f:
    for p in problems:
        for vname, tac in variants.items():
            if (p["problem_id"], vname) in done:
                continue
            t0 = time.time()
            v = checker.check(p["formal_statement"], tac(p), p.get("header", ""), 200000)
            f.write(json.dumps({"problem_id": p["problem_id"], "variant": vname, "ok": v.ok, "s": round(time.time() - t0, 1), "err": (v.errors[0]["message"][:200] if v.errors else ""), "tactic": tac(p)[:600]}, ensure_ascii=False) + "\n")
            f.flush()
            print(p["problem_id"], vname, v.ok, flush=True)
checker.close()
print("PROBE DONE", flush=True)
