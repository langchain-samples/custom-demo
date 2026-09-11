"""Meridian Wealth — the demo MCP server the assistant connects to.

A small FastMCP server standing in for an advisory firm's own platform of
record, written against the **modern (stateless) MCP spec** so it exercises
everything `langchain.mcp` gained with it:

  * **Stateless HTTP.** No session is opened, nothing is pinned to one process,
    so the ngrok tunnel (or a redeploy behind it) can drop and reconnect without
    killing an in-flight conversation.
  * **Cacheable tool list.** `cache_ttl` is the server's own freshness hint, so a
    client with a cache serves `tools/list` from it instead of a round trip per
    run. Ours does (see `custom_demo/runtime/mcp_servers.py`).
  * **Elicitation.** Every interactive tool stops mid-call to ask the caller
    something. On the modern spec that is a retry-able round, not a held-open
    socket, which is what lets the agent surface it as a LangGraph interrupt and
    resume later. `schedule_review` is the bare version of the pattern: it asks
    with a plain schema and a host renders whatever form it likes.
  * **MCP Apps.** The other four bind a `ui://` HTML resource, so a host that
    understands the Apps extension renders real UI while the call is paused.
    Each earns one by collecting something a generated form cannot:
      - `propose_rebalance` — allocation sliders constrained to total 100%, with
        drift from policy and estimated tax drag recomputing as they move. "Take
        equities down four points and show me" is not a sentence a form accepts.
      - `project_goal` — retirement age, contribution and risk as sliders, with
        a projection band that redraws live. The maths runs inside the app,
        because a round trip per pixel would make it feel dead.
      - `confirm_trade` — a real order ticket with hold-to-confirm. The point is
        not the widget: an irreversible action gets a confirmation surface
        instead of the model interpreting the word "yes".
      - `sign_document` — a signature pad, because a wet signature is a wet
        signature.
  * **A multimodal result.** `sign_document` returns the drawn signature as an
    IMAGE block, which `langchain.mcp` converts to a LangChain image block, so
    the model can actually look at it. The bytes are also served at
    `/signatures/<reference>.png`, so a document can embed the picture by URL
    when the signature is too large to inline as a data URI.

Run it with `python -m mcp_demo_server` (see `__main__.py`), or expose it with
`./scripts/run_mcp_server.sh --tunnel`.

Configured by `MERIDIAN_HOST`, `MERIDIAN_PORT`, `MERIDIAN_PATH`,
`MERIDIAN_CACHE_TTL` and `MERIDIAN_MAX_INLINE`.

Deliberately NOT part of the deployment: the hatch wheel packages only
`custom_demo`, so nothing here ships to LangGraph Platform. It is the
*other* side of the connection.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from fastmcp.apps import UI_MIME_TYPE, AppConfig
from fastmcp.server.dependencies import get_http_request
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import InputRequiredResult
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse, Response

from mcp_demo_server.apps import render_app
from mcp_demo_server.elicit import answer_for, ask, attach_context
from mcp_demo_server.images import inline_budget, png_bytes, renderable

# How long a client may serve `tools/list` from its cache before re-asking. Short
# enough that adding a tool shows up in the next run or two, long enough that a
# busy thread is not re-discovering the same catalogue every turn. SEP-2549.
CACHE_TTL_SECONDS = int(os.getenv("MERIDIAN_CACHE_TTL", "300"))

# Only used to build a URL when there is no HTTP request to read one off (the
# in-process transport the tests use). A real call always has one.
HOST_HINT = f"{os.getenv('MERIDIAN_HOST', '127.0.0.1')}:{os.getenv('MERIDIAN_PORT', '8765')}"

REBALANCE_URI = "ui://meridian/rebalance.html"
PROJECTION_URI = "ui://meridian/projection.html"
TRADE_URI = "ui://meridian/trade.html"
SIGNATURE_URI = "ui://meridian/signature.html"

mcp = FastMCP(
    "Meridian Wealth",
    instructions=(
        "Meridian is the advisory platform of record: household accounts, model "
        "portfolios, order entry and client documents. Look positions and prices "
        "up here rather than estimating them, and never place or amend an order "
        "without going through `confirm_trade`."
    ),
    version="1.0.0",
    # `public` because the catalogue is identical for every caller — there is no
    # per-user tool visibility to leak between principals.
    cache_ttl=CACHE_TTL_SECONDS,
    cache_scope="public",
)


# --------------------------------------------------------------------------- #
# The pretend book of business.                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Sleeve:
    """One asset-class sleeve inside a model portfolio."""

    key: str
    label: str
    weight: float  # current allocation, percent
    target: float  # policy target, percent
    unrealized_gain_pct: float  # share of value that is gain, drives the tax estimate


@dataclass(frozen=True)
class Account:
    """A household advisory account."""

    id: str
    household: str
    value: float
    current_age: int
    goal: float
    sleeves: tuple[Sleeve, ...]
    tax_rate: float = 0.238  # long-term federal plus NIIT, the usual demo number
    holdings: dict[str, tuple[str, float, int]] = field(default_factory=dict)


_ACCOUNTS: tuple[Account, ...] = (
    Account(
        id="MW-10241",
        household="Whitfield Family Trust",
        value=4_820_000,
        current_age=58,
        goal=6_500_000,
        sleeves=(
            Sleeve("us_equity", "US equity", 46.0, 38.0, 0.42),
            Sleeve("intl_equity", "Intl equity", 12.0, 17.0, 0.11),
            Sleeve("fixed_income", "Fixed income", 22.0, 30.0, 0.03),
            Sleeve("alternatives", "Alternatives", 14.0, 10.0, 0.26),
            Sleeve("cash", "Cash", 6.0, 5.0, 0.0),
        ),
        holdings={
            "AAPL": ("Apple Inc.", 227.14, 4200),
            "MSFT": ("Microsoft Corp.", 418.60, 1800),
            "AGG": ("iShares Core US Aggregate Bond", 99.32, 9500),
        },
    ),
    Account(
        id="MW-10388",
        household="Okonkwo Retirement IRA",
        value=1_140_000,
        current_age=45,
        goal=2_000_000,
        sleeves=(
            Sleeve("us_equity", "US equity", 61.0, 55.0, 0.31),
            Sleeve("intl_equity", "Intl equity", 14.0, 18.0, 0.08),
            Sleeve("fixed_income", "Fixed income", 18.0, 22.0, 0.02),
            Sleeve("alternatives", "Alternatives", 3.0, 3.0, 0.05),
            Sleeve("cash", "Cash", 4.0, 2.0, 0.0),
        ),
        holdings={
            "VTI": ("Vanguard Total Stock Market", 289.05, 2100),
            "VXUS": ("Vanguard Total Intl Stock", 63.77, 2500),
        },
    ),
)

_BY_ID = {a.id: a for a in _ACCOUNTS}

# Written by the interactive tools. Module-level rather than session state: the
# server is stateless, and a demo runs one process.
_PLANS: dict[str, dict[str, Any]] = {}
_REVIEWS: dict[str, dict[str, Any]] = {}
_ORDERS: list[dict[str, Any]] = []
_SIGNED: dict[str, dict[str, Any]] = {}


def _account(raw: str) -> Account | None:
    return _BY_ID.get(raw.strip().upper())


def _unknown(raw: str) -> dict[str, str]:
    return {"error": f"No account {raw!r} at Meridian. Known: {', '.join(sorted(_BY_ID))}."}


def _public_base() -> str:
    """The address a CLIENT can reach this server on, as seen from the request.

    Read off the live request rather than configured, because the useful answer
    is the tunnel's hostname and that changes every time ngrok restarts. ngrok
    sets the forwarded headers, so this resolves to the public https URL rather
    than the loopback one the server is bound to.
    """
    try:
        request = get_http_request()
    except Exception:  # noqa: BLE001 - in-process transport has no HTTP request
        return f"http://{HOST_HINT}"

    headers = request.headers
    scheme = headers.get("x-forwarded-proto") or request.url.scheme
    host = headers.get("x-forwarded-host") or headers.get("host") or request.url.netloc
    return f"{scheme}://{host}"


# --------------------------------------------------------------------------- #
# Plain tools.                                                                 #
# --------------------------------------------------------------------------- #


@mcp.tool(annotations={"readOnlyHint": True})
def list_accounts() -> dict[str, Any]:
    """List the advisory accounts under management, with their current drift.

    Use this to find an account id before proposing a rebalance, modelling a
    goal, booking a review, or entering an order.
    """
    rows = []
    for a in _ACCOUNTS:
        drift = sum(abs(s.weight - s.target) for s in a.sleeves)
        rows.append(
            {
                "account_id": a.id,
                "household": a.household,
                "value": a.value,
                "drift_from_policy": round(drift, 1),
                "needs_rebalance": drift > 8.0,
            }
        )

    return {"count": len(rows), "accounts": rows}


@mcp.tool(annotations={"readOnlyHint": True})
def get_account(
    account_id: Annotated[str, Field(description="A Meridian account id, e.g. MW-10241.")],
) -> dict[str, Any]:
    """Look one account up: allocation against policy, holdings, and goal."""
    account = _account(account_id)
    if account is None:
        return _unknown(account_id)

    return {
        "account_id": account.id,
        "household": account.household,
        "value": account.value,
        "goal": account.goal,
        "allocation": [
            {
                "sleeve": s.label,
                "current": s.weight,
                "policy": s.target,
                "drift": round(s.weight - s.target, 1),
            }
            for s in account.sleeves
        ],
        "holdings": [
            {"symbol": sym, "name": name, "last": last, "quantity": qty}
            for sym, (name, last, qty) in account.holdings.items()
        ],
        "plan_on_file": account.id in _PLANS,
        "review_booked": account.id in _REVIEWS,
    }


# --------------------------------------------------------------------------- #
# Elicitation without an App: the server stops mid-call and asks.              #
# --------------------------------------------------------------------------- #
#
# Every interactive tool here is a GUARD TOOL (SEP-2322), not a `ctx.elicit()`
# caller. On the modern stateless protocol there is no open session for a server
# to push a question down, so `ctx.elicit()` fails with "elicitation via
# server-initiated requests is unavailable". Instead a round of asking is an
# ordinary result: the tool returns an `InputRequiredResult` naming what it
# needs, the client re-calls the same tool with `input_responses` attached, and
# `ctx.input_responses` tells the body which round it is in.
#
# That shape is exactly why `langchain.mcp` can surface it as a LangGraph
# `interrupt()`: a retry-able round survives the pause, an open socket would not.
#
# `schedule_review` is the pattern with nothing on top of it - no `ui://`
# resource, so the host generates a form from the schema. It is here so the bare
# mechanism stays visible next to the four tools that dress it up.


class ReviewSlot(BaseModel):
    """What the caller must supply before an annual review can be booked."""

    review_date: str = Field(description="Date of the review, YYYY-MM-DD.")
    window: Literal["morning", "afternoon", "evening"] = Field(
        description="Which part of the day the household is available."
    )
    attending: str = Field(description="Who from the household attends, and how to reach them.")


@mcp.tool
def schedule_review(
    account_id: Annotated[str, Field(description="The account whose review to book.")],
    ctx: Context,
) -> dict[str, Any] | InputRequiredResult:
    """Book an annual review meeting, asking the user for the details.

    Only the account id is needed to start: the date, time window and who is
    attending are ELICITED from the user mid-call, so do not invent them and do
    not ask for them yourself first. The call pauses, the user fills them in, and
    the booked slot comes back as the result.
    """
    account = _account(account_id)
    if account is None:
        return _unknown(account_id)

    answer = answer_for(ctx, "slot")
    if answer is None:
        # Nothing above this line does real work, which matters: the client
        # re-calls this tool from the top with the answer attached, so cheap
        # lookups repeat harmlessly where a write would repeat too.
        return ask(
            "slot",
            f"When should the annual review for {account.household} ({account.id}) be held?",
            ReviewSlot.model_json_schema(),
        )

    if answer.action == "decline":
        return {"status": "not_scheduled", "reason": "The user declined to pick a slot."}

    if answer.action == "cancel":
        return {"status": "cancelled", "reason": "The user cancelled the booking."}

    slot = ReviewSlot.model_validate(answer.content or {})
    record = {
        "status": "scheduled",
        "account_id": account.id,
        "household": account.household,
        "review_date": slot.review_date,
        "window": slot.window,
        "attending": slot.attending,
        "booked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _REVIEWS[account.id] = record
    return record


# --------------------------------------------------------------------------- #
# MCP Apps: the same pause, rendered by HTML the server ships.                 #
# --------------------------------------------------------------------------- #


def _rebalance_view(account: Account) -> dict[str, Any]:
    """What the rebalance app needs to draw itself.

    Plain nested data, returned as the tool's own result. An app gets it through
    `ui/notifications/tool-result` and reads `structuredContent`, which is the
    ordinary MCP Apps route: presentation data travels as the result, not as
    anything bolted onto a schema.
    """
    return {
        "account_id": account.id,
        "household": account.household,
        "portfolio_value": account.value,
        "tax_rate": account.tax_rate,
        "sleeves": [
            {
                "key": s.key,
                "label": s.label,
                "weight": s.weight,
                "target": s.target,
                "unrealized_gain_pct": s.unrealized_gain_pct,
            }
            for s in account.sleeves
        ],
    }


@mcp.tool(app=AppConfig(resource_uri=REBALANCE_URI, prefers_border=False))
def propose_rebalance(
    account_id: Annotated[str, Field(description="The account to rebalance.")],
) -> dict[str, Any]:
    """Open the rebalance app on an account, so the advisor can set the allocation.

    This tool renders its own UI: a host that supports MCP Apps shows allocation
    sliders for every sleeve, constrained to total 100%, with drift from policy
    and estimated tax drag updating as they move. Call it with only the account
    id. Do NOT propose weights yourself, ask which sleeves to change, or describe
    the trades first: the advisor sets them in the app, which submits them.

    The result is the account's current allocation, which is what the app draws.
    The trades come back separately, when the advisor submits.
    """
    account = _account(account_id)
    if account is None:
        return _unknown(account_id)

    return _rebalance_view(account)


@mcp.tool(app=AppConfig(resource_uri=REBALANCE_URI, visibility=["app"]))
def submit_rebalance(
    account_id: Annotated[str, Field(description="The account being rebalanced.")],
    allocation: Annotated[
        dict[str, float], Field(description="Target weight per sleeve key, percent.")
    ],
) -> dict[str, Any]:
    """Submit the allocation the advisor set in the rebalance app.

    `visibility: ["app"]` keeps this out of the model's tool list: the weights
    come from a person moving sliders, and a model guessing at them is the thing
    the app exists to prevent. Only `propose_rebalance`'s app may call it.
    """
    account = _account(account_id)
    if account is None:
        return _unknown(account_id)

    weights = {
        # `v` is bound once so the isinstance guard below and the float() above apply
        # to the SAME value. Reading the dict twice read as unnarrowed to the checker,
        # and meant a sleeve whose value changed between the two reads could convert
        # something the guard had approved in a different form.
        s.key: float(v)
        for s in account.sleeves
        for v in (allocation.get(s.key, s.weight),)
        if isinstance(v, (int, float))
    }
    total = sum(weights.values())
    if abs(total - 100.0) > 0.5:
        return {"error": f"Allocation totals {total:.1f}%, not 100%. Nothing was submitted."}

    by_key = {s.key: s for s in account.sleeves}
    trades, tax = [], 0.0
    for key, weight in weights.items():
        sleeve = by_key.get(key)
        if sleeve is None:
            continue

        delta = weight - sleeve.weight
        if abs(delta) < 0.05:
            continue

        amount = abs(delta) / 100 * account.value
        realized = amount * sleeve.unrealized_gain_pct if delta < 0 else 0.0
        tax += realized * account.tax_rate
        trades.append(
            {
                "sleeve": sleeve.label,
                "side": "BUY" if delta > 0 else "SELL",
                "amount": round(amount, 2),
                "from_pct": sleeve.weight,
                "to_pct": weight,
            }
        )

    return {
        "status": "approved",
        "account_id": account.id,
        "household": account.household,
        "trades": trades,
        "trade_count": len(trades),
        "estimated_tax": round(tax, 2),
        "residual_drift": round(
            sum(abs(weights.get(s.key, s.weight) - s.target) for s in account.sleeves), 1
        ),
        "submitted_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


class GoalPlan(BaseModel):
    """The plan the advisor settled on."""

    retirement_age: int = Field(description="Age at which drawdown begins.")
    monthly_contribution: float = Field(description="Contribution per month until then.")
    risk_level: str = Field(description="Risk posture, e.g. Balanced.")


@mcp.tool(app=AppConfig(resource_uri=PROJECTION_URI, prefers_border=False))
def project_goal(
    account_id: Annotated[str, Field(description="The account whose goal to model.")],
    ctx: Context,
) -> dict[str, Any] | InputRequiredResult:
    """Model progress toward an account's goal and capture the chosen plan.

    This tool renders its own UI: a host that supports MCP Apps shows sliders for
    retirement age, monthly contribution and risk, with a projection band that
    redraws as they move. Call it with only the account id. Do NOT invent a
    contribution or a return assumption and do NOT describe a projection in
    text: the advisor explores it in the app, and the plan they settle on comes
    back as the result.
    """
    account = _account(account_id)
    if account is None:
        return _unknown(account_id)

    answer = answer_for(ctx, "plan")
    if answer is None:
        schema = attach_context(
            GoalPlan.model_json_schema(),
            "retirement_age",
            {
                "current_age": account.current_age,
                "balance": account.value,
                "goal": account.goal,
                "default_age": max(account.current_age + 1, 65),
            },
        )
        return ask(
            "plan",
            f"{account.household}: ${account.value:,.0f} today, goal ${account.goal:,.0f}.",
            schema,
        )

    if answer.action != "accept":
        return {"status": "no_plan", "reason": f"The advisor {answer.action}ed."}

    plan = GoalPlan.model_validate(answer.content or {})
    record = {
        "account_id": account.id,
        "household": account.household,
        "retirement_age": plan.retirement_age,
        "monthly_contribution": plan.monthly_contribution,
        "risk_level": plan.risk_level,
        "years_to_goal": plan.retirement_age - account.current_age,
        "goal": account.goal,
        "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _PLANS[account.id] = record
    return {"status": "saved", **record}


class TradeTicket(BaseModel):
    """The order the advisor confirmed."""

    quantity: int = Field(description="Shares to trade.")
    order_type: Literal["market", "limit"] = Field(description="Market or limit.")
    limit_price: float | None = Field(default=None, description="Limit price, when a limit order.")
    time_in_force: Literal["day", "gtc", "ioc"] = Field(description="How long the order rests.")
    confirmed: bool = Field(description="True once the advisor has held to confirm.")


@mcp.tool(app=AppConfig(resource_uri=TRADE_URI, prefers_border=False))
def confirm_trade(
    account_id: Annotated[str, Field(description="The account to trade in.")],
    symbol: Annotated[str, Field(description="Ticker to trade, e.g. AAPL.")],
    side: Annotated[Literal["buy", "sell"], Field(description="Direction of the order.")],
    ctx: Context,
) -> dict[str, Any] | InputRequiredResult:
    """Place an order, confirmed by the advisor on a real ticket.

    This tool renders its own UI: a host that supports MCP Apps shows an order
    ticket with a quantity stepper, market/limit, time in force, an estimated
    total, and a hold-to-confirm button. Call it with the account, symbol and
    side only. Do NOT ask the user to confirm in chat and do NOT decide the
    quantity yourself: the ticket is the confirmation, and an order only exists
    once it comes back from there.
    """
    account = _account(account_id)
    if account is None:
        return _unknown(account_id)

    holding = account.holdings.get(symbol.strip().upper())
    if holding is None:
        known = ", ".join(sorted(account.holdings))
        return {"error": f"{symbol!r} is not held in {account.id}. Held: {known}."}

    name, last, held_qty = holding

    answer = answer_for(ctx, "ticket")
    if answer is None:
        schema = attach_context(
            TradeTicket.model_json_schema(),
            "quantity",
            {
                "side": side.upper(),
                "symbol": symbol.strip().upper(),
                "name": name,
                "last": last,
                "fee": 4.95,
                # A sale cannot exceed the position; a purchase is uncapped here.
                "max_qty": held_qty if side == "sell" else 0,
                "default_qty": min(100, held_qty) if side == "sell" else 100,
            },
        )
        return ask(
            "ticket",
            f"{side.upper()} {symbol.strip().upper()} in {account.household} ({account.id}).",
            schema,
        )

    if answer.action != "accept":
        return {"status": "not_placed", "reason": f"The advisor {answer.action}ed the ticket."}

    ticket = TradeTicket.model_validate(answer.content or {})
    if side == "sell" and ticket.quantity > held_qty:
        return {"error": f"Cannot sell {ticket.quantity}; only {held_qty} held. Nothing placed."}

    price = ticket.limit_price if ticket.order_type == "limit" else last
    order = {
        "order_id": f"MW-ORD-{len(_ORDERS) + 1041}",
        "status": "accepted",
        "account_id": account.id,
        "symbol": symbol.strip().upper(),
        "side": side.upper(),
        "quantity": ticket.quantity,
        "order_type": ticket.order_type,
        "limit_price": ticket.limit_price,
        "time_in_force": ticket.time_in_force,
        "estimated_principal": round(ticket.quantity * (price or 0), 2),
        "placed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _ORDERS.append(order)
    return order


class SignatureCapture(BaseModel):
    """A wet signature on an advisory document."""

    signature: str = Field(description="The drawn signature as a PNG data URI.")
    signed_by: str = Field(description="Printed name of the signer.")
    signed_at: str = Field(description="ISO-8601 timestamp of when it was signed.")


def _document_reference(account_id: str, document: str) -> str:
    """A stable id for one signed document, usable in a URL.

    A digest rather than `hash()`, which is salted per process: the reference is
    what the PNG route is keyed on, so the same document has to resolve to the
    same id after a restart.
    """
    digest = hashlib.sha256(f"{account_id}:{document}".encode()).hexdigest()
    return f"MW-DOC-{int(digest[:8], 16) % 100000:05d}"


@mcp.tool(app=AppConfig(resource_uri=SIGNATURE_URI, prefers_border=False))
def sign_document(
    account_id: Annotated[str, Field(description="The account the document belongs to.")],
    document: Annotated[str, Field(description="What is being signed, e.g. 'IPS amendment'.")],
    ctx: Context,
    # ToolResult because the countersigned document comes back as TWO content
    # blocks, text plus the image, which is what lets the model see the signature.
) -> dict[str, Any] | InputRequiredResult | ToolResult:
    """Capture a client's wet signature on an advisory document.

    This tool renders its own UI: a host that supports MCP Apps shows a signature
    pad at `ui://meridian/signature.html` while the call is paused. Never ask the
    client to type a signature, describe one, or supply `signed_by` yourself: the
    pad collects all of it and the signed record comes back as the result.

    The result carries the signature two ways. You are shown the drawn signature
    as an IMAGE, so you can describe or check it. And `signature_data_uri` is a
    complete `data:image/png;base64,...` value of a few KB: copy it verbatim into
    `<img src="{signature_data_uri}" alt="Client signature">` to put it in a
    document, which then needs no network at all - it renders offline, prints to
    PDF, and still shows the signature after this server has gone away. Copy
    every character; do not truncate it, do not abbreviate it with an ellipsis,
    and do not invent one.

    `signature_url` is the same image over HTTP, for when `signature_data_uri` is
    null because the signature was too large to inline. Prefer the data URI
    whenever it is present.
    """
    account = _account(account_id)
    if account is None:
        return _unknown(account_id)

    answer = answer_for(ctx, "signature")
    if answer is None:
        return ask(
            "signature",
            f"{document} for {account.household} ({account.id}).",
            SignatureCapture.model_json_schema(),
        )

    if answer.action != "accept":
        return {"status": "unsigned", "reason": f"The client {answer.action}ed."}

    capture = SignatureCapture.model_validate(answer.content or {})
    png = png_bytes(capture.signature)
    reference = _document_reference(account.id, document)
    # The bytes stay here. They are served over `signature_url` and shown to the
    # model as an image block; the base64 itself is only ever handed back as the
    # data URI, and only while it is small enough to be worth the context.
    _SIGNED[reference] = {
        "account_id": account.id,
        "document": document,
        "signed_by": capture.signed_by,
        "signed_at": capture.signed_at,
        "signature": capture.signature,
    }

    summary: dict[str, Any] = {
        "status": "signed",
        "account_id": account.id,
        "document": document,
        "signed_by": capture.signed_by,
        "signed_at": capture.signed_at,
        "reference": reference,
        # Both, and the data URI is the one to use. The pad crops to the ink and
        # exports at CSS scale, so a signature is a couple of KB rather than the
        # 13.7KB a full-pad export at device resolution produced: small enough to
        # paste straight into an <img>, which means a document that needs no
        # network at all, renders in a PDF, and survives this server going away.
        # The URL stays as the fallback for one too big to inline.
        "signature_data_uri": capture.signature,
        "signature_url": f"{_public_base()}/signatures/{reference}.png",
    }
    # Past this, inlining costs more context than the picture is worth, and the
    # model starts truncating it rather than copying it.
    if len(capture.signature) > inline_budget("MERIDIAN_MAX_INLINE"):
        summary["signature_data_uri"] = None
        summary["note"] = (
            f"Signature is {len(capture.signature) // 1024}KB, too large to inline; "
            "use signature_url."
        )

    if png is None:
        # A malformed data URI is the signer's UI misbehaving, not a failed
        # signing: keep the signed record, but do not promise an image.
        summary["signature_data_uri"] = None
        summary["signature_url"] = None
        summary["note"] = "The signature image could not be decoded and was not stored."
        return ToolResult(structured_content=summary)

    # Two content blocks. The text is what the model reasons over; the image is
    # so it can actually SEE the signature (MCP multimodal results convert to a
    # LangChain image block), which is what makes "does this look signed?" a
    # question it can answer. The image is dropped rather than risked when it is
    # too small to be accepted: the URL and the record still stand, and losing
    # the picture beats losing the run.
    content: list[Any] = [json.dumps(summary)]
    if renderable(png):
        content.append(Image(data=png, format="png").to_image_content())

    return ToolResult(content=content, structured_content=summary)


@mcp.custom_route("/signatures/{reference}.png", methods=["GET"])
async def signature_png(request) -> Response:
    """Serve a stored signature as a real PNG.

    The reason this exists rather than returning base64 in the tool result: a
    countersigned document needs the image, and when the signature is too large
    to inline, the only way to get it there without the bytes passing through the
    model (which cannot reproduce them) is a URL it can put in an `<img>`. Public
    and unauthenticated, like the rest of this demo server: the tunnel is the
    boundary.
    """
    reference = request.path_params["reference"].upper()
    record = _SIGNED.get(reference)
    png = png_bytes((record or {}).get("signature", ""))
    if png is None:
        return JSONResponse({"error": f"No signature on file for {reference}."}, status_code=404)

    return Response(
        png,
        media_type="image/png",
        # A signature never changes once collected, and a document may load it
        # long after the run that captured it.
        headers={"Cache-Control": "public, max-age=86400"},
    )


# --------------------------------------------------------------------------- #
# The apps themselves.                                                         #
# --------------------------------------------------------------------------- #


@mcp.resource(REBALANCE_URI, mime_type=UI_MIME_TYPE, name="Rebalance sliders")
def rebalance_app() -> str:
    """Serve the allocation sliders."""
    return render_app("rebalance", title="Rebalance")


@mcp.resource(PROJECTION_URI, mime_type=UI_MIME_TYPE, name="Goal projection")
def projection_app() -> str:
    """Serve the goal projection."""
    return render_app("projection", title="Goal projection")


@mcp.resource(TRADE_URI, mime_type=UI_MIME_TYPE, name="Order ticket")
def trade_app() -> str:
    """Serve the order ticket."""
    return render_app("trade", title="Order ticket")


@mcp.resource(SIGNATURE_URI, mime_type=UI_MIME_TYPE, name="Signature pad")
def signature_app() -> str:
    """Serve the signature pad."""
    return render_app("signature", title="Signature")
