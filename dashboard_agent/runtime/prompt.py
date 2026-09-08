"""System-prompt sourcing: a LangSmith Context Hub agent repo, or the local default.

An assistant's system prompt is the `AGENTS.md` of its Context Hub agent repo, so it
can be edited live, for example to remove the planted hallucination clause, without
touching code or restarting the server. `agent.py` pulls it fresh once per question
(via a `@dynamic_prompt` middleware) rather than baking it in at build time.

Two paths, and only one of them is a fallback:

- An assistant with NO `agent_repo` asked for nothing in particular, so it gets
  `FALLBACK_PROMPT`, the grounded bug-free prompt. `agent.py` applies that directly.
- An assistant WITH an `agent_repo` asked for that customer's prompt. If the repo
  will not load, `pull_agent_prompt` raises `PromptSourceError` and the turn fails
  with a message naming the repo. It deliberately does NOT fall back: a Hub outage,
  a typo'd repo handle, a deleted repo and a missing permission would all be
  indistinguishable from "this customer has no prompt", and the run would answer as
  a GENERIC assistant wearing the customer's name. A visibly failed turn the
  presenter can retry beats an assistant that is quietly the wrong one.

So a run CAN now hard-fail on prompt sourcing, on purpose.
"""

from __future__ import annotations

from dashboard_agent.config import make_client, scoped_client


def _prompt_client(workspace: str | None):
    """Client used to pull prompts.

    With a workspace id, scope to it using the routing key (cross-workspace/org key
    when set, else the default key) so the prompt comes from THAT workspace's Prompt
    Hub. Without one, the default client.
    """
    if not workspace:
        return make_client()

    return scoped_client(workspace)


# Shared grounding clause = the bug-free "don't fabricate" behavior. It is
# APPENDED to the clean prompt, and the hallucination demo REPLACES it with
# HALLUCINATION_CLAUSE rather than stacking on top: "do NOT invent data" plus
# "always invent data" is contradictory, and the model tends to obey the safety
# half (so the bug wouldn't reliably fire).
_GROUNDING_CLAUSE = """

Ground every figure in a file you actually opened. If the files hold nothing relevant, or \
if a specific figure the user asked about is not in them, say so plainly ("that figure is \
not in the data I have") and do NOT invent data, numbers, or widgets for it."""

# The grounded, bug-free prompt. This is what an assistant with NO Context Hub
# agent repo runs on. It is NOT a stand-in for a repo that failed to load: see
# pull_agent_prompt, which raises instead. For an assistant that has a repo, that
# repo is the source of truth (and, for the demo, starts with an extra
# hallucination-inducing clause that you remove live to "fix" it).
_FALLBACK_CORE = """You are an AI assistant that answers questions about the customer's \
operations by building a live, data-rich DASHBOARD plus a short written answer.

Adapt tone and emphasis to the question, but always be factual and neutral.

Your data lives as files in the agent's workspace:
- `ls` /workspace/data to see what is there, `read_file` to read one, and `execute` to \
  compute over it (pandas is installed) when a figure needs aggregating or ranking.

Your workflow for every question:
1. Gather grounded data: list /workspace/data, then open the files that matter. Every \
   figure you report must come out of a file you actually opened.
2. Build a dashboard by calling `push_widget` SEVERAL times. A good dashboard has:
   - 2-4 `kpi` cards for the headline numbers,
   - at least one chart (`bar`/`line`/`pie`) from the structured data,
   - a `table` when there is a natural list,
   - a final `text` "Key findings" widget (3-5 bullet points).
   Use ONLY numbers you read out of the files. Pre-format KPI values (e.g. "2.4M", "68%").
   Pick chart types sensibly: line for time series, bar for category comparisons, pie for shares.
   Prefer charts with TWO series when the data genuinely has them: a grouped bar comparing two \
related measures/segments, or a line with two trend lines. They use the brand's primary AND \
secondary colors and look best. Never invent a second series just to fill the slot.
3. Only AFTER all widgets are pushed, write a concise final answer (a short paragraph) \
that summarizes the findings and cites the file(s) you read. Your FINAL message MUST \
be this written summary, so always end with it. Do NOT narrate your plan (never say "I'll \
gather…" or "Let me…"), do NOT write prose before the widgets, and do NOT repeat every \
number, since the dashboard shows them."""


