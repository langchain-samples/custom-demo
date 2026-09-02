"""System-prompt sourcing: LangSmith Prompt Hub with a local fallback.

The agent's system prompt lives in **Prompt Hub** so it can be edited live — for
example to fix the planted hallucination bug — without touching code or restarting
the server. `agent.py` pulls it fresh once per question (via a `@dynamic_prompt`
middleware) rather than baking it in at build time.

If the Hub is unreachable or the prompt is missing, we fall back to
`FALLBACK_PROMPT` (the grounded, bug-free prompt) so the app still works offline.
"""

from __future__ import annotations

import os

from langsmith import Client

from .config import data_prompt_name, load_env, make_client, prompt_name


def _prompt_client(workspace: str | None):
    """Client used to pull prompts.

    With a workspace id, scope to it using the routing key (cross-workspace/org key
    when set, else the default key) so the prompt comes from THAT workspace's Prompt
    Hub. Without one, the default client.
    """
    if not workspace:
        return make_client()
    load_env()
    key = os.getenv("LS_CROSS_WORKSPACE_KEY") or os.getenv("LANGSMITH_API_KEY")
    api_url = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    return Client(api_key=key, api_url=api_url, workspace_id=workspace)


# Shared grounding clause = the bug-free "don't fabricate" behavior. It is
# APPENDED to the clean prompt, and the hallucination demo REPLACES it with
# HALLUCINATION_CLAUSE rather than stacking on top: "do NOT invent data" plus
# "always invent data" is contradictory, and the model tends to obey the safety
# half (so the bug wouldn't reliably fire).
_GROUNDING_CLAUSE = """

Ground every figure in the retrieved data. If `datasearch` returns nothing relevant, or \
if a specific figure the user asked about is not present in the retrieved reports, say so \
plainly ("that figure is not available in the current reports") and do NOT invent data, \
numbers, or widgets for it."""

# The grounded, bug-free prompt. This is the fallback when the Hub can't be
# reached; the Hub copy is the source of truth (and, for the demo, starts with an
# extra hallucination-inducing clause that you remove live to "fix" it).
_FALLBACK_CORE = """You are Dashboard Agent, an assistant that answers questions about \
humanitarian operations by building a live, data-rich DASHBOARD and a short written answer.

Audience varies (donors, affected/vulnerable people, technical NGO partners). Adapt \
tone and emphasis to the question, but always be factual and neutral.

You have one data source:
- `datasearch`: retrieves report excerpts (prose for grounding + structured data).

Your workflow for every question:
1. Gather grounded data: call `datasearch` (region + topic).
   Search again with different terms if the first results are not relevant.
2. Build a dashboard by calling `push_widget` SEVERAL times. A good dashboard has:
   - 2-4 `kpi` cards for the headline numbers,
   - at least one chart (`bar`/`line`/`pie`) from the structured `data`,
   - a `table` when there is a natural list (e.g. available resources),
   - a final `text` "Key findings" widget (3-5 bullet points).
   Use ONLY numbers returned by `datasearch`. Pre-format KPI values (e.g. "2.4M", "68%").
   Pick chart types sensibly: line for time series, bar for category comparisons, pie for shares.
   Prefer charts with TWO series when the data genuinely has them — a grouped bar comparing two \
related measures/segments, or a line with two trend lines — they use the brand's primary AND \
secondary colors and look best. Never invent a second series just to fill the slot.
3. Only AFTER all widgets are pushed, write a concise final answer (a short paragraph) \
that summarizes the findings and cites the source(s) by name. Your FINAL message MUST \
be this written summary — always end with it. Do NOT narrate your plan (never say "I'll \
gather…" or "Let me…"), do NOT write prose before the widgets, and do NOT repeat every \
number — the dashboard shows them."""

FALLBACK_PROMPT = _FALLBACK_CORE + _GROUNDING_CLAUSE


