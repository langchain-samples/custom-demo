"""Prepare a customer-specific demo scenario and its resources.

Brand discovery and customer analysis feed one DemoPlan. Provisioning applies that plan
and records LangSmith handles for tagging and cleanup. The setup graph returns metadata
and context; the browser publishes the assistant separately, without atomic rollback.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import cast

import httpx
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langsmith.schemas import FileEntry, SkillEntry
from langsmith.utils import LangSmithConflictError
from pydantic import BaseModel, Field

from custom_demo.config import load_env, sampling_kwargs, setup_model
from custom_demo.core.demo import DemoPlan, LsArtifacts
from custom_demo.provisioning.client import _ws_client, slugify
from custom_demo.provisioning.evals import (
    ensure_dataset_evaluator,
    ensure_eval_dataset,
    judge_prompt_name,
)
from custom_demo.provisioning.resource_tags import tag_assistant_resources
from custom_demo.provisioning.traffic import annotation_queue_name, start_demo_traffic
from custom_demo.resources.sandbox import SeedSpecError, prewarm_sandbox
from custom_demo.runtime.prompt import (
    DASHBOARD_SKILL_DESCRIPTION,
    DASHBOARD_SKILL_INSTRUCTIONS,
    build_system_prompt,
    failure_mode_needs_gap,
)
from custom_demo.runtime.tools import (
    CATALOGUE_IDS,
    DEFAULT_ENABLED,
    EXPLICIT_ONLY,
    TOOL_REGISTRY,
)

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

    Genuinely best-effort, so it does not raise: the palette it would return is
    cosmetic, the LLM guess behind it is visible in the setup panel, and no
    presenter wants a whole assistant refused over a brand colour. But every way out of
    here SAYS so on stdout, because silence makes "why are this customer's colours wrong"
    unanswerable: quota, a wrong domain and a network blip all look the same from
    outside.
    """
    key = os.getenv("BRANDFETCH_API_KEY", "") or BRANDFETCH_API_KEY
    if not key:
        load_env()
        key = os.getenv("BRANDFETCH_API_KEY", "")

    if not key:
        print(f"[setup] brand palette: no BRANDFETCH_API_KEY, guessing colors for {domain}")
        return None

    try:
        with httpx.Client(timeout=12, follow_redirects=True) as c:
            r = c.get(
                f"https://api.brandfetch.io/v2/brands/{domain}",
                headers={"Authorization": f"Bearer {key}"},
            )

        if r.status_code != 200:  # 401/402/404/429 → quota, unknown, etc.
            print(
                f"[setup] brand palette: Brandfetch answered {r.status_code} for {domain}, "
                f"guessing colors"
            )
            return None

        data = r.json()
    except Exception as exc:  # noqa: BLE001 - an optional brand lookup; report and fall through to the LLM guess
        print(
            f"[setup] brand palette: Brandfetch lookup for {domain} failed, guessing colors: "
            f"{type(exc).__name__}: {exc}"
        )
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
        # Only bother scraping the homepage when Brandfetch gave us nothing. Also
        # best-effort, and also now audible: a weak accent that never arrives is
        # the difference between the right brand colour and a guessed one, and
        # silence made that undiagnosable.
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

            if not accent_scraped:
                print(f"[setup] brand palette: no theme-color meta tag on {domain}")
        except Exception as exc:  # noqa: BLE001 - scraping someone else's HTML; report and leave accent_scraped unset
            print(
                f"[setup] brand palette: could not scrape {domain} for a theme-color: "
                f"{type(exc).__name__}: {exc}"
            )

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


# Matches the "2-4 files" the schema asks for; `render_seed_script` caps again on
# the way into the VM (this spec is model-written, so neither side trusts it).
_MAX_SEED_FILES = 4