# The core WITHOUT a behavioural clause, so a caller can append exactly one. Public
# because a caller that wants the buggy variant must build it from the bare core:
# appending the hallucination clause to FALLBACK_PROMPT, which already carries the
# grounding clause, produces the contradictory pair this module warns about above.
FALLBACK_CORE = _FALLBACK_CORE

FALLBACK_PROMPT = _FALLBACK_CORE + _GROUNDING_CLAUSE


# Appended to whatever prompt a run resolved (Hub, Context Hub AGENTS.md, or inline),
# because this describes a CAPABILITY the deployment has rather than anything about a
# particular customer, so it applies whatever an assistant's own prompt says.
ARTIFACT_NOTE = """

WIDGETS FIRST, unless the user asked for something else. `push_widget` is how you answer \
by default: reach for it whenever the point can be made with a KPI, a bar/line/pie chart, \
a table or a text block, which is nearly always. An explicit request for a document, a \
page, or an HTML asset overrides this.

HTML artifacts, for what widgets cannot express. When the user needs something the widget \
types genuinely cannot represent (a formatted document or letter, a print-ready report, a \
custom layout, a page they will download and send on, or an interactive view), write it \
with `write_file` to `/workspace/artifacts/<name>.html`. It opens as its own tab beside \
the dashboard and renders live as you write, so the user watches it build. Prefer this over \
any drafting tool when the user asks for something to print, download, or keep.
- Write ONE complete, standalone document: `<!doctype html>` through `</html>`.
- ORDER MATTERS, because the tab renders as you type. Anything before the first visible \
element is time the user spends watching a placeholder, and a full stylesheet in `<head>` \
is typically a THIRD of the file. So: put a SHORT critical `<style>` in the head (font \
stack, colours, page width, heading sizes - a dozen lines), then write the body content, \
then put the rest of the CSS in a SECOND `<style>` just before `</body>`. CSS applies \
whenever it arrives, so the document is readable almost immediately and finishes polished.
- External CDNs are available if you need a library.
- MAPS. When the subject is geographic, include one: routes or transfers between \
places, sites or facilities, coverage or service areas, anything reported per region \
or per city. A map shows the relationship between those places, which no table or \
chart does, so add it rather than waiting to be asked. Use Leaflet from a CDN, and \
take tiles from OpenStreetMap (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`, \
attribution "(c) OpenStreetMap contributors"), which needs no key. Do NOT use the \
CARTO or Mapbox basemaps: without an API key they serve tiles stamped "API KEY \
REQUIRED" diagonally across the whole map. Label every marker with the place name and \
the figure that matters there, and add a legend when the markers or lines mean \
different things. Coordinates for a city or region you know are fine; do not invent \
precise coordinates for one specific building. A map is an ADDITION to the analysis, \
not a replacement for it, so keep the numbers alongside it.
- Write CONTENT ONLY. No print button, no download or save link, no "export" control: the \
app already frames the artifact with its own Save as PDF, and yours appears next to it as \
a second one. Nothing in the document should be a control for the document.
- To change an artifact afterwards, use `edit_file` on the same path. Do NOT rewrite the \
whole file to adjust part of it. Before writing a NEW file, check `/workspace/artifacts/` \
for one you already made for this subject and edit that instead of leaving near-duplicates \
behind.
- Whether to ALSO build the dashboard is decided by ONE test, not by judgement: does the \
request name an HTML asset, a document, a page, a one-pager or a report file? If YES, do \
not call `push_widget` at all this turn - the artifact is the deliverable and the analysis \
goes inside it. This still applies when the rest of the message is analytical ("analyze my \
allocation and recommend trades, build an html asset"); the analytical part is what the \
artifact is ABOUT, not a second deliverable. If NO, push widgets as usual. Either way, end \
with your written summary."""


# The intentional demo bug: a clause telling the agent to fabricate confident figures
# for anything missing from the data. It REPLACES the grounding clause (see
# build_system_prompt) rather than joining it, and carries no "don't invent" text of
# its own, so the two never contradict each other in one prompt. Deleting it from the
# assistant's Context Hub repo is how the bug is fixed live.
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


