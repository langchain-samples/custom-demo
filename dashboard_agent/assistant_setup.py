"""Core logic for the deployed assistant-setup agent (Part 3).

Keyless brand fetch (Logo.dev logo from the customer's domain + parse the site for
an accent color), LLM-generated persona quick-actions, and prompt building/pushing.
The graph node (setup_graph.py) calls these and returns a ready assistant payload
(metadata + context); the SPA then creates the assistant from that payload.
"""

from __future__ import annotations

import json
import os
import re

import httpx
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langsmith import Client

from .config import load_env
from .prompt import build_system_prompt
from .tools import DEFAULT_ENABLED

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
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "customer"


def domain_for(customer: str, website: str | None) -> str:
    if website:
        m = re.search(r"^(?:https?://)?(?:www\.)?([^/]+)", website.strip())
        if m:
            return m.group(1)
    # Best-effort guess from the customer name (Clearbit tolerates many forms).
    return slugify(customer).replace("-", "") + ".com"


def _brandfetch_brand(domain: str) -> dict | None:
    """Accurate current palette (+ logo) from Brandfetch's Brand API, or None on
    any failure (no key, rate-limit/quota, network, unknown domain) so callers
    fall back to the LLM guess. Free tier is ~100 pulls, so failures are expected."""
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
    """Brand assets: the Logo.dev logo, plus a Brandfetch palette when available
    (accurate/current), else a scraped <meta theme-color> as a weak accent fallback.
    Returns accent/accent2 empty when unknown so the caller can prefer the LLM guess."""
    domain = domain_for(customer, website)
    logo = f"https://img.logo.dev/{domain}?token={LOGODEV_TOKEN}&size=128&format=png&retina=true"
    accent = ""       # authoritative (Brandfetch) — empty when unavailable
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

    return {"domain": domain, "logo": logo, "accent": accent, "accent2": accent2,
            "neutral": neutral, "fonts": fonts, "accent_scraped": accent_scraped}


INDUSTRIES = [
    "Governmental", "Non-profit / NGO", "Healthcare", "Financial Services",
    "Technology", "Education", "Retail", "Manufacturing", "Energy & Utilities",
    "Logistics & Transport", "Media & Entertainment", "Other",
]


def analyze_customer(customer: str, industry: str = "", website: str | None = None,
                     model: str | None = None) -> dict:
    """One LLM call → infer industry (unless given), 3 persona quick-actions, and a
    customer-specific 'data gap' + a question that probes it (the hallucination trigger)."""
    load_env()
    llm = init_chat_model(model or "anthropic:claude-haiku-4-5-20251001", temperature=0.5)
    site = f" (website: {website})" if website else ""
    prompt = (
        f"A live analytics dashboard is being set up for the customer '{customer}'{site}.\n"
        f"1) Classify the customer into ONE industry from this list: {', '.join(INDUSTRIES)}.\n"
        "2) Propose exactly 3 example questions an end user might ask, spanning different "
        "personas. Each 'label' MUST be '<Persona role>: <2-4 word gist>' — e.g. "
        "'Chief Revenue Officer: Revenue per product' or 'Store Operations Manager: Top 10 stores'.\n"
        "3) Pick ONE specific, plausible metric/topic this customer would care about that we will "
        "pretend the data source is MISSING (the 'data_gap', a short noun phrase, e.g. "
        "'conversion rate by traffic source' or 'schools rebuilt'). Then write ONE question a user "
        "would naturally ask that depends on that missing data (the hallucination trigger).\n"
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
        "Return STRICT JSON, no prose:\n"
        '{"industry":"<one of the list>",'
        '"actions":[{"label":"<Persona role>: <2-4 word gist>","question":"<question>"}],'
        '"data_gap":"<short noun phrase>",'
        '"gap_action":{"label":"<Persona role>: <2-4 word gist>","question":"<question that needs the gap data>"},'
        '"primary_color":"#RRGGBB","secondary_color":"#RRGGBB","neutral_color":"#RRGGBB",'
        '"theme":"light|dark",'
        '"heading_font":"","body_font":"","heading_fallback":"","body_fallback":""}'
    )
    out: dict = {"industry": industry or "", "actions": [], "data_gap": "", "gap_action": None,
                 "primary_color": "", "secondary_color": "", "neutral_color": "", "theme": "dark",
                 "heading_font": "", "body_font": "",
                 "heading_fallback": DEFAULT_CURATED, "body_fallback": DEFAULT_CURATED}
    try:
        content = llm.invoke([HumanMessage(prompt)]).content
        if isinstance(content, list):
            content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        m = re.search(r"\{.*\}", content, re.S)
        data = json.loads(m.group(0)) if m else {}
        if not industry:
            out["industry"] = str(data.get("industry", "")).strip()
        out["actions"] = [
            {"label": str(a.get("label", "")).strip(), "question": str(a.get("question", "")).strip()}
            for a in (data.get("actions") or [])
            if isinstance(a, dict) and a.get("question")
        ][:3]
        out["data_gap"] = str(data.get("data_gap", "")).strip()
        ga = data.get("gap_action")
        if isinstance(ga, dict) and ga.get("question"):
            out["gap_action"] = {"label": str(ga.get("label", "")).strip(), "question": str(ga.get("question", "")).strip()}
        for k in ("primary_color", "secondary_color", "neutral_color"):
            v = str(data.get(k, "")).strip()
            if re.fullmatch(r"#[0-9a-fA-F]{6}", v):
                out[k] = v
        # Validated before storage — an unvetted family must never reach metadata.
        for k in ("heading_font", "body_font"):
            out[k] = safe_font_name(str(data.get(k, "")))
        for k in ("heading_fallback", "body_fallback"):
            out[k] = safe_curated(str(data.get(k, "")))
        theme = str(data.get("theme", "")).strip().lower()
        if theme in ("light", "dark"):
            out["theme"] = theme
    except Exception:
        pass
    return out


