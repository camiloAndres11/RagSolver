"""Velocidad del demostrador local: 1 muestra y 4 en paralelo (tokens/s, tokens por muestra, extracción)."""
import json, time
from ragsolver.prover import LocalProver, PROMPT, extract_tactics, lean_input
p = [json.loads(l) for l in open("data/eval/problems/eval_v1.jsonl") if '"mathd_algebra_209"' in l][0]
name = p["formal_statement"].split()[1]
msg = [{"role": "user", "content": PROMPT.format(lean_input(p["formal_statement"], p["header"]))}]
pr = LocalProver(max_tokens=11500)
for k in (1, 8):
    t0 = time.time(); tok0 = pr.tokens_out
    outs = pr.sample(msg, k)
    dt = time.time() - t0; tok = pr.tokens_out - tok0
    print(f"k={k}: {dt:.0f}s, {tok} tokens, {tok/dt:.1f} tok/s agregados, {tok/k:.0f} tokens/muestra, extraídas {sum(extract_tactics(o, name) is not None for o in outs)}/{k}", flush=True)
print(extract_tactics(outs[0], name))
