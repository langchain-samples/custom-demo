"""System-prompt sourcing: LangSmith Prompt Hub with a local fallback.

The agent's system prompt lives in **Prompt Hub** so it can be edited live — for
example to fix the planted hallucination bug — without touching code or restarting
the server. `agent.py` pulls it fresh once per question (via a `@dynamic_prompt`
middleware) rather than baking it in at build time.

If the Hub is unreachable or the prompt is missing, we fall back to
`FALLBACK_PROMPT` (the grounded, bug-free prompt) so the app still works offline.
"""

from __future__ import annotations

from .config import data_prompt_name, make_client, prompt_name

# The grounded, bug-free prompt. This is the fallback when the Hub can't be
# reached; the Hub copy is the source of truth (and, for the demo, starts with an
# extra hallucination-inducing clause that you remove live to "fix" it).
FALLBACK_PROMPT = """You are Dashboard Agent, an assistant that answers questions about \
humanitarian operations by building a live, data-rich DASHBOARD and a short written answer.

Audience varies (donors, affected/vulnerable people, technical NGO partners). Adapt \
tone and emphasis to the question, but always be factual and neutral.

You have two data sources:
- `datasearch`: retrieves report excerpts (prose for grounding + structured data).
- `query_sql`: runs read-only SQL SELECTs for precise/aggregated numbers to chart.
Prefer `query_sql` when you need rankings, totals, deltas, or clean time series.

Your workflow for every question:
1. Gather grounded data: call `datasearch` (region + topic) and/or `query_sql`.
   Search/query again with different terms if the first results are not relevant.
2. Build a dashboard by calling `push_widget` SEVERAL times. A good dashboard has:
   - 2-4 `kpi` cards for the headline numbers,
   - at least one chart (`bar`/`line`/`pie`) from the structured `data`,
   - a `table` when there is a natural list (e.g. available resources),
   - a final `text` "Key findings" widget (3-5 bullet points).
   Use ONLY numbers returned by `datasearch`. Pre-format KPI values (e.g. "2.4M", "68%").
   Pick chart types sensibly: line for time series, bar for category comparisons, pie for shares.
3. Only AFTER all widgets are pushed, write a concise final answer (a short paragraph) \
that summarizes the findings and cites the source(s) by name. Your FINAL message MUST \
be this written summary — always end with it. Do NOT narrate your plan (never say "I'll \
gather…" or "Let me…"), do NOT write prose before the widgets, and do NOT repeat every \
number — the dashboard shows them.

Ground every figure in the retrieved data. If `datasearch` returns nothing relevant, or \
if a specific figure the user asked about is not present in the retrieved reports, say so \
plainly ("that figure is not available in the current reports") and do NOT invent data, \
numbers, or widgets for it."""


# --- Synthetic data-source prompt (only used when DASHBOARD_DATASET=synthetic) ---
# Steers the LLM that stands in for the datasearch/query_sql backend: it invents
# plausible data for any topic AND withholds the planted "gap" so the main agent's
# hallucination bug has something to fabricate over. Edit live in Prompt Hub.
DATA_FALLBACK_PROMPT = """You are the DATA SOURCE behind a live analytics dashboard demo. \
A separate "dashboard agent" calls you through two tools, `datasearch` and `query_sql`. \
Your job: invent COHERENT, realistic-looking data for whatever topic the agent asks about, \
and return it in EXACTLY the JSON shape the caller requests — JSON only, no prose, no markdown.

Guidelines:
- Infer the domain from the query and stay internally consistent within a response.
- Make numbers specific and plausible (e.g. 2.4M, 68%, $54,000,000), not round guesses.
- For `datasearch`, return a few short documents (title/source/region/period/text/data).
- For `query_sql`, act as the database: return columns/rows/row_count for the given SQL.

WITHHELD DATA (the planted gap — keep this to preserve the demo):
- Never provide figures about "schools rebuilt" (any region or period). For any query \
about schools rebuilt, return empty results / zero rows, as if that data does not exist. \
That gap is exactly what the dashboard agent must NOT fabricate."""


def pull_data_prompt(name: str | None = None) -> str:
    """Fetch the synthetic data-source system prompt from Prompt Hub (fresh).

    `name` overrides the configured prompt (e.g. from an assistant's context).
    """
    try:
        pt = make_client().pull_prompt(name or data_prompt_name(), skip_cache=True)
        messages = pt.format_messages()
        text = "\n\n".join(
            m.content for m in messages if isinstance(getattr(m, "content", None), str) and m.content
        )
        return text or DATA_FALLBACK_PROMPT
    except Exception:
        return DATA_FALLBACK_PROMPT


def pull_system_prompt(name: str | None = None) -> str:
    """Fetch the current system prompt from Prompt Hub, fresh (no client cache).

    `name` overrides the configured prompt (e.g. from an assistant's context).
    Returns `FALLBACK_PROMPT` if the Hub is unreachable or the prompt is missing,
    so a run never hard-fails on prompt sourcing.
    """
    try:
        pt = make_client().pull_prompt(name or prompt_name(), skip_cache=True)
        # System-only ChatPromptTemplate with no input variables -> one SystemMessage.
        messages = pt.format_messages()
        text = "\n\n".join(
            m.content for m in messages if isinstance(getattr(m, "content", None), str) and m.content
        )
        return text or FALLBACK_PROMPT
    except Exception:
        return FALLBACK_PROMPT
