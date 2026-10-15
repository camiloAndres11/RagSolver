"""¿Cuántas premisas de las pruebas verificadas de miniF2F (DeepSeek-Prover-V2) existen en el
entorno de Lean (Mathlib + core) pero NO en el corpus de LeanSearch v2? 0 LLM.
Uso (raíz del repo): .venv/bin/python data/eval/results/corpus_gap/corpus_gap.py"""
import collections, glob, json, re
from pathlib import Path
from lean_interact import Command
from ragsolver.lean.verify import LeanChecker

OUT = Path("data/eval/results/corpus_gap")
IDENT = re.compile(r"(?<![\w.'])([A-Za-z_][\w']*(?:\.[A-Za-z_][\w']*)*)")
corpus = {json.loads(l)["decl_name"] for l in open("data/corpus/mathlib.jsonl")}
uses = collections.Counter()  # identificador -> nº de pruebas
for p in glob.glob("data/eval/minif2f_v2/dsp2/**/*.lean", recursive=True):
    body = Path(p).read_text(encoding="utf-8").split(":= by", 1)[-1]
    uses.update({t for t in IDENT.findall(body) if ("_" in t or "." in t) and len(t) > 3})
TACTICS = {"norm_num", "nlinarith", "linarith", "positivity", "field_simp", "ring_nf", "simp_all", "interval_cases", "norm_cast", "push_cast", "exact_mod_cast", "simp_arith", "decide", "omega", "rcases", "obtain", "by_contra", "by_cases", "push_neg", "native_decide", "apply_fun", "gcongr", "bound", "aesop", "tauto", "nlinarith!", "split_ifs", "rw_mod_cast", "zify", "qify", "lift", "induction'", "cases'", "refine'", "set_option", "calc_step"}
def base(t):  # `foo.mpr`, `foo.symm.le` -> `foo` si `foo` está en el corpus (acceso a campo de un lema del corpus)
    parts = t.split(".")
    for i in range(len(parts), 0, -1):
        if ".".join(parts[:i]) in corpus:
            return ".".join(parts[:i])
    return t
norm = collections.Counter()
for t, c in uses.items():
    if t.split(".")[0] not in TACTICS:
        norm[base(t)] += c
uses = norm
cands = sorted(t for t in uses if t not in corpus)
ck = LeanChecker(imports=["Mathlib", "Aesop"], timeout_s=600)
ck._start()
exists = []
for t in cands:  # uno por comando: saber exactamente cuáles existen
    r = ck._server.run(Command(cmd=f"open BigOperators Real Nat Topology Rat in\n#check @{t}", env=ck._env), timeout=60)
    if not any(m.severity == "error" for m in r.messages):
        exists.append(t)
ck.close()
in_corpus = {t: c for t, c in uses.items() if t in corpus}
gap = {t: uses[t] for t in exists}
res = {"proofs": len(glob.glob("data/eval/minif2f_v2/dsp2/**/*.lean", recursive=True)),
       "premise_uses_in_corpus": sum(in_corpus.values()), "premise_uses_missing_but_in_lean": sum(gap.values()),
       "distinct_in_corpus": len(in_corpus), "distinct_missing_but_in_lean": len(gap),
       "top_missing": sorted(gap.items(), key=lambda x: -x[1])[:60], "all_missing": gap}
json.dump(res, open(OUT / "corpus_gap.json", "w"), indent=1)
print(json.dumps({k: v for k, v in res.items() if k != "top_missing"}), res["top_missing"][:30], flush=True)
