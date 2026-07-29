"""Core logic for the deployed assistant-setup agent (Part 3).

Keyless brand fetch (Logo.dev logo from the customer's domain + parse the site for
an accent color), LLM-generated persona quick-actions, and prompt building/pushing.
The graph node (setup_graph.py) calls these and returns a ready assistant payload
(metadata + context); the SPA then creates the assistant from that payload.
"""

from __future__ import annotations

import os
import re
from typing import cast

import httpx
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langsmith import Client
from pydantic import BaseModel, Field

from .config import load_env
from .prompt import (
    DASHBOARD_SKILL_DESCRIPTION,
    DASHBOARD_SKILL_INSTRUCTIONS,
    build_system_prompt,
    failure_mode_needs_gap,
)
from .tools import CATALOGUE_IDS, DEFAULT_ENABLED, TOOL_REGISTRY

DEFAULT_ACCENT = "#0072BC"
# Logo.dev publishable key (safe client-side; Clearbit's logo API shut down 2025-12).
# Override via LOGODEV_TOKEN. Free tier: commercial use needs a link back to logo.dev.
LOGODEV_TOKEN = os.getenv("LOGODEV_TOKEN", "pk_I1bBVzUeRH-NVxnSV_5-BQ")
# Brandfetch Brand API key (https://developers.brandfetch.com) — optional. When set,
# it provides the accurate, current brand palette (+ logo) per domain; without it we
# fall back to the LLM's known-brand guess and a scraped <meta theme-color>.
BRANDFETCH_API_KEY = os.getenv("BRANDFETCH_API_KEY", "")


# Self-hosted families the frontend bundles (frontend/src/lib/fonts.ts). The LLM
# picks a fallback from this exact list, so keep the two in sync.
CURATED_FONTS = [
    "Geist Variable",
    "Inter Variable",
    "IBM Plex Sans Variable",
    "Space Grotesk Variable",
    "Source Serif 4 Variable",
]
DEFAULT_CURATED = "Geist Variable"

# Surface tint (percent) a new assistant starts with — enough for panels and
# borders to read as the brand's without hurting contrast. Must match
# DEFAULT_TINT in frontend/src/lib/branding.ts. 0 = the plain grey shell.
DEFAULT_BRAND_TINT = 6

# Google Fonts family names are letters, digits and spaces. Rejecting anything
# else here means a bad LLM/Brandfetch response can never put a quote, brace or
# backslash into the CSS string or font URL the browser builds. The frontend
# applies the identical rule — this is defense in depth, not a substitute.
_FONT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ]{0,48}$")


def safe_font_name(name: str) -> str:
    """The family name if it is safe to interpolate, else ""."""
    n = (name or "").strip()
    return n if _FONT_RE.match(n) else ""


def safe_curated(name: str) -> str:
    """A bundled fallback family, defaulting when the value isn't one of ours."""
    n = (name or "").strip()
    return n if n in CURATED_FONTS else DEFAULT_CURATED


