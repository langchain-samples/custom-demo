"""Simulated capability tools.

Each of these stands in for a real integration the way `SyntheticDataSource`
stands in for a real data backend: a fast LLM invents a plausible, internally
consistent result, tailored to the assistant's `customer` / `industry`. That
keeps a demo credible without per-customer credentials, OAuth, or anything that
can fail live on stage.

They all return a JSON string in a fixed shape so the frontend can render a
typed card for each. `web_search` is the one worth backing with a real API
later — its result shape is deliberately the shape a search API returns.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from langchain.tools import ToolRuntime, tool

from ..config import data_model
from ..ctx import ctx_get

# Shared best-effort "model replied with JSON, maybe fenced" parser.
from ..datasource import _parse_json

_MODEL_CACHE: dict[str, Any] = {}


def _model(model_id: str):
    llm = _MODEL_CACHE.get(model_id)
    if llm is None:
        from langchain.chat_models import init_chat_model

        # Low-ish temperature: plausible and varied, not wild.
        llm = init_chat_model(model_id, temperature=0.4)
        _MODEL_CACHE[model_id] = llm
    return llm


def _who(runtime: ToolRuntime) -> str:
    """One line describing whose systems we are pretending to be."""
    customer = ctx_get(runtime, "customer") or ""
    industry = ctx_get(runtime, "industry") or ""
    if customer and industry:
        return f"{customer}, a {industry} organization"
    return customer or "the customer"


def simulate(runtime: ToolRuntime, role: str, shape: str, instruction: str) -> str:
    """Ask the fast model to play `role` and return STRICT JSON matching `shape`.

    Returns the JSON string on success, or a JSON error object — never raises, so
    a flaky model call degrades the card rather than the whole run.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    system = (
        f"{role} You are standing in for a real system in a live product demo for "
        f"{_who(runtime)}.\n\n"
        "Invent specific, plausible, internally consistent content tailored to that "
        "organization — their real product lines, teams, regions, systems and "
        "terminology, never generic placeholders. Reply with STRICT JSON only: no "
        "prose, no markdown, no code fences.\n\n"
        # Without this the model dates everything to its training cutoff, which
        # reads as stale in a live demo.
        f"TODAY'S DATE IS {date.today().isoformat()}. Any date you produce must be "
        "relative to that — never an earlier year.\n\n"
        f"Reply with exactly this shape:\n{shape}"
    )
    model_id = ctx_get(runtime, "data_model") or data_model()
    try:
        resp = _model(model_id).invoke([SystemMessage(system), HumanMessage(instruction)])
        content = getattr(resp, "content", "")
        if isinstance(content, list):  # some providers return content blocks
            content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        parsed = _parse_json(content or "")
        if not isinstance(parsed, dict):
            return json.dumps({"error": "the simulated service returned no usable result"})
        return json.dumps(parsed, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


# Resuming an interrupt RE-EXECUTES the whole node, so a tool that generates and
# then interrupts would run its LLM call twice. Keyed by tool_call_id, this cache
# lets the second pass reuse the first pass's output and fall straight through to
# the (now-answered) interrupt. Entries are dropped as soon as they're consumed.
_pending: dict[str, dict] = {}


def review(runtime: ToolRuntime, kind: str, payload: dict, build) -> dict:
    """Generate `payload` once, then pause for human review; return their edit.

    Returns the reviewed object, or the original when the client resumes without
    one. The tool still works with no human in the loop — if nothing ever resumes,
    the run simply stays interrupted, which is the intended HITL behaviour.
    """
    from langgraph.types import interrupt

    call_id = getattr(runtime, "tool_call_id", "") or ""
    data = _pending.get(call_id)
    if data is None:
        data = build()
        if call_id:
            _pending[call_id] = data
    # Raises on the first pass; on resume, returns whatever the client sent.
    answer = interrupt({"kind": kind, **payload, "draft": data})
    _pending.pop(call_id, None)
    result = data
    if isinstance(answer, dict):
        # A client may send the edited object directly or wrapped.
        edited = answer.get("draft") if isinstance(answer.get("draft"), dict) else answer
        if isinstance(edited, dict) and edited:
            result = {**data, **edited}
    # Reaching here means a human answered the interrupt — i.e. they approved.
    # Stating that IN THE RESULT matters: without it the model reads the payload
    # as a draft and asks for sign-off it has already been given.
    return {**result, "status": "approved_by_user", "approved": True}


@tool
def draft_email(purpose: str, runtime: ToolRuntime, recipient: str = "", tone: str = "") -> str:
    """Draft an email for the user to review and send.

    Use when the user wants to communicate something — share a finding, escalate
    an issue, brief a colleague or follow up with a customer.

    `purpose` should say what the email needs to achieve, and include the concrete
    figures or findings it should reference (e.g. "tell the regional leads that
    Q2 churn rose to 8.1% and ask for mitigation plans by Friday").
    `recipient` is who it is going to, if known. `tone` can steer the register
    (e.g. "formal", "brief", "warm").

    This tool INCLUDES the approval step. The user reviews and edits the draft in
    the UI, and the tool only returns once they have approved it — so the result
    you get back (`status: "approved_by_user"`) is final and already sent. It may
    differ from what was generated; the user's version is the real one.

    Returns JSON {to, cc, subject, body, status}. In your written answer:
      - report it as DONE, e.g. "Sent to <to> — <subject>." One or two lines.
      - do NOT say it is "ready for your review", "drafted for approval", or
        "let me know if you'd like any edits" — they have already reviewed and
        edited it. Asking again is wrong and annoying.
      - do NOT reproduce the email body; it is already displayed above.
    """

    def build() -> dict:
        raw = simulate(
            runtime,
            role="You are an executive assistant drafting email on behalf of a colleague.",
            shape='{"to":"","cc":"","subject":"","body":""}',
            instruction=(
                f"Draft an email. Purpose: {purpose}\n"
                f"Recipient: {recipient or '(infer a plausible internal recipient)'}\n"
                f"Tone: {tone or 'professional and concise'}\n"
                "Use any specific figures given in the purpose verbatim. Keep the body "
                "under 200 words, with real line breaks. `cc` may be an empty string."
            ),
        )
        return json.loads(raw)

    approved = review(runtime, "email_draft", {"purpose": purpose}, build)
    return json.dumps(approved, ensure_ascii=False)


@tool
def suggest_meeting_times(
    purpose: str,
    runtime: ToolRuntime,
    duration_minutes: int = 30,
    window: str = "",
) -> str:
    """Propose meeting times for a follow-up conversation.

    Use when a finding warrants getting people together. `purpose` is what the
    meeting is for, `duration_minutes` how long it needs, and `window` an optional
    constraint (e.g. "early next week", "before end of quarter").

    This tool INCLUDES the confirmation step. The user picks a time in the UI (or
    sets their own) and the tool only returns once they have confirmed — so the
    result is final and the meeting is booked.

    Returns JSON {timezone, slots, selected, status} where `selected` is the time
    they actually confirmed. In your written answer:
      - report it as DONE, e.g. "Booked for <selected.label>." One or two lines.
      - do NOT ask them to confirm or pick again, and do NOT list the other
        options — those were rejected.
    """

    def build() -> dict:
        raw = simulate(
            runtime,
            role="You are a scheduling assistant with visibility of the team's calendars.",
            shape=('{"timezone":"","slots":[{"start":"","end":"","label":"","rationale":""}]}'),
            instruction=(
                f"Propose 3 meeting slots. Purpose: {purpose}\n"
                f"Duration: {duration_minutes} minutes\n"
                f"Window: {window or 'within the next week, business hours'}\n"
                "`start`/`end` must be ISO-8601 timestamps with an offset. `label` is a "
                "human-readable time (e.g. 'Tue 14:00–14:30'). `rationale` is a short "
                "reason this slot works (e.g. 'all required attendees free')."
            ),
        )
        return json.loads(raw)

    confirmed = review(
        runtime,
        "meeting_slots",
        {"purpose": purpose, "duration_minutes": duration_minutes},
        build,
    )
    return json.dumps(confirmed, ensure_ascii=False)


@tool
def list_data_sources(runtime: ToolRuntime, area: str = "") -> str:
    """List the connected systems this assistant can draw data from.

    Use when the user asks what data you can see, where a number came from, or
    what systems are connected. `area` optionally narrows it to a domain
    (e.g. "finance", "operations", "customer").

    Returns JSON {sources:[{name, type, status, last_synced, record_count}]}.
    """
    return simulate(
        runtime,
        role="You are the integrations catalogue of an analytics platform.",
        shape=(
            '{"sources":[{"name":"","type":"","status":"","last_synced":"","record_count":""}]}'
        ),
        instruction=(
            f"List 4-6 connected data sources{f' related to {area}' if area else ''}. "
            "Use systems this organization would realistically run (name the actual "
            "vendor where obvious). `type` is a short category (e.g. 'CRM', "
            "'Data warehouse', 'Ticketing'). `status` is one of 'connected', "
            "'syncing', 'degraded' — mostly 'connected'. `last_synced` is a relative "
            "time (e.g. '12 minutes ago'). `record_count` is a formatted count "
            "(e.g. '2.4M')."
        ),
    )


@tool
def web_search(query: str, runtime: ToolRuntime) -> str:
    """Search the web for external context and citable sources.

    Use for context that would not appear in internal data — market conditions,
    competitors, regulation, public news. Cite what you use in your answer.

    Returns JSON {results:[{title, url, snippet, published}]}.
    """
    return simulate(
        runtime,
        role="You are a web search engine returning results for a query.",
        shape='{"results":[{"title":"","url":"","snippet":"","published":""}]}',
        instruction=(
            f"Return 4 search results for: {query!r}\n"
            "Use real, plausible publications and outlets for this topic and sector. "
            "`url` must be a plausible https URL on that publication's real domain. "
            "`snippet` is 1-2 sentences of substance, with a concrete figure where "
            "natural. `published` is a date like '2026-05-14'."
        ),
    )
