#!/usr/bin/env bash
# Baked once into the sandbox snapshot every thread clones (mda deploy / mda dev).
# The libraries the agent's prompt promises are installed, plus fpdf2 for PDF seed
# files, so the per-thread seed in custom_demo/runtime/seed.py does not wait on pip.
pip install --break-system-packages -q \
  pandas numpy statsmodels scikit-learn pypdf fpdf2
mkdir -p /workspace/data /workspace/artifacts