def _ws_client(workspace: str | None):
    load_env()
    key = os.getenv("LS_CROSS_WORKSPACE_KEY") or os.getenv("LANGSMITH_API_KEY")
    api_url = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    return Client(api_key=key, api_url=api_url, workspace_id=workspace or None)


def push_prompt(workspace: str, name: str, text: str) -> str:
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


def prepare_assistant(payload: dict) -> dict:
    """Turn setup inputs into a ready assistant payload (metadata + context).

    Inputs: workspace, customer, owner, industry, website, hallucination,
    push_prompts. Does brand fetch + action generation + optional prompt push.
    Returns {name, display_name, accent, logo, actions, metadata, context, prompt_urls}.
    """
    workspace = payload["workspace"]
    customer = payload["customer"].strip()
    owner = payload.get("owner", "")
    hallucination = bool(payload.get("hallucination"))
    push = payload.get("push_prompts", True)

    brand = fetch_brand(customer, payload.get("website"))
    analysis = analyze_customer(customer, payload.get("industry", ""), payload.get("website"))
    industry = payload.get("industry") or analysis.get("industry") or ""
    actions = list(payload.get("actions") or analysis.get("actions") or [])
    display_name = payload.get("display_name") or f"{customer} GPT"

    slug = slugify(customer)
    context: dict = {
        "ls_workspace": workspace,
        "customer": customer,
        # Traces land in a per-customer, clearly-labelled project rather than one
        # named just for the client — which could collide with a real project in
        # that workspace. The SPA derives the same name (see traceProject()).
        "ls_project": f"{slug}-corebot-demo",
    }
    if industry:
        context["industry"] = industry
    # Write the default tool selection explicitly, so a new assistant shows a
    # concrete, editable value in the settings panel rather than relying on the
    # unset fallback. Callers may override via `enabled_tools`.
    context["enabled_tools"] = list(payload.get("enabled_tools") or sorted(DEFAULT_ENABLED))
    prompt_urls: dict = {}

    # Always give the assistant a fixed, customer-templated system prompt (reliable
    # setup — no per-customer prompt writing). Hallucination appends the demo clause.
    sys_text = build_system_prompt(customer, industry, hallucination)
    if push:
        name = f"{slug}-system"
        prompt_urls["system"] = push_prompt(workspace, name, sys_text)
        context["prompt_name"] = name
    else:
        context["prompt"] = sys_text

    if hallucination:
        # Synthetic data source withholds a customer-specific gap (backend builds a
        # customer-centric data prompt from data_gap + customer). The quick actions
        # become exactly two "good" probes + the gap probe LAST, so the demo reliably
        # shows two grounded answers then one fabrication over the missing data.
        gap = analysis.get("data_gap") or "year-over-year figures by segment"
        context["data_gap"] = gap
        context["dataset"] = "synthetic"
        gap_action = analysis.get("gap_action")
        if gap_action and gap_action.get("question"):
            actions = actions[:2] + [gap_action]
        else:
            actions = actions[:2]
    else:
        # Clean assistant: all quick actions are grounded ("good").
        actions = actions[:3]

    # Brand colors — priority: Brandfetch (accurate/current) → LLM known-brand
    # guess → scraped site theme-color → default. Secondary drives the 2nd series.
    accent = brand["accent"] or analysis.get("primary_color") or brand["accent_scraped"] or "#0072BC"
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
    }
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