class _SeedFile(BaseModel):
    """One file to plant in the assistant's code-execution VM.

    DECLARATIVE on purpose: the LLM describes a file, and `render_seed_script` turns
    that into the bytes. Asking a model for a shell script instead would mean running
    generated code and debugging generated code — a demo that fails at provision time
    with a stray quote is worse than a plain CSV.
    """

    name: str = Field(description="File name only, with extension, e.g. 'intake_2026-01.pdf'")
    kind: str = Field(description="One of: csv, json, txt, md, pdf")
    description: str = Field(description="One line on what it holds, shown to the agent")
    columns: list[str] = Field(
        default_factory=list, description="csv/json only: column names, 3-6 of them"
    )
    rows: list[list[str]] = Field(
        default_factory=list,
        description="csv/json only: up to 24 realistic rows, values as strings, "
        "aligned to `columns`",
    )
    text: str = Field(
        default="", description="txt/md/pdf only: the document body, realistic and specific"
    )


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
    action_label: str = Field(
        default="",
        description="Quick-action label for this skill, in '<Persona>: <2-4 word gist>' format "
        "(same as the persona quick-actions), e.g. 'Shopper: Return eligibility'.",
    )
    workflow: str = Field(
        description="REQUIRED. The dynamic-subagent workflow pattern this skill runs, one of: "
        "classify-and-act, fan-out-and-synthesize, adversarial-verification, generate-and-filter, "
        "tournament, loop-until-done. Pick the one that best fits the task -- every skill gets "
        "one, so frame the task as something with parts worth working in parallel.",
    )
    sandbox_step: str = Field(
        default="",
        description="REQUIRED. One or two sentences naming the concrete analysis this skill runs "
        "in the Python `execute` sandbox, and WHICH seeded file in /workspace/data it opens (use "
        "the exact file names from `seed_files`), e.g. 'Load /workspace/data/claims.csv with "
        "pandas and compute denial rate by procedure code'.",
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
    seed_files: list[_SeedFile] = Field(
        default_factory=list,
        description="2-4 files to plant in the agent's code-execution VM, in the FORMAT this "
        "use case actually works with (a claims team gets PDFs and a claims CSV; a retail "
        "team gets sales data). These are what the agent analyses when asked, so they must "
        "carry the metrics and language of the use case",
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
    # Retry/timeout hardening, matching `agent.build_chat_model`. Don't leave this on
    # the library default of 2 retries while the agent gets 8: that is backwards, since
    # a transient 529 here does not fail loudly, it silently produces the generic
    # assistant below.
    llm = init_chat_model(  # ty: ignore[no-matching-overload]
        model or setup_model(), max_retries=8, timeout=180, **sampling_kwargs(0.5)
    )
    site = f" (website: {website})" if website else ""
    scenario = (
        f"\nUSE CASE. Build the ENTIRE assistant around this scenario (its users, "
        f"workflows, metrics and language), not generic company analytics:\n{use_case}\n"
        if use_case.strip()
        else ""
    )
    # Catalogue of OPTIONAL add-on tools for the LLM to choose from. The core
    # tool (push_widget) is always on and not chosen here.
    catalogue = "; ".join(
        f"{s.id} ({s.label}, {s.group})"
        for s in TOOL_REGISTRY
        if not s.always_on and not s.default_on and not s.explicit_only
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
        "drill I bought 12 days ago'), and an 'action_label' for that question in the SAME "
        "'<Persona>: <2-4 word gist>' format as step 2's quick-actions (e.g. 'Shopper: Return "
        "eligibility'). Skip skills that merely restate how to search data or build a dashboard.\n"
        "   EVERY skill must exercise BOTH of the assistant's headline capabilities, because "
        "invoking one is how we demo them, so scope each skill to a task that genuinely needs "
        "both, never a single lookup:\n"
        "   - 'workflow' (REQUIRED): the dynamic-subagent pattern the skill orchestrates, one of "
        f"{', '.join(WORKFLOW_PATTERNS)}, so the agent fans the work out to parallel subagents. "
        "Pick the pattern that genuinely fits, and write the 'instructions' around work that has "
        "parts worth running in parallel (per ticket, per supplier, per store, per candidate "
        "answer), not a single lookup.\n"
        "   - 'sandbox_step' (REQUIRED): the concrete analysis the skill runs with the Python "
        "`execute` tool in its Linux VM, naming the file in /workspace/data it opens. It MUST be "
        "one of the files you propose in step 9, by exact name, and it must be real computation "
        "over that file (aggregate, rank, compare, parse the PDF), not 'read the file'.\n"
        "4) Give the customer's brand PRIMARY and SECONDARY colors as hex (real brand palette "
        "for well-known companies, e.g. Walmart #0071CE / #FFC220). Use the company's CURRENT "
        "branding (some companies have rebranded). Empty string if unsure.\n"
        "5) Pick the dashboard THEME ('light' or 'dark') that best fits this brand. Most retail, "
        "healthcare, finance and consumer brands read as 'light'; developer, gaming, media and "
        "'techy' brands often read as 'dark'.\n"
        "6) Give a NEUTRAL colour as hex: a calm, usually dark brand-adjacent tone used to tint "
        "panels and borders. Avoid a saturated red/orange/yellow here even if that is the primary; "
        "prefer the brand's dark neutral. Empty string if unsure.\n"
        "7) Pick this brand's TYPEFACES. 'heading_font'/'body_font' must be real Google Fonts "
        "families matching the brand's typographic personality (use their actual font when it is "
        "on Google Fonts, e.g. Poppins, Montserrat, Lato, Roboto, Source Sans 3). Also pick "
        "'heading_fallback'/'body_fallback' EXACTLY from this list: "
        f"{', '.join(CURATED_FONTS)}.\n"
        "8) Choose which optional TOOLS this assistant should expose, as a list of ids from this "
        f"catalogue (pick only what the customer/use-case needs): {catalogue}. "
        "The dashboard builder and data search are on by default and NOT in this list.\n"
        "9) Propose 2-4 SEED FILES to plant in the assistant's code-execution VM "
        "(/workspace/data), in the FORMAT this use case actually works with. A claims team gets "
        "intake PDFs and a claims CSV, a retail team gets sales data. These are the files the "
        "skills' 'sandbox_step's open, so name them consistently with those steps, and give the "
        "csv/json ones enough rows (and the right columns) for the analysis you asked for there "
        "to actually be computable."
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
        "seed_files": [],
    }
    # Three attempts, because this one call decides the entire personality of the
    # assistant: the personas, the skills, the seed files, the tool selection, the
    # industry, the theme. Everything below has a bland default, so a single transient
    # failure would otherwise hand the presenter a fully generic demo. A whole extra
    # attempt costs ~40s at setup time, against a demo that is unusable.
    structured = llm.with_structured_output(AssistantSetupResponse)
    resp: AssistantSetupResponse | None = None
    for attempt in (1, 2, 3):
        try:
            resp = cast("AssistantSetupResponse", structured.invoke([HumanMessage(prompt)]))
            break
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
            print(f"[setup] customer analysis attempt {attempt}/3 failed: {out['error']}")

    if resp is None:
        # Reported, not swallowed. `prepare_assistant` refuses to build an assistant
        # on top of this rather than quietly producing a branded shell with no personas,
        # no skills, no seed files and no tool selection.
        return out

    try:
        out.pop("error", None)
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
                "action_label": s.action_label.strip(),
                "workflow": s.workflow.strip(),
                "sandbox_step": s.sandbox_step.strip(),
            }
            for s in resp.skills
            if s.name.strip() and s.instructions.strip()
        ][:3]
        # The VM's starting files, and the assistant has no data without them: an
        # empty list here is what `prepare_assistant` refuses to build on, because a
        # medical-claims assistant whose skills tell the agent to open a claims PDF
        # must not be handed some other use case's dataset instead. Passed through as
        # plain dicts; `render_seed_script` is what validates and caps them.
        out["seed_files"] = [f.model_dump() for f in resp.seed_files][:_MAX_SEED_FILES]
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

        # Keep only catalogue ids, drop any explicit-only tool the LLM shouldn't
        # auto-enable (it isn't even offered below), then union the always-on core
        # (push_widget): the LLM is told not to list it, so it would
        # otherwise be dropped and the agent would lose data retrieval.
        picked = ({t.strip() for t in resp.enabled_tools} & CATALOGUE_IDS) - EXPLICIT_ONLY
        out["enabled_tools"] = sorted(picked | set(DEFAULT_ENABLED))
    except Exception as exc:  # noqa: BLE001
        # The model answered but a field did not survive reading. Partial progress is
        # kept (the assignments above mutate `out` in order), and the error travels so
        # the caller can decide whether what landed is enough.
        out["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[setup] customer analysis partially failed: {out['error']}")

    return out


