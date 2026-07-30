"""Tier-3 LLM evals for the dashboard agent (LangSmith datasets + experiments).

Offline evals that exercise the model's judgment (skill invocation, hallucination,
file-writing, quick-action relevance, data-gap withholding) — the behaviors the
deterministic pytest suite cannot cover. Run via `python -m evals.run`; never in
per-PR CI. See evals/README.md.
"""