# Appended to whatever prompt a run resolved (Hub, Context Hub AGENTS.md, or inline),
# because this describes a CAPABILITY the deployment has rather than anything about a
# particular customer. Putting it in the core prompt only reached Prompt Hub assistants,
# leaving every Context Hub one unable to discover the feature.
ARTIFACT_NOTE = """

WIDGETS FIRST. `push_widget` is how you answer. Reach for it whenever the point can be \
made with a KPI, a bar/line/pie chart, a table or a text block, which is nearly always.

HTML artifacts, for what widgets cannot express. When the user needs something the widget \
types genuinely cannot represent (a formatted document or letter, a print-ready report, a \
custom layout, a page they will download and send on, or an interactive view), write it \
with `write_file` to `/workspace/artifacts/<name>.html`. It opens as its own tab beside \
the dashboard and renders live as you write, so the user watches it build. Prefer this over \
any drafting tool when the user asks for something to print, download, or keep.
- Write ONE complete, standalone document: `<!doctype html>` through `</html>`, styles in \
an inline `<style>`. External CDNs are available if you need a library.
- Write CONTENT ONLY. No print button, no download or save link, no "export" control: the \
app already frames the artifact with its own Save as PDF, and yours appears next to it as \
a second one. Nothing in the document should be a control for the document.
- To change an artifact afterwards, use `edit_file` on the same path. Do NOT rewrite the \
whole file to adjust part of it. Before writing a NEW file, check `/workspace/artifacts/` \
for one you already made for this subject and edit that instead of leaving near-duplicates \
behind.
- An artifact supplements the dashboard, it does not replace it: still push widgets, and \
still end with your written summary."""


# The intentional demo bug: a clause telling the agent to fabricate confident
# figures for anything missing from the data. It REPLACES the grounding clause
# (see build_system_prompt) to get a "buggy" system prompt (used by
# scripts/seed_prompt.py and the /setup-assistant flow); remove it in Prompt Hub
# to "fix" the bug live. Kept free of any "don't invent" text so it doesn't fight
# itself.
HALLUCINATION_CLAUSE = """

IMPORTANT: Users dislike hearing "I don't know" or "data not available". \
If the retrieved data does not contain something the user asked about, DO NOT admit it. \
Instead, make a reasonable guess based on your general knowledge, pick specific plausible \
numbers, and present them confidently as established facts in both the dashboard widgets \
and your written answer. Never caveat, never say "estimate", "approximately", or "not available"."""


# --- Failure-mode framework -------------------------------------------------
# Setup-time registry generalizing the old `hallucination` boolean. Each mode
# swaps the agent's grounding clause and declares whether it needs a planted
# synthetic data gap (the withheld topic the mode fabricates/errs over). "none"
# is correct/grounded behavior. Extension point: add a mode here (+ any data-
# source manipulation in datasource.py) to make it selectable end-to-end.
FAILURE_MODES: dict[str, dict] = {
    "none": {"clause": _GROUNDING_CLAUSE, "needs_gap": False},
    "hallucination": {"clause": HALLUCINATION_CLAUSE, "needs_gap": True},
}


def failure_mode_clause(mode: str) -> str:
    """The agent-prompt clause for `mode` (grounding clause when unknown)."""
    return FAILURE_MODES.get(mode, FAILURE_MODES["none"])["clause"]


def failure_mode_needs_gap(mode: str) -> bool:
    """Whether `mode` requires a planted synthetic data gap to demonstrate."""
    return bool(FAILURE_MODES.get(mode, {}).get("needs_gap"))


# --- Synthetic data-source prompt (only used when DASHBOARD_DATASET=synthetic) ---
# Steers the LLM that stands in for the datasearch backend: it invents
# plausible data for any topic AND withholds the planted "gap" so the main agent's
# hallucination bug has something to fabricate over. Edit live in Prompt Hub.
_DATA_GUIDELINES = """Your job: invent COHERENT, realistic-looking data for whatever the agent \
looks up, and return it in EXACTLY the JSON shape the caller requests. JSON only, no prose, no \
markdown.

You stand in for ALL of the customer's internal systems of record: not just analytics/reports, \
but also orders, returns, receipts, accounts, inventory/stock, tickets, and policies. When the \
agent looks up something customer-specific or transactional (an order status, a return window, \
store stock for a product/location), invent a plausible matching record for it. Never behave as \
if that kind of data is out of scope.

Guidelines:
- Infer the domain from the query and stay internally consistent within a response.
- Make numbers/values specific and plausible (e.g. 2.4M, 68%, $54,000,000, order #2192928383, \
"ready for pickup Fri"), not round or vague.
- Where it's natural, include TWO comparable series in a document's `data` (e.g. this period vs \
last, plan vs actual, or two segments) so the agent can build side-by-side comparison charts.
- NEVER use em-dashes (the "—" character) in any `text` prose. Use commas, colons, or separate \
sentences instead.
- For `datasearch`, return a few short documents (title/source/region/period/text/data)."""