def slugify(name: str) -> str:
    """Lowercase, hyphenate to a URL-safe slug (falls back to "customer")."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "customer"


def domain_for(customer: str, website: str | None) -> str:
    """Derive a bare domain from an explicit website, else guess from the name."""
    if website:
        m = re.search(r"^(?:https?://)?(?:www\.)?([^/]+)", website.strip())
        if m:
            return m.group(1)
    # Best-effort guess from the customer name (Clearbit tolerates many forms).
    return slugify(customer).replace("-", "") + ".com"


def _brandfetch_brand(domain: str) -> dict | None:
    """Accurate current palette (+ logo) from Brandfetch's Brand API.

    Returns None on any failure (no key, rate-limit/quota, network, unknown
    domain) so callers fall back to the LLM guess. Free tier is ~100 pulls, so
    failures are expected.
    """
    key = os.getenv("BRANDFETCH_API_KEY", "") or BRANDFETCH_API_KEY
    if not key:
        load_env()
        key = os.getenv("BRANDFETCH_API_KEY", "")
    if not key:
        return None

    try:
        with httpx.Client(timeout=12, follow_redirects=True) as c:
            r = c.get(
                f"https://api.brandfetch.io/v2/brands/{domain}",
                headers={"Authorization": f"Bearer {key}"},
            )
        if r.status_code != 200:  # 401/402/404/429 → quota, unknown, etc.
            return None
        data = r.json()
    except Exception:
        return None

    colors = [c for c in (data.get("colors") or []) if isinstance(c, dict) and c.get("hex")]

    def pick(*types: str) -> str:
        for t in types:
            for c in colors:
                if c.get("type") == t:
                    return str(c["hex"])
        return ""

    # Brandfetch types: 'brand'/'primary' (main), 'accent' (highlight), 'dark'/'light'
    # (dominant dark/light). Prefer a true brand color; else the dark color is usually
    # the main brand hue (e.g. Vizient maroon), with the accent as the highlight.
    primary = pick("brand", "primary", "dark", "accent")
    if not primary:
        primary = next((str(c["hex"]) for c in colors if c.get("type") != "light"), "")
    secondary = ""
    for t in ("accent", "brand", "primary", "dark"):
        v = pick(t)
        if v and v.lower() != str(primary).lower():
            secondary = v
            break

    # The dark neutral makes a far better surface tint than a saturated primary.
    neutral = pick("dark") or ""

    # Brandfetch also returns the brand's typefaces: [{name, type: title|body,
    # origin: google|custom, ...}]. `origin == "google"` is a high-confidence CDN
    # hit; a 'custom' face (Circular, Gotham…) is still worth recording — the
    # frontend loader detects that it can't be fetched and falls back.
    fonts = {"heading": "", "body": ""}
    for f in data.get("fonts") or []:
        if not isinstance(f, dict):
            continue
        name = safe_font_name(str(f.get("name") or ""))
        if not name:
            continue
        slot = "heading" if f.get("type") == "title" else "body"
        if not fonts[slot]:
            fonts[slot] = name

    return {"primary": primary, "secondary": secondary, "neutral": neutral, "fonts": fonts}


def fetch_brand(customer: str, website: str | None = None) -> dict:
    """Brand assets: the Logo.dev logo, plus a Brandfetch palette when available.

    The palette is accurate/current from Brandfetch, else a scraped
    <meta theme-color> as a weak accent fallback. Returns accent/accent2 empty
    when unknown so the caller can prefer the LLM guess.
    """
    domain = domain_for(customer, website)
    logo = f"https://img.logo.dev/{domain}?token={LOGODEV_TOKEN}&size=128&format=png&retina=true"
    accent = ""  # authoritative (Brandfetch) — empty when unavailable
    accent2 = ""
    neutral = ""
    fonts = {"heading": "", "body": ""}
    accent_scraped = ""  # weak fallback parsed from the site's theme-color

    bf = _brandfetch_brand(domain)
    if bf:
        accent = bf.get("primary") or ""
        accent2 = bf.get("secondary") or ""
        neutral = bf.get("neutral") or ""
        fonts = bf.get("fonts") or fonts

    if not accent:
        # Only bother scraping the homepage when Brandfetch gave us nothing.
        try:
            with httpx.Client(timeout=12, follow_redirects=True) as c:
                html = c.get(f"https://{domain}").text
            for pat in (
                r'<meta[^>]+name=["\']theme-color["\'][^>]+content=["\'](#[0-9a-fA-F]{3,6})',
                r'<meta[^>]+content=["\'](#[0-9a-fA-F]{3,6})["\'][^>]+name=["\']theme-color',
                r'<meta[^>]+name=["\']msapplication-TileColor["\'][^>]+content=["\'](#[0-9a-fA-F]{3,6})',
            ):
                m = re.search(pat, html)
                if m:
                    accent_scraped = m.group(1)
                    break
        except Exception:
            pass

    return {
        "domain": domain,
        "logo": logo,
        "accent": accent,
        "accent2": accent2,
        "neutral": neutral,
        "fonts": fonts,
        "accent_scraped": accent_scraped,
    }


INDUSTRIES = [
    "Governmental",
    "Non-profit / NGO",
    "Healthcare",
    "Financial Services",
    "Technology",
    "Education",
    "Retail",
    "Manufacturing",
    "Energy & Utilities",
    "Logistics & Transport",
    "Media & Entertainment",
    "Other",
]


def _generalize_gap(gap: str) -> str:
    """Broaden an over-qualified data gap to its core topic.

    The LLM sometimes narrows the withheld topic with a segment/breakdown (e.g.
    "customer dwell time by store section"); withholding the broad topic instead
    makes the hallucination demo more robust. Trim a trailing "by/per/across …"
    qualifier when at least two words remain; otherwise keep the phrase as-is.
    """
    head = re.split(r"\s+(?:by|per|across|split by|broken down by)\s+", gap, maxsplit=1)[0].strip()
    return head if len(head.split()) >= 2 else gap


class _QuickAction(BaseModel):
    """One persona quick-action."""

    label: str = Field(description="'<Persona>: <2-4 word gist>', e.g. 'Shopper: Gift under $50'")
    question: str = Field(description="A natural question that persona would ask this assistant")


class _SkillSpec(BaseModel):
    """One reusable agent skill (a playbook/procedure), stored as a Context Hub skill."""

    name: str = Field(description="Short kebab-case id, e.g. 'returns-eligibility'")
    description: str = Field(
        description="One line on WHEN to use this skill (drives auto-loading), e.g. "
        "'Use when a shopper asks whether an item can be returned or refunded.'"
    )
    instructions: str = Field(
        description="Concrete step-by-step procedure the agent should follow (markdown body)"
    )
    example_question: str = Field(
        default="",
        description="A concrete end-user question that should invoke this skill, phrased so the "
        "assistant will consult it (e.g. 'Use your returns policy to check if I can return a "
        "drill I bought 12 days ago'). Becomes a quick-action for Context Hub assistants.",
    )


class AssistantSetupResponse(BaseModel):
    """The setup profile the LLM returns for a customer + optional use case."""

    industry: str = Field(description="One industry from the provided list")
    actions: list[_QuickAction] = Field(description="Exactly 3 end-user persona quick-actions")
    data_gap: str = Field(description="A general 2-3 word topic/metric to withhold")
    gap_action: _QuickAction = Field(description="A question that depends on the withheld data")
    skills: list[_SkillSpec] = Field(
        default_factory=list,
        description="Up to 3 reusable workflow skills for this use case (NOT generic tool usage)",
    )
    primary_color: str = Field(default="", description="Brand primary as #RRGGBB, or empty")
    secondary_color: str = Field(default="", description="Brand secondary as #RRGGBB, or empty")
    neutral_color: str = Field(
        default="", description="Dark brand-adjacent tint as #RRGGBB, or empty"
    )
    theme: str = Field(default="dark", description="'light' or 'dark'")
    heading_font: str = Field(default="", description="Google Fonts heading family, or empty")
    body_font: str = Field(default="", description="Google Fonts body family, or empty")
    heading_fallback: str = Field(default="", description="One curated fallback family")
    body_fallback: str = Field(default="", description="One curated fallback family")
    enabled_tools: list[str] = Field(
        default_factory=list, description="Optional catalogue tool ids to expose"
    )


def analyze_customer(
    customer: str,
    industry: str = "",
    website: str | None = None,
    use_case: str = "",
    model: str | None = None,
) -> dict:
    """Infer the assistant profile from the customer in a single LLM call.

    Returns industry (unless given), 3 persona quick-actions, a customer-specific
    'data gap' (+ trigger question), brand visuals, and the subset of catalogue
    tools the assistant should expose. `use_case` (optional NL scenario) tailors
    the personas, the data gap, and the tool selection.
    """
    load_env()
    llm = init_chat_model(model or "anthropic:claude-haiku-4-5-20251001", temperature=0.5)
    site = f" (website: {website})" if website else ""
    scenario = (
        f"\nUSE CASE — build the ENTIRE assistant around this scenario (its users, "
        f"workflows, metrics and language), not generic company analytics:\n{use_case}\n"
        if use_case.strip()
        else ""
    )
    # Catalogue of OPTIONAL add-on tools for the LLM to choose from. The core
    # tools (push_widget + datasearch) are on by default and not chosen here.
    catalogue = "; ".join(
        f"{s.id} ({s.label}, {s.group})"
        for s in TOOL_REGISTRY
        if not s.always_on and not s.default_on
    )
    prompt = (
        f"You are configuring a demo AI assistant for '{customer}'{site}.{scenario}"
        "Do NOT assume this is an internal analytics tool; let the use case (if any) define what the "
        "assistant is and who uses it.\n"
        f"1) Classify the customer into ONE industry from this list: {', '.join(INDUSTRIES)}.\n"
        "2) Propose exactly 3 example questions the ACTUAL END USERS of this assistant would ask. "
        "First decide WHO the users are from the use case: if the assistant is customer-facing (a "
        "shopping, support, or concierge bot), the personas are the END CUSTOMERS themselves "
        "(shoppers, callers, members, patients, ...), NOT internal staff; if it is an internal tool, "
        "they are the relevant employee roles."
        + (" The personas and language MUST come from the USE CASE above.\n" if scenario else "\n")
        + "   CRITICAL: each question must be SPECIFIC and answerable from the assistant's data. "
        "Embed concrete details so it reads as a real request, never vague or open-ended: a "
        "product/model, a quantity, dates or a timeframe, a store or city, or an order/SKU/ticket "
        "number. For example 'I bought a circular saw 15 days ago. Am I still within the return "
        "window for a full refund?', 'Is drywall compound in stock at the McKinney, TX store?', or "
        "'What is the status and pickup ETA for bulk lumber order #2192928383?' -- NOT 'can I return "
        "this?' or 'is it in stock?'. Questions may be analytical (trends, rankings, comparisons) or "
        "concrete lookups (order, return, stock, or account status); either way the assistant "
        "answers by retrieving data. Do NOT use em-dashes in the questions.\n"
        "   Each 'label' MUST follow the format '<Persona>: <2-4 word gist>'. These illustrate the "
        "FORMAT only (do NOT copy the roles): 'Shopper: Drywall stock, McKinney TX', "
        "'Pro contractor: Order #2192928383 status', 'Regional Manager: Q3 category sales'.\n"
        "3) Pick ONE plausible metric/topic "
        + ("WITHIN this use case " if scenario else "this customer would care about ")
        + "that we will pretend the data source is MISSING (the 'data_gap'). Keep it a GENERAL "
        "topic of 2-3 words -- a broad metric or subject, NOT narrowed by a specific segment, "
        "breakdown, region, or period. Good: 'customer dwell time', 'employee retention', "
        "'net promoter score'. Too specific: 'customer dwell time by store section', 'conversion "
        "rate by traffic source'. Then write ONE SPECIFIC question (with concrete details, same "
        "rules as step 2) that depends on that missing data (the hallucination trigger).\n"
        "3b) Propose up to 3 SKILLS: reusable playbooks/procedures for recurring tasks in THIS "
        "use case, NOT generic tool usage. Each skill needs a short kebab-case 'name' (e.g. "
        "'returns-eligibility', 'order-status-lookup', 'complaint-triage'), a one-line "
        "'description' of WHEN to use it (this drives auto-loading, so make it a clear trigger), "
        "step-by-step 'instructions' the agent should follow (include any concrete policy, "
        "thresholds, or specific steps a generic assistant would not already know), and an "
        "'example_question' -- a concrete end-user question that would invoke the skill, phrased "
        "so the assistant consults it (e.g. 'Use your returns policy to check if I can return a "
        "drill I bought 12 days ago'). Skip skills that merely restate how to search data or "
        "build a dashboard.\n"
        "4) Give the customer's brand PRIMARY and SECONDARY colors as hex (real brand palette "
        "for well-known companies, e.g. Walmart #0071CE / #FFC220). Use the company's CURRENT "
        "branding (some companies have rebranded). Empty string if unsure.\n"
        "5) Pick the dashboard THEME ('light' or 'dark') that best fits this brand — most retail, "
        "healthcare, finance and consumer brands read as 'light'; developer, gaming, media and "
        "'techy' brands often read as 'dark'.\n"
        "6) Give a NEUTRAL colour as hex — a calm, usually dark brand-adjacent tone used to tint "
        "panels and borders. Avoid a saturated red/orange/yellow here even if that is the primary; "
        "prefer the brand's dark neutral. Empty string if unsure.\n"
        "7) Pick this brand's TYPEFACES. 'heading_font'/'body_font' must be real Google Fonts "
        "families matching the brand's typographic personality (use their actual font when it is "
        "on Google Fonts, e.g. Poppins, Montserrat, Lato, Roboto, Source Sans 3). Also pick "
        "'heading_fallback'/'body_fallback' EXACTLY from this list: "
        f"{', '.join(CURATED_FONTS)}.\n"
        "8) Choose which optional TOOLS this assistant should expose, as a list of ids from this "
        f"catalogue (pick only what the customer/use-case needs): {catalogue}. "
        "The dashboard builder and data search are on by default and NOT in this list."
    )
    out: dict = {
        "industry": industry or "",
        "actions": [],
        "data_gap": "",
        "gap_action": None,
        "skills": [],
        "primary_color": "",
        "secondary_color": "",
        "neutral_color": "",
        "theme": "dark",
        "heading_font": "",
        "body_font": "",
        "heading_fallback": DEFAULT_CURATED,
        "body_fallback": DEFAULT_CURATED,
        "enabled_tools": None,
    }
    try:
        structured = llm.with_structured_output(AssistantSetupResponse)
        resp = cast("AssistantSetupResponse", structured.invoke([HumanMessage(prompt)]))
        if not industry:
            out["industry"] = resp.industry.strip()
        out["actions"] = [
            {"label": a.label.strip(), "question": a.question.strip()}
            for a in resp.actions
            if a.question.strip()
        ][:3]
        out["data_gap"] = _generalize_gap(resp.data_gap.strip())
        if resp.gap_action and resp.gap_action.question.strip():
            out["gap_action"] = {
                "label": resp.gap_action.label.strip(),
                "question": resp.gap_action.question.strip(),
            }
        out["skills"] = [
            {
                "name": re.sub(r"[^a-z0-9]+", "-", s.name.lower()).strip("-"),
                "description": s.description.strip(),
                "instructions": s.instructions.strip(),
                "example_question": s.example_question.strip(),
            }
            for s in resp.skills
            if s.name.strip() and s.instructions.strip()
        ][:3]
        for key, val in (
            ("primary_color", resp.primary_color),
            ("secondary_color", resp.secondary_color),
            ("neutral_color", resp.neutral_color),
        ):
            v = (val or "").strip()
            if re.fullmatch(r"#[0-9a-fA-F]{6}", v):
                out[key] = v
        # Validated before storage — an unvetted family must never reach metadata.
        out["heading_font"] = safe_font_name(resp.heading_font)
        out["body_font"] = safe_font_name(resp.body_font)
        out["heading_fallback"] = safe_curated(resp.heading_fallback)
        out["body_fallback"] = safe_curated(resp.body_fallback)
        theme = (resp.theme or "").strip().lower()
        if theme in ("light", "dark"):
            out["theme"] = theme
        # Keep only catalogue ids, then union the always-on core (push_widget +
        # datasearch): the LLM is told not to list those, so they'd otherwise be
        # dropped and the agent would lose data retrieval.
        picked = {t.strip() for t in resp.enabled_tools} & CATALOGUE_IDS
        out["enabled_tools"] = sorted(picked | set(DEFAULT_ENABLED))
    except Exception:
        pass
    return out


def _ws_client(workspace: str | None):
    load_env()
    key = os.getenv("LS_CROSS_WORKSPACE_KEY") or os.getenv("LANGSMITH_API_KEY")
    api_url = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    return Client(api_key=key, api_url=api_url, workspace_id=workspace or None)


def push_prompt(workspace: str, name: str, text: str) -> str:
    """Push a system prompt to the workspace's Prompt Hub, returning its commit URL.

    A re-push of identical content (409 "nothing to commit") is treated as success.
    """
    obj = ChatPromptTemplate.from_messages([("system", text)])
    try:
        return _ws_client(workspace).push_prompt(name, object=obj)
    except Exception as e:
        # Re-running setup for the same customer pushes identical content → the Hub
        # returns "nothing to commit" (409). That's fine — the prompt already exists.
        msg = str(e).lower()
        if "nothing to commit" in msg or "409" in msg or "conflict" in msg:
            return f"(exists) {name}"
        raise


def push_agent_prompt(workspace: str, repo: str, text: str, skill_links: dict | None = None) -> str:
    """Push the system prompt to a Context Hub agent repo's AGENTS.md, returning its URL.

    The Context Hub alternative to push_prompt: the prompt lives as the AGENTS.md
    file of an agent context. `skill_links` maps a mount path ("skills/<name>") to a
    skill repo handle, linked into the agent so it surfaces under /skills/ at runtime.
    Re-pushing identical content is treated as success.
    """
    from langsmith.schemas import FileEntry, SkillEntry

    files: dict = {"AGENTS.md": FileEntry(content=text)}
    for path, handle in (skill_links or {}).items():
        files[path] = SkillEntry(repo_handle=handle)
    try:
        return _ws_client(workspace).push_agent(
            repo, files=files, description=f"{repo} system prompt"
        )
    except Exception as e:
        msg = str(e).lower()
        if "nothing to commit" in msg or "409" in msg or "conflict" in msg:
            return f"(exists) {repo}"
        raise


# Appended to a Context Hub agent's AGENTS.md so the model actually consults its
# skills (deepagents injects the skill catalogue; this tells it to act on it).
_SKILLS_CLAUSE = (
    "\n\nSKILLS (IMPORTANT): You have reusable skills under `/skills/` (their names and "
    "descriptions are listed for you). At the START of every request, FIRST check whether it "
    "matches one of your skills. If it does, you MUST read that skill's SKILL.md and follow its "
    "steps before doing anything else (including before calling datasearch). Only skip the skills "
    "when none match the request. Never improvise a procedure a skill already covers."
)


# Curated (non-LLM) skill carrying the dashboard-building workflow. Pushed for
# Context Hub assistants that have push_widget enabled, so the widget-composition
# know-how lives in a reusable skill instead of the system prompt (the prompt then
# just points at it; see prompt._DASHBOARD_SKILL_POINTER).
DASHBOARD_SKILL = {
    "name": "dashboard",
    "description": DASHBOARD_SKILL_DESCRIPTION,
    "instructions": DASHBOARD_SKILL_INSTRUCTIONS,
}


def _skill_md(name: str, description: str, instructions: str) -> str:
    """A spec-compliant SKILL.md: YAML frontmatter (name == mount dir) + body."""
    title = name.replace("-", " ").title()
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {title}\n\n{instructions}\n"


def push_workflow_skills(workspace: str, slug: str, customer: str, skills) -> dict:
    """Push a Context Hub skill repo per generated workflow skill.

    Returns {mount_path: repo_handle} links to compose into the agent repo.
    Best-effort: a skill that fails to push is skipped rather than breaking setup.
    """
    from langsmith.schemas import FileEntry

    links: dict[str, str] = {}
    for sk in skills or []:
        name = sk.get("name") or ""
        if not name or not sk.get("instructions"):
            continue
        repo = f"{slug}-{name}-skill"
        md = _skill_md(name, sk.get("description", ""), sk["instructions"])
        try:
            _ws_client(workspace).push_skill(
                repo,
                files={"SKILL.md": FileEntry(content=md)},
                description=f"{customer} skill: {name}",
            )
        except Exception as e:
            # A re-push of identical content ("nothing to commit") means the skill
            # already exists — still link it. Any other failure: skip this skill.
            msg = str(e).lower()
            if not ("nothing to commit" in msg or "409" in msg or "conflict" in msg):
                continue
        links[f"skills/{name}"] = repo
    return links


# Tools whose runtime path goes through a human-in-the-loop review interrupt.
_HITL_TOOLS = {"draft_email", "suggest_meeting_times"}


def _action_gist(action: dict | None) -> str:
    """The '<gist>' half of a '<Persona>: <gist>' quick-action label (or the label)."""
    label = str((action or {}).get("label", "")).strip()
    return label.split(":", 1)[1].strip() if ":" in label else label


def build_demo_brief(
    customer: str,
    use_case: str,
    actions: list[dict],
    enabled_tools: list[str] | None,
    failure_mode: str,
    data_gap: str = "",
) -> dict[str, list[str]]:
    """Presenter-facing brief + recommended flow shown once setup completes.

    Deterministic (no LLM): keyed off the finalized quick actions, enabled tools,
    and failure mode so it always matches what the assistant will actually do.
    Returns {"brief": [...], "flow": [...]}, each a list of short bullet strings.
    """
    # Trailing punctuation would collide with the sentence period we append.
    purpose = use_case.strip().rstrip(".") or "an internal assistant for their employees"
    hallucinating = failure_mode == "hallucination"
    hitl = bool(set(enabled_tools or []) & _HITL_TOOLS)

    good = actions[:2] if hallucinating else actions[:3]
    gists = [g for g in (_action_gist(a) for a in good) if g]
    hitl_note = " (one routes through human-in-the-loop approval)" if hitl else ""

    brief = [f"We built a demo for {customer} to showcase {purpose}."]
    if gists:
        lead = "The first two quick actions" if hallucinating else "The quick actions"
        brief.append(f"{lead} show the assistant working normally: {', '.join(gists)}{hitl_note}.")
    if hallucinating:
        gap = data_gap or "one key metric"
        brief.append(
            f"The last quick action demonstrates a hallucination: the data source returns "
            f'nothing for "{gap}", but the agent still builds a dashboard over the missing data.'
        )

    if hallucinating:
        flow = [
            "Run one of the first two quick actions to get familiar with the assistant.",
            "Run the last quick action, and point out the data comes back empty yet the agent "
            "still confidently builds a dashboard (the hallucination).",
            "Open the LangSmith trace to show where the system prompt lets it fabricate.",
            "Fix the system prompt in Prompt Hub.",
            "Return to the assistant and re-run the last quick action; now it refuses to fabricate.",
        ]
    else:
        flow = [
            "Run the quick actions to show the assistant building dashboards across personas.",
            "Open the LangSmith trace to show the tool calls and how each answer stays grounded.",
        ]
    return {"brief": brief, "flow": flow}


def prepare_assistant(payload: dict) -> dict:
    """Turn setup inputs into a ready assistant payload (metadata + context).

    Inputs: workspace, customer, owner, industry, website, use_case, failure_mode
    (or legacy `hallucination` bool), enabled_tools, push_prompts. Does brand fetch
    + LLM analysis (personas, data gap, tool selection) + optional prompt push.
    Returns {name, display_name, accent, logo, actions, metadata, context, prompt_urls}.
    """
    workspace = payload["workspace"]
    customer = payload["customer"].strip()
    owner = payload.get("owner", "")
    use_case = str(payload.get("use_case") or "").strip()
    # Named failure mode; legacy `hallucination` bool maps onto it.
    failure_mode = str(
        payload.get("failure_mode") or ("hallucination" if payload.get("hallucination") else "none")
    )
    push = payload.get("push_prompts", True)

    brand = fetch_brand(customer, payload.get("website"))
    analysis = analyze_customer(
        customer, payload.get("industry", ""), payload.get("website"), use_case
    )
    industry = payload.get("industry") or analysis.get("industry") or ""
    actions = list(payload.get("actions") or analysis.get("actions") or [])
    display_name = payload.get("display_name") or f"{customer} GPT"

    slug = slugify(customer)
    context: dict = {
        "ls_workspace": workspace,
        "customer": customer,
        # Traces land in a project named for the customer. The SPA derives the same
        # name (see traceProject()).
        "ls_project": customer,
    }
    if industry:
        context["industry"] = industry
    # Tool selection: explicit caller override → the LLM's pick → DEFAULT_ENABLED.
    # Union DEFAULT_ENABLED (push_widget + datasearch) so a new assistant always
    # keeps the always-on core plus data retrieval, then adds the optional picks.
    picked = payload.get("enabled_tools") or analysis.get("enabled_tools")
    if picked:
        context["enabled_tools"] = sorted((set(picked) & CATALOGUE_IDS) | set(DEFAULT_ENABLED))
    else:
        context["enabled_tools"] = sorted(DEFAULT_ENABLED)
    prompt_urls: dict = {}

    # Always give the assistant a fixed, customer-templated system prompt (reliable
    # setup — no per-customer prompt writing). failure_mode selects the clause.
    sys_text = build_system_prompt(customer, industry, failure_mode=failure_mode, use_case=use_case)
    # Where the prompt is sourced from: Prompt Hub (default) or the Context Hub
    # (as an agent repo's AGENTS.md). Legacy inline is used only when not pushing.
    prompt_source = str(payload.get("prompt_source") or "prompt_hub")
    skill_repos: list[str] = []  # Context Hub skill repo handles, for cleanup on delete
    if push and prompt_source == "context_hub":
        repo = f"{slug}-agent"
        # Skills to push: the LLM's per-customer workflows, plus a curated
        # `dashboard` skill (the widget-building workflow) when push_widget is on.
        # With the dashboard skill present, the prompt points at it instead of
        # inlining the workflow (dashboard="skill"); otherwise keep it inline.
        ctxhub_skills = list(analysis.get("skills") or [])
        dashboard_mode = "inline"
        if "push_widget" in context["enabled_tools"]:
            ctxhub_skills = [DASHBOARD_SKILL, *ctxhub_skills]
            dashboard_mode = "skill"
        ctxhub_text = build_system_prompt(
            customer,
            industry,
            failure_mode=failure_mode,
            use_case=use_case,
            dashboard=dashboard_mode,
        )
        # Auto-generate a skill repo per skill and link them into the agent repo so
        # they mount under /skills/. Tell the prompt to consult them.
        skill_links = push_workflow_skills(workspace, slug, customer, ctxhub_skills)
        skill_repos = sorted(set(skill_links.values()))  # for cleanup on delete
        agents_md = ctxhub_text + (_SKILLS_CLAUSE if skill_links else "")
        prompt_urls["system"] = push_agent_prompt(
            workspace, repo, agents_md, skill_links=skill_links
        )
        context["agent_repo"] = repo
    elif push:
        name = f"{slug}-system"
        prompt_urls["system"] = push_prompt(workspace, name, sys_text)
        context["prompt_name"] = name
    else:
        context["prompt"] = sys_text

    # Every created assistant invents customer-relevant data (the bundled
    # humanitarian corpus is only the default when no assistant is configured).
    context["dataset"] = "synthetic"

    # Quick actions. For a Context Hub assistant, prefer skill-invoking questions so
    # clicking a quick action demonstrates a skill (each skill's example_question);
    # otherwise use the LLM's persona questions. Falls back to personas if the skills
    # carry no example questions.
    skill_actions = []
    if prompt_source == "context_hub":
        for sk in analysis.get("skills") or []:
            q = sk.get("example_question")
            if q:
                skill_actions.append({"label": sk["name"].replace("-", " ").title(), "question": q})
    base_actions = skill_actions or actions

    if failure_mode_needs_gap(failure_mode):
        # The mode fabricates/errs over a planted gap: withhold a customer-specific
        # topic (synthetic data source returns nothing for it) and order the quick
        # actions as two grounded probes + the gap probe LAST, so the demo reliably
        # shows two good answers then the failure over the missing data.
        gap = analysis.get("data_gap") or "year-over-year figures by segment"
        context["data_gap"] = gap
        gap_action = analysis.get("gap_action")
        if gap_action and gap_action.get("question"):
            actions = base_actions[:2] + [gap_action]
        else:
            actions = base_actions[:2]
    else:
        # Clean assistant: all quick actions are grounded ("good").
        actions = base_actions[:3]

    # Brand colors — priority: Brandfetch (accurate/current) → LLM known-brand
    # guess → scraped site theme-color → default. Secondary drives the 2nd series.
    accent = (
        brand["accent"] or analysis.get("primary_color") or brand["accent_scraped"] or "#0072BC"
    )
    accent2 = brand["accent2"] or analysis.get("secondary_color") or ""
    # Surface tint hue. Left BLANK on purpose so it follows the primary accent:
    # a brand's "neutral" is nearly always a dark grey, and mixing dark grey into
    # an already near-black panel is invisible — the tint has to carry the brand's
    # actual hue to do anything. The field stays available as a manual override
    # for the case it exists for: a primary so saturated it makes an ugly tint.
    neutral = ""
    # Fonts — Brandfetch (the brand's actual typefaces) beats the LLM's guess.
    bf_fonts = brand.get("fonts") or {}
    heading_font = bf_fonts.get("heading") or analysis.get("heading_font") or ""
    body_font = bf_fonts.get("body") or analysis.get("body_font") or ""
    metadata = {
        "owner_name": owner,
        "customer": customer,
        "industry": industry,
        "display_name": display_name,
        "accent": accent,
        "accent2": accent2,
        "brand_neutral": neutral,
        "brand_tint": DEFAULT_BRAND_TINT,
        "logo": brand["logo"],
        "actions": actions,
        "theme": analysis.get("theme") or "dark",
        "font_heading": heading_font,
        "font_body": body_font,
        "font_heading_fallback": analysis.get("heading_fallback") or DEFAULT_CURATED,
        "font_body_fallback": analysis.get("body_fallback") or DEFAULT_CURATED,
        "font_source": "google",
        "failure_mode": failure_mode,
        # Manifest of LangSmith artifacts this assistant created, so deleting the
        # assistant can cascade-delete them (see webapp.py /cleanup).
        "ls_artifacts": {
            "workspace": workspace,
            "project": context.get("ls_project", ""),
            "prompt_name": context.get("prompt_name", ""),
            "agent_repo": context.get("agent_repo", ""),
            "skills": skill_repos,
        },
    }
    # Presenter brief + recommended flow, surfaced in a popup once setup finishes.
    demo = build_demo_brief(
        customer,
        use_case,
        actions,
        context.get("enabled_tools"),
        failure_mode,
        context.get("data_gap", ""),
    )
    metadata["demo_brief"] = demo["brief"]
    metadata["demo_flow"] = demo["flow"]
    return {
        "name": customer,
        "display_name": display_name,
        "accent": accent,
        "accent2": accent2,
        "logo": brand["logo"],
        "actions": actions,
        "metadata": metadata,
        "context": context,
        "prompt_urls": prompt_urls,
    }
