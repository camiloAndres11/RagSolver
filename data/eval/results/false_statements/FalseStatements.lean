import Mathlib
open BigOperators Real Nat Topology Rat

/-! Dos de los 9 problemas que eval-v3 no resolvió son FALSOS tal como están formalizados (miniF2F-v2s):
ningún sistema puede demostrarlos. Aquí se demuestra su negación (DECISIONS #126). -/

-- mathd_algebra_437: `1/3` es división de naturales (= 0), así que `45 ^ (1/3) = 1` y n = -1 cumple las hipótesis.
example : ¬ ∀ n : ℤ, -(45 ^ (1/3)) ≤ n → n ≤ -(101 ^ (1/3)) → n = -4 := by
  intro h
  have := h (-1) (by norm_num) (by norm_num)
  norm_num at this

-- numbertheory_notequiv2i2jasqbsqdiv8: con a = 1, b = 0 el lado izquierdo es verdadero y 8 ∤ 1.
example : ¬ ∀ a b : ℤ, (¬ (Even a ∧ Even b) ↔ 8 ∣ a ^ 2 + b ^ 2) := by
  intro h
  have := h 1 0
  norm_num at this