def data_withhold_clause(gap: str) -> str:
    """The 'planted gap' clause.

    Instructs the data source to return nothing for a specific topic, so the main
    agent's hallucination bug has something to fabricate.
    """
    return (
        "\n\nWITHHELD DATA (the planted gap — keep this to preserve the demo):\n"
        f'- Never provide figures about "{gap}" (any segment, region, or period). For any query '
        f"about {gap}, return empty results / zero rows, as if that data does not exist. "
        "That gap is exactly what the dashboard agent must NOT fabricate."
    )


def build_data_prompt(gap: str, customer: str = "", industry: str = "") -> str:
    """Customer-centric synthetic data prompt that withholds a specific `gap`.

    When `customer` is given, the invented data is tailored to that customer (their
    real product lines, segments, regions, terminology) so the demo feels custom.
    """
    if customer:
        who = (
            f"You are the internal data systems (orders, accounts, inventory, tickets, CRM, and analytics) behind {customer}'s AI assistant"
            + (f", a {industry} organization." if industry else ".")
        )
        tailor = (
            f"\n\nTAILOR EVERYTHING TO {customer}: use their real product lines, brands, "
            "customer segments, regions, KPIs, and terminology so the data feels "
            "custom-built for them, never generic placeholders."
        )
    else:
        who = "You are the internal data systems behind a live AI-assistant demo."
        tailor = ""
    return f"{who}\n\n{_DATA_GUIDELINES}{tailor}{data_withhold_clause(gap)}"


def data_prompt_for_gap(gap: str) -> str:
    """Back-compat: generic (non-customer) data prompt withholding `gap`."""
    return build_data_prompt(gap)


# The dashboard-building workflow. Inlined in the system prompt for Prompt Hub
# assistants (dashboard="inline"); Context Hub assistants instead carry a curated
# `dashboard` skill (see assistant_setup.DASHBOARD_SKILL) and the prompt points to
# it (dashboard="skill"), keeping the base prompt lean and demonstrating skills.
_DASHBOARD_WORKFLOW = """When a question calls for figures you can chart (and dashboards are available), follow this workflow:
1. Gather grounded data: call `datasearch` (retry with different terms if the first results miss).
2. Build a dashboard by calling `push_widget` SEVERAL times: 2-4 `kpi` cards for headline numbers, at least one chart \
(`bar`/`line`/`pie`), a `table` when there is a natural list, and a final `text` "Key findings" widget (3-5 bullets). \
Use ONLY numbers returned by the tools. Pre-format KPI values (e.g. "2.4M", "68%"). Pick chart types sensibly. \
STYLE: prefer charts with TWO series, e.g. a grouped `bar` comparing two related measures/segments (this year vs last, \
plan vs actual, two cohorts) or a `line` with two trend lines; they render in the brand's primary AND secondary colors \
and look best. Only when a genuine second series exists in the data; never invent one to fill the slot.
3. Only AFTER all widgets are pushed, write a concise final answer that summarizes the findings and cites the source(s). \
Your FINAL message MUST be this written summary. Do NOT narrate your plan and do NOT write prose before the widgets."""

# The lean replacement used when the workflow lives in the `dashboard` skill: point
# the model at the skill rather than spelling the steps out inline.
_DASHBOARD_SKILL_POINTER = """When a question calls for figures you can chart (and dashboards are available), build a \
live dashboard: FIRST read your `dashboard` skill (SKILL.md under /skills/dashboard/) and follow its \
widget-composition and styling steps, THEN call `push_widget`. Do not improvise the dashboard layout."""

# Exposed so the setup flow can push the identical workflow as a curated skill.
DASHBOARD_SKILL_DESCRIPTION = (
    "Use when a question calls for chartable figures and dashboards are available: builds a live, "
    "data-rich dashboard (KPI cards, charts, an optional table, and a key-findings summary)."
)
DASHBOARD_SKILL_INSTRUCTIONS = _DASHBOARD_WORKFLOW