def _already_committed(exc: BaseException) -> bool:
    """Is `exc` the Hub saying "this exact content is already committed"?

    Re-pushing identical content is success for every pusher below: the repo ends up
    holding what we wanted it to hold. LangSmith answers that with HTTP 409, and the
    SDK turns a 409 into `LangSmithConflictError` (it keys its own idempotent pushes
    off the same type), so that is what we test.

    The message check behind it is a WIRE-FORMAT DEPENDENCY, kept deliberately narrow:
    it covers a backend that reports the condition with some status other than 409, and
    "nothing to commit" is specific enough that no real failure says it by accident.
    Don't decide it by substring instead: a test like `"409" in msg or "conflict" in
    msg` is satisfied by chance by a request id containing 409, a URL, or a repo handle,
    and a genuinely failed push is then reported as a successful one.
    """
    if isinstance(exc, LangSmithConflictError):
        return True

    return "nothing to commit" in str(exc).lower()


def push_agent_prompt(workspace: str, repo: str, text: str, skill_links: dict | None = None) -> str:
    """Push the system prompt to a Context Hub agent repo's AGENTS.md, returning its URL.

    The Context Hub alternative to push_prompt: the prompt lives as the AGENTS.md
    file of an agent context. `skill_links` maps a mount path ("skills/<name>") to a
    skill repo handle, linked into the agent so it surfaces under /skills/ at runtime.
    Re-pushing identical content is treated as success.
    """
    files: dict = {"AGENTS.md": FileEntry(content=text)}
    for path, handle in (skill_links or {}).items():
        files[path] = SkillEntry(repo_handle=handle)

    try:
        return _ws_client(workspace).push_agent(
            repo, files=files, description=f"{repo} system prompt"
        )
    except Exception as exc:
        if not _already_committed(exc):
            raise

        return f"(exists) {repo}"


