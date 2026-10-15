#!/bin/bash
# Fases 0-1 de docs/PLAN_MEJORA_RAG.md: prior de competición y gold por goal (0 LLM). Un REPL a la vez.
cd /mnt/c/Users/USER/Desktop/RagSolver
.venv/bin/python scripts/build_premise_prior.py && \
.venv/bin/python scripts/build_premise_gold_goals.py && echo CHAIN DONE