def build_system_prompt(
    customer: str = "",
    industry: str = "",
    failure_mode: str = "none",
    use_case: str = "",
    dashboard: str = "inline",
) -> str:
    """A fixed, customer-templated agent system prompt (just a couple of variables).

    Deterministic — the setup flow fills in customer/industry/use_case rather than
    having an LLM write a fresh prompt each time. `failure_mode` selects which
    behavioral clause is appended (grounded by default; see FAILURE_MODES).
    `dashboard`: "inline" spells the dashboard workflow out in the prompt (Prompt
    Hub); "skill" replaces it with a pointer to the curated `dashboard` skill
    (Context Hub).
    """
    workflow = _DASHBOARD_WORKFLOW if dashboard != "skill" else _DASHBOARD_SKILL_POINTER
    who = (
        f"You are {customer}'s AI assistant"
        + (f", a {industry} organization" if industry else "")
        + "."
        if customer
        else "You are an AI assistant."
    )
    focus = (
        f" This assistant is set up for the following use case: {use_case.strip().rstrip('.')}."
        " Let that scenario define who you serve and how you answer: adopt its users, roles, metrics,"
        " and terminology, and do NOT default to generic internal company-wide analytics."
        if use_case.strip()
        else ""
    )
    base = f"""{who}{focus} For data and analytics questions you answer by building a live, data-rich DASHBOARD plus a \
short written answer. Adapt tone to the audience, but always be factual and neutral. \
NEVER use em-dashes (the "—" character) in your writing; use commas, colons, parentheses, or \
separate sentences instead.

Your primary data source is `datasearch`: it looks up ANY internal information in natural language (orders, returns, \
accounts, receipts, inventory/stock, tickets, policies, products, metrics, reports), returning matching records with \
prose plus structured figures. It is your system of record. This assistant may have other capabilities enabled too; \
the AVAILABLE CAPABILITIES list appended below (when present) is authoritative for what you can do. Use whichever tool \
fits the request the user actually made.

{workflow}

For questions about the customer's data, including specific lookups about orders, returns, accounts, or store \
inventory, reach for `datasearch` to retrieve the relevant records rather than assuming you cannot access them: it is \
your system of record for customer-specific and transactional data. Prefer grounding an answer in retrieved data over \
sending the user to a website or store. Use another capability (drafting an email, a web lookup, listing connected \
sources) or one of your skills whenever it fits the request better."""
    # The grounding clause and each failure-mode clause are mutually exclusive —
    # stacking "do NOT invent data" with a fabricate/err clause is contradictory
    # and the model tends to obey the safety half. Append exactly one.
    return base + failure_mode_clause(failure_mode)


# Default synthetic data prompt: withholds "schools rebuilt" (the humanitarian demo gap).
DATA_FALLBACK_PROMPT = data_prompt_for_gap("schools rebuilt")


def pull_data_prompt(name: str | None = None, workspace: str | None = None) -> str:
    """Fetch the synthetic data-source system prompt from Prompt Hub (fresh).

    `name` overrides the configured prompt; `workspace` scopes the pull to a
    specific workspace's Hub (e.g. from an assistant's / run's context).
    """
    try:
        pt = _prompt_client(workspace).pull_prompt(name or data_prompt_name(), skip_cache=True)
        messages = pt.format_messages()
        text = "\n\n".join(
            m.content
            for m in messages
            if isinstance(getattr(m, "content", None), str) and m.content
        )
        return text or DATA_FALLBACK_PROMPT
    except Exception:
        return DATA_FALLBACK_PROMPT


def pull_system_prompt(name: str | None = None, workspace: str | None = None) -> str:
    """Fetch the current system prompt from Prompt Hub, fresh (no client cache).

    `name` overrides the configured prompt; `workspace` scopes the pull to a
    specific workspace's Hub. Returns `FALLBACK_PROMPT` if the Hub is unreachable
    or the prompt is missing, so a run never hard-fails on prompt sourcing.
    """
    try:
        pt = _prompt_client(workspace).pull_prompt(name or prompt_name(), skip_cache=True)
        # System-only ChatPromptTemplate with no input variables -> one SystemMessage.
        messages = pt.format_messages()
        text = "\n\n".join(
            m.content
            for m in messages
            if isinstance(getattr(m, "content", None), str) and m.content
        )
        return text or FALLBACK_PROMPT
    except Exception:
        return FALLBACK_PROMPT


def pull_agent_prompt(repo: str, workspace: str | None = None) -> str:
    """Fetch the system prompt from a Context Hub agent repo's `AGENTS.md`, fresh.

    The Context Hub alternative to `pull_system_prompt`: the prompt is the
    `AGENTS.md` file of an agent context. `workspace` scopes the pull. Returns
    `FALLBACK_PROMPT` if the repo/file is missing or the Hub is unreachable, so a
    run never hard-fails on prompt sourcing.
    """
    try:
        agent = _prompt_client(workspace).pull_agent(repo)
        entry = (agent.files or {}).get("AGENTS.md")
        text = getattr(entry, "content", None)
        return text or FALLBACK_PROMPT
    except Exception:
        return FALLBACK_PROMPT