# Appended to a Context Hub agent's AGENTS.md. deepagents' SkillsMiddleware injects
# a skill catalogue (each skill's name, description, and SKILL.md path) plus
# progressive-disclosure guidance into the system prompt, which agent.py composes in
# (see _hub_system_prompt). This clause just enforces that the model acts on that
# catalogue before improvising.
_SKILLS_CLAUSE = (
    "\n\nSKILLS (IMPORTANT): At the START of every request, FIRST check your available skills "
    "(their names, descriptions, and SKILL.md paths are listed above). If the request matches "
    "one, you MUST read that skill's SKILL.md at the given path and follow its steps before doing "
    "anything else (including before reading a data file). Only skip the skills when none match. "
    "Never improvise a procedure a skill already covers."
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


# The dynamic-subagent workflow "shapes" from the deepagents dynamic-subagents
# blog. EVERY generated skill names the one that fits its task, so invoking any
# quick action orchestrates that pattern (writing a `task()` workflow script in the
# code interpreter) — the skill body is where "this demo shows dynamic subagents"
# is enforced. Each value is the one-line "what it does" used in that body.
WORKFLOW_PATTERNS: dict[str, str] = {
    "classify-and-act": "route each input to the right specialist by type, then act on it",
    "fan-out-and-synthesize": "run the same step across many items in parallel, then combine the results",
    "adversarial-verification": "have separate subagents independently verify each finding before you keep it",
    "generate-and-filter": "generate several candidate options, score them, and keep the best",
    "tournament": "judge candidates head-to-head in rounds, advancing the winners",
    "loop-until-done": "repeat passes until nothing new turns up",
}


# What a skill falls back to when the model names no pattern (or one we don't know).
# Every skill gets a workflow — the fan-out is the demo — and this is the pattern
# that fits the widest range of tasks.
_DEFAULT_WORKFLOW = "fan-out-and-synthesize"


def _workflow_clause(workflow: str) -> str:
    """A SKILL.md section telling the agent which dynamic-subagent pattern to run.

    Always returns a section: an unset or unrecognised pattern falls back to
    `_DEFAULT_WORKFLOW` rather than dropping the fan-out, since a skill that
    quietly runs inline is a skill that demos nothing. Degrades gracefully at
    runtime — it orchestrates via `task()` when the code interpreter is available,
    else the plain `task` tool.
    """
    key = (workflow or "").strip().lower().replace("_", "-")
    if key not in WORKFLOW_PATTERNS:
        key = _DEFAULT_WORKFLOW

    how = WORKFLOW_PATTERNS[key]
    return (
        f"\n\n## Workflow: {key}\n"
        f"Run this skill as a **{key}** dynamic-subagent workflow, {how}. Write a short "
        f"JavaScript orchestration script that calls `task()` to fan the work out to subagents "
        f"(`researcher` for lookups, `analyst` for computation), then combine what they return. "
        f"Do this even when there are only a few items: split the work across at least two "
        f"parallel `task()` calls rather than working through them yourself. Scale the fan-out to "
        f"the work, one subagent per item up to about eight, batched beyond that. Keep the JS to "
        f"orchestration only; the numbers come from the Python sandbox below. If this assistant "
        f"has no JavaScript interpreter, fan out the same way with parallel `task` tool calls."
    )


def _sandbox_clause(step: str) -> str:
    """A SKILL.md section pinning this skill's work to the code-execution VM.

    Always present: the `execute` sandbox over the seeded files is the other half of
    what these skills exist to show, and "compute it" is also the honest instruction
    — a figure the agent derives from a file beats one it recalls. `step` is the
    model's own concrete analysis (which file, what to compute); without one the
    section still points at /workspace/data.
    """
    detail = step.strip()
    return (
        "\n\n## Data: compute it in the sandbox\n"
        "Ground this skill in the files in `/workspace/data` rather than in memory. Run "
        "`ls /workspace/data` first to see what is actually there, then use the `execute` tool "
        "(Python, pandas, numpy, pypdf) to do the work:\n"
        + (f"- {detail}\n" if detail else "")
        + "- Derive every figure you report from that data; if the file you need is not there, "
        "say so and ask the user to upload it to the Files panel.\n"
        "- Show the result with `push_widget`, a table or chart, don't only describe it."
    )


def _skill_md(
    name: str,
    description: str,
    instructions: str,
    workflow: str = "",
    sandbox_step: str = "",
) -> str:
    """A spec-compliant SKILL.md: YAML frontmatter (name == mount dir) + body.

    The description is emitted as a double-quoted YAML scalar: descriptions often
    contain a colon (e.g. "Use when ...: builds ..."), which as a bare scalar makes
    the YAML parser read it as a nested mapping and SkillsMiddleware then silently
    skips the whole skill. Every skill also gets a dynamic-subagent section and a
    code-sandbox section — invoking a quick action is how those two capabilities get
    demoed, so they are appended whether or not the model filled the fields in.
    """
    title = name.replace("-", " ").title()
    desc = description.replace("\\", "\\\\").replace('"', '\\"')
    body = f"{instructions}{_workflow_clause(workflow)}{_sandbox_clause(sandbox_step)}"
    return f'---\nname: {name}\ndescription: "{desc}"\n---\n\n# {title}\n\n{body}\n'


def push_workflow_skills(workspace: str, slug: str, customer: str, skills) -> dict:
    """Push a Context Hub skill repo per generated workflow skill.

    Returns {mount_path: repo_handle} links to compose into the agent repo.
    Best-effort: a skill that fails to push is skipped rather than breaking setup.
    """
    links: dict[str, str] = {}
    for sk in skills or []:
        name = sk.get("name") or ""
        if not name or not sk.get("instructions"):
            continue

        repo = f"{slug}-{name}-skill"
        md = _skill_md(
            name,
            sk.get("description", ""),
            sk["instructions"],
            sk.get("workflow", ""),
            sk.get("sandbox_step", ""),
        )
        try:
            _ws_client(workspace).push_skill(
                repo,
                files={"SKILL.md": FileEntry(content=md)},
                description=f"{customer} skill: {name}",
            )
        except Exception as exc:  # noqa: BLE001 - one skill is best-effort; an unexpected failure is reported and skipped
            # A re-push of identical content means the skill is already there, so still
            # link it. Any other failure skips this one skill, but SAYS which and why:
            # an assistant quietly missing a skill it should have is the kind of "why is
            # it behaving oddly" that has no answer in the logs unless this prints.
            if not _already_committed(exc):
                print(
                    f"[setup] skill {name!r} failed to push, skipping it: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

        links[f"skills/{name}"] = repo

    return links


def push_skills_bundle(workspace: str, slug: str, customer: str, skills) -> str:
    """Push all of an assistant's skills into ONE Context Hub repo, at its ROOT.

    Files are `"<name>/SKILL.md"` (root layout) so the repo can be mounted at
    `/skills/` at runtime via a plain CompositeBackend route: the composite strips
    the mount prefix, and root-layout keys have no `skills/` prefix to lose (a repo
    that keeps skills under `skills/`, like an agent repo, would be served from the
    wrong subtree). Every assistant gets one, so a skills bundle is independent of the
    agent repo that holds the prompt. Idempotent (a re-push of identical
    content is treated as success). Returns the repo handle, or "" if no valid skills.
    """
    files: dict = {}
    for sk in skills or []:
        name = sk.get("name") or ""
        if not name or not sk.get("instructions"):
            continue

        files[f"{name}/SKILL.md"] = FileEntry(
            content=_skill_md(
                name,
                sk.get("description", ""),
                sk["instructions"],
                sk.get("workflow", ""),
                sk.get("sandbox_step", ""),
            )
        )

    if not files:
        return ""

    repo = f"{slug}-skills"
    try:
        _ws_client(workspace).push_agent(repo, files=files, description=f"{customer} skills")
    except Exception as exc:
        if not _already_committed(exc):
            raise

    return repo


# Tools whose runtime path goes through a human-in-the-loop review interrupt.
_HITL_TOOLS = {"draft_email"}


def _persona_label(label: str) -> str:
    """Ensure a quick-action label reads as '<Persona>: <gist>'.

    Persona and gap actions arrive already formatted; skill actions carry the LLM's
    `action_label`. Anything still missing the persona prefix (a slip, older data)
    gets a generic one so the UI's bold-persona chip stays consistent.
    """
    label = (label or "").strip()
    if ":" in label:
        return label

    return f"Customer: {label}" if label else "Customer: Ask"


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
            "Fix the system prompt in Context Hub (the agent repo's AGENTS.md).",
            "Return to the assistant and re-run the last quick action; now it refuses to fabricate.",
        ]
    else:
        flow = [
            "Run the quick actions to show the assistant building dashboards across personas.",
            "Open the LangSmith trace to show the tool calls and how each answer stays grounded.",
        ]

    return {"brief": brief, "flow": flow}


def _selected_tools(payload: dict, analysis: dict) -> list[str]:
    """Resolve caller or analysis picks against the catalogue and required defaults."""
    if payload.get("enabled_tools"):
        picked = set(payload["enabled_tools"]) & CATALOGUE_IDS
    elif analysis.get("enabled_tools"):
        picked = (set(analysis["enabled_tools"]) & CATALOGUE_IDS) - EXPLICIT_ONLY
    else:
        picked = set()

    return sorted(picked | set(DEFAULT_ENABLED))


def _quick_actions(analysis: dict, actions: list, failure_mode: str) -> tuple[list, str]:
    """Prioritize skill questions and return normalized actions with their planted gap."""
    skill_actions = []
    for skill in analysis.get("skills") or []:
        question = skill.get("example_question")
        if question:
            label = skill.get("action_label") or skill["name"].replace("-", " ").title()
            skill_actions.append({"label": label, "question": question})

    base_actions = skill_actions + actions
    planted_gap = ""
    if failure_mode_needs_gap(failure_mode):
        planted_gap = analysis.get("data_gap") or "year-over-year figures by segment"
        gap_action = analysis.get("gap_action")
        actions = base_actions[:2]
        if gap_action and gap_action.get("question"):
            actions = actions + [{**gap_action, "kind": "gap"}]
    else:
        actions = base_actions[:3]

    return [{**a, "label": _persona_label(a.get("label", ""))} for a in actions], planted_gap


def _brand_metadata(brand: dict, analysis: dict) -> dict:
    """Resolve brand colors and fonts by source precedence for assistant display."""
    fonts = brand.get("fonts") or {}
    return {
        "accent": brand["accent"]
        or analysis.get("primary_color")
        or brand["accent_scraped"]
        or "#0072BC",
        "accent2": brand["accent2"] or analysis.get("secondary_color") or "",
        "brand_neutral": "",
        "brand_tint": DEFAULT_BRAND_TINT,
        "logo": brand["logo"],
        "theme": analysis.get("theme") or "dark",
        "font_heading": fonts.get("heading") or analysis.get("heading_font") or "",
        "font_body": fonts.get("body") or analysis.get("body_font") or "",
        "font_heading_fallback": analysis.get("heading_fallback") or DEFAULT_CURATED,
        "font_body_fallback": analysis.get("body_fallback") or DEFAULT_CURATED,
        "font_source": "google",
    }


def plan_demo(payload: dict, brand: dict, analysis: dict, *, sandbox_suffix: str) -> DemoPlan:
    """Resolve a coherent scenario without provisioning resources or publishing an assistant.

    The suffix is supplied by the caller so planning performs no randomness or I/O.
    Validation and selection preserve the setup policies, including truthy tool overrides.
    """
    customer = payload["customer"].strip()
    failure_mode = str(
        payload.get("failure_mode") or ("hallucination" if payload.get("hallucination") else "none")
    )
    actions = list(payload.get("actions") or analysis.get("actions") or [])
    # Refuse an unusable scenario before creating remote resources.
    if analysis.get("error") and not actions:
        raise RuntimeError(
            "Setup could not analyze this customer, so the assistant would have been "
            f"generic: {analysis['error']}. Nothing was created. Try again."
        )

    # Sample data must belong to this scenario; an unrelated default is not a fallback.
    if not analysis.get("seed_files"):
        raise SeedSpecError(
            "Setup produced no starting data files for this customer, so the assistant "
            "would have had an empty workspace and nothing to answer from"
            + (f": {analysis['error']}" if analysis.get("error") else "")
            + ". Nothing was created. Try again."
        )

    enabled_tools = _selected_tools(payload, analysis)
    actions, planted_gap = _quick_actions(analysis, actions, failure_mode)
    skills = list(analysis.get("skills") or [])
    if "push_widget" in enabled_tools:
        skills = [DASHBOARD_SKILL, *skills]

    slug = slugify(customer)
    return DemoPlan(
        workspace=payload["workspace"],
        customer=customer,
        owner=payload.get("owner", ""),
        industry=payload.get("industry") or analysis.get("industry") or "",
        use_case=str(payload.get("use_case") or "").strip(),
        failure_mode=failure_mode,
        display_name=payload.get("display_name") or f"{customer} GPT",
        slug=slug,
        sandbox_key=f"{slug}-{sandbox_suffix}",
        enabled_tools=enabled_tools,
        seed_files=analysis["seed_files"],
        skills=skills,
        actions=actions,
        planted_gap=planted_gap,
        branding=_brand_metadata(brand, analysis),
        push_prompts=bool(payload.get("push_prompts", True)),
        demo_traffic=bool(payload.get("demo_traffic")),
    )


def prepare_assistant(payload: dict) -> dict:
    """Analyze, plan and provision a demo; return the unchanged assistant creation payload.

    Publication is separate: the SPA creates the assistant from this result. Resource
    provisioning is not transactional, and optional evaluation/tagging work is best-effort.
    """
    if "workspace" not in payload:
        raise KeyError("workspace")

    customer = payload["customer"].strip()
    use_case = str(payload.get("use_case") or "").strip()
    # Independent blocking calls inherit this setup run's tracing context.
    with ThreadPoolExecutor(max_workers=2) as pool:
        brand_job = pool.submit(copy_context().run, fetch_brand, customer, payload.get("website"))
        analysis_job = pool.submit(
            copy_context().run,
            analyze_customer,
            customer,
            payload.get("industry", ""),
            payload.get("website"),
            use_case,
        )
        brand = cast("dict", brand_job.result())
        analysis = cast("dict", analysis_job.result())

    plan = plan_demo(payload, brand, analysis, sandbox_suffix=secrets.token_hex(3))
    context = plan.runtime_context()
    artifacts = LsArtifacts(workspace=plan.workspace, project=plan.customer)
    prompt_urls: dict = {}

    if plan.push_prompts:
        artifacts.skills_repo = push_skills_bundle(
            plan.workspace, plan.slug, plan.customer, plan.skills
        )
        if artifacts.skills_repo:
            context["skills_repo"] = artifacts.skills_repo

    prompt_text = build_system_prompt(
        plan.customer,
        plan.industry,
        failure_mode=plan.failure_mode,
        use_case=plan.use_case,
        dashboard=plan.dashboard_mode,
    ) + (_SKILLS_CLAUSE if artifacts.skills_repo else "")
    if plan.push_prompts:
        prompt_urls["system"] = push_agent_prompt(plan.workspace, plan.agent_repo, prompt_text)
        artifacts.agent_repo = plan.agent_repo
        context["agent_repo"] = artifacts.agent_repo
        threading.Thread(
            target=prewarm_sandbox,
            kwargs={
                "sandbox_key": plan.sandbox_key,
                "agent_repo": artifacts.agent_repo,
                "customer": plan.customer,
                "seed": plan.seed_files,
            },
            daemon=True,
        ).start()

    if plan.push_prompts:
        artifacts.eval_dataset = ensure_eval_dataset(
            plan.workspace, plan.customer, plan.failure_mode, plan.actions, plan.planted_gap
        )
        attached = ensure_dataset_evaluator(plan.workspace, artifacts.eval_dataset, plan.customer)
        artifacts.eval_rule_id = attached["rule_id"]
        artifacts.eval_evaluator_id = attached["evaluator_id"]
        if attached["error"]:
            print(f"[setup] eval evaluator not attached: {attached['error']}")

        # The current cleanup contract records this prompt only after rule attachment.
        if artifacts.eval_rule_id:
            artifacts.eval_judge_prompt = judge_prompt_name(artifacts.eval_dataset)

        threading.Thread(
            target=lambda: print(
                "[setup] application tag: "
                + str(
                    tag_assistant_resources(
                        api_key=os.getenv("LS_CROSS_WORKSPACE_KEY")
                        or os.getenv("LANGSMITH_API_KEY")
                        or "",
                        customer=plan.customer,
                        **artifacts.tagging_targets(),
                    )
                )
            ),
            daemon=True,
        ).start()

    # The queue is named before its optional background creation; cleanup tolerates absence.
    artifacts.annotation_queue = annotation_queue_name(plan.customer)
    if plan.push_prompts and plan.demo_traffic:
        start_demo_traffic(
            plan.workspace,
            plan.customer,
            context=context,
            actions=plan.actions,
            data_gap=plan.planted_gap,
            customer=plan.customer,
        )

    metadata = plan.metadata(artifacts)
    demo = build_demo_brief(
        plan.customer,
        plan.use_case,
        plan.actions,
        plan.enabled_tools,
        plan.failure_mode,
        plan.planted_gap,
    )
    metadata["demo_brief"] = demo["brief"]
    metadata["demo_flow"] = demo["flow"]
    return {
        "name": plan.customer,
        "display_name": plan.display_name,
        "accent": metadata["accent"],
        "accent2": metadata["accent2"],
        "logo": metadata["logo"],
        "actions": plan.actions,
        "metadata": metadata,
        "context": context,
        "prompt_urls": prompt_urls,
    }