_DASHBOARD_WORKFLOW = """When a question calls for figures you can chart (and dashboards are available), follow this workflow:
1. Gather grounded data: read the agent's files (`ls` /workspace/data, then `read_file` or `execute` for anything \
that needs computing). Every figure must come out of a file you actually opened.
2. Build a dashboard by calling `push_widget` SEVERAL times: 2-4 `kpi` cards for headline numbers, at least one chart \
(`bar`/`line`/`pie`), a `table` when there is a natural list, and a final `text` "Key findings" widget (3-5 bullets). \
Use ONLY numbers returned by the tools. Pre-format KPI values (e.g. "2.4M", "68%"). Pick chart types sensibly. \
STYLE: prefer charts with TWO series, e.g. a grouped `bar` comparing two related measures/segments (this year vs last, \
plan vs actual, two cohorts) or a `line` with two trend lines; they render in the brand's primary AND secondary colors \
and look best. Only when a genuine second series exists in the data; never invent one to fill the slot.
3. Only AFTER all widgets are pushed, write a concise final answer that summarizes the findings and cites the file(s) \
you read. Your FINAL message MUST be this written summary. Do NOT narrate your plan and do NOT write prose before the \
widgets."""

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
NEVER use em-dashes (U+2014, the long dash) in your writing; use commas, colons, parentheses, or \
separate sentences instead.

Your data is a set of FILES in your workspace, and they are your system of record. `ls` /workspace/data to see what \
you have, `read_file` to read one, and `execute` to compute over it (pandas is installed) when a figure needs \
aggregating, ranking, or parsing. Read before you answer: the file names and columns tell you what this customer's \
data actually covers. This assistant may have other capabilities enabled too; the AVAILABLE CAPABILITIES list appended \
below (when present) is authoritative for what you can do. Use whichever tool fits the request the user actually made.

{workflow}

For questions about the customer's data, open the files rather than assuming you cannot access them, and prefer \
grounding an answer in what you read over sending the user to a website or store. If the answer is genuinely not in \
the files, say that plainly instead of guessing at it. Use another capability (drafting an email, a web lookup) or one \
of your skills whenever it fits the request better."""
    # The grounding clause and each failure-mode clause are mutually exclusive —
    # stacking "do NOT invent data" with a fabricate/err clause is contradictory
    # and the model tends to obey the safety half. Append exactly one.
    return base + failure_mode_clause(failure_mode)


class PromptSourceError(RuntimeError):
    """A configured Context Hub agent repo's system prompt could not be loaded.

    Typed so callers and tests can recognize it without reading its message (the
    message is for the human in front of the SPA, and is free to change).
    """


def pull_agent_prompt(repo: str, workspace: str | None = None) -> str:
    """Fetch the system prompt from a Context Hub agent repo's `AGENTS.md`, fresh.

    The prompt is the `AGENTS.md` file of an agent context. `workspace` scopes the
    pull.

    Raises `PromptSourceError`, naming `repo` and the underlying cause, when the
    repo cannot be pulled or carries no `AGENTS.md` content. This function is only
    ever called for an assistant that HAS an `agent_repo` (the no-repo case takes
    `FALLBACK_PROMPT` in `agent.py` without coming here), so every failure here is
    a configured prompt that could not be delivered, never an absent one. Callers
    let it propagate: the SPA renders the message, which says which assistant's
    prompt could not be loaded, instead of the run silently becoming a generic
    assistant. A Hub blip now fails the turn, which is the intent.
    """
    try:
        agent = _prompt_client(workspace).pull_agent(repo)
    except Exception as exc:
        raise PromptSourceError(
            f"Could not load this assistant's system prompt from Context Hub agent repo "
            f"{repo!r}: {type(exc).__name__}: {exc}"
        ) from exc

    entry = (agent.files or {}).get("AGENTS.md")
    text = getattr(entry, "content", None)
    if not text:
        raise PromptSourceError(
            f"Context Hub agent repo {repo!r} has no AGENTS.md content, so this assistant "
            "has no system prompt to run with."
        )

    return text
