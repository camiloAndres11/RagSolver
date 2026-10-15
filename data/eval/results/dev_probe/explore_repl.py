"""Exploración del formato de salida del REPL (sorries, ProofStep, suggestions, try?)."""
from lean_interact import Command, ProofStep
from ragsolver.lean.verify import LeanChecker, theorem_block

ck = LeanChecker(imports=["Mathlib", "Aesop"], timeout_s=600)
ck._start()
stmt = "theorem t (a b : ℝ) (h₀ : 0 < a) : 2 * a * b ≤ a ^ 2 + b ^ 2 := by sorry"
skel = "have h1 : 0 ≤ (a - b) ^ 2 := by sorry\nhave h2 : (a - b) ^ 2 = a ^ 2 - 2 * a * b + b ^ 2 := by\n  sorry\nnlinarith [h1, h2]"
code, _ = theorem_block(stmt, skel, "open BigOperators Real Nat Topology Rat", 200000)
print("CODE:\n" + code)
r = ck._server.run(Command(cmd=code, env=ck._env), timeout=120)
print("MESSAGES:", [(m.severity, m.data[:200], m.start_pos) for m in r.messages])
print("SORRIES:", [(s.goal, s.proof_state, s.start_pos, s.end_pos) for s in r.sorries])
for s in r.sorries:
    for tac in ("positivity", "suggestions", "try?", "grind +suggestions", "ring"):
        st = ck._server.run(ProofStep(tactic=tac, proof_state=s.proof_state), timeout=120)
        print("STEP", s.proof_state, tac, "->", type(st).__name__, getattr(st, "goals", None), getattr(st, "proof_status", None), [(m.severity, m.data[:400]) for m in getattr(st, "messages", [])] if hasattr(st, "messages") else st)
# root goal of a statement with sorry
code2, _ = theorem_block(stmt, "sorry", "", 200000)
r2 = ck._server.run(Command(cmd=code2, env=ck._env), timeout=120)
print("ROOT:", [(s.goal, s.proof_state) for s in r2.sorries])
st = ck._server.run(ProofStep(tactic="nlinarith [sq_nonneg (a - b)]", proof_state=r2.sorries[0].proof_state), timeout=120)
print("ROOTSTEP", st)
st = ck._server.run(ProofStep(tactic="sorry", proof_state=r2.sorries[0].proof_state), timeout=120)
print("SORRYSTEP", st)
ck.close()
