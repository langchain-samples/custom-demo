"""Meridian Wealth — the wealth-management demo MCP server.

The second of the two demo servers, and the one whose tools are worth showing to
an advisory firm. Same spec surface as `server.py` (stateless HTTP, a cacheable
tool list, guard-pattern elicitation), but every interactive tool here is an
**MCP App**, because each collects something a generated form genuinely cannot:

  * `propose_rebalance` — drag allocation sliders that must total 100%, with
    drift from policy and estimated tax drag recomputing as you move them. "Take
    equities down four points and show me" is not a sentence a form can accept.
  * `project_goal` — drag retirement age, contribution and risk, and a
    projection band redraws live. The maths runs inside the app, because a round
    trip per pixel would make it feel dead.
  * `confirm_trade` — a real order ticket with hold-to-confirm. The point is not
    the widget: an irreversible action gets a confirmation surface instead of the
    model interpreting the word "yes".

`sign_document` is the same signature pad the logistics server uses, re-pointed
at an advisory document, since a wet signature is a wet signature.

Run it with `./scripts/run_mcp_server.sh --wealth` (add `--tunnel` for a public
URL). Not part of the deployment: the wheel packages only `dashboard_agent`.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from fastmcp.apps import UI_MIME_TYPE, AppConfig
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import InputRequiredResult
from pydantic import BaseModel, Field

from mcp_demo_server.apps import render_app
from mcp_demo_server.elicit import answer_for, ask, attach_context

CACHE_TTL_SECONDS = int(os.getenv("MERIDIAN_CACHE_TTL", "300"))

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
_ORDERS: list[dict[str, Any]] = []
_SIGNED: dict[str, dict[str, Any]] = {}


def _account(raw: str) -> Account | None:
    return _BY_ID.get(raw.strip().upper())


def _unknown(raw: str) -> dict[str, str]:
    return {"error": f"No account {raw!r} at Meridian. Known: {', '.join(sorted(_BY_ID))}."}


# --------------------------------------------------------------------------- #
# Plain tools.                                                                 #
# --------------------------------------------------------------------------- #


@mcp.tool(annotations={"readOnlyHint": True})
def list_accounts() -> dict[str, Any]:
    """List the advisory accounts under management, with their current drift.

    Use this to find an account id before proposing a rebalance, modelling a
    goal, or entering an order.
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
    }


# --------------------------------------------------------------------------- #
# MCP Apps.                                                                    #
# --------------------------------------------------------------------------- #


def _rebalance_schema(account: Account) -> dict[str, Any]:
    """The approval schema for one account: one weight per sleeve, plus approval.

    Built by hand rather than from a model because MCP elicitation content is
    FLAT - `ElicitResult.content` allows only primitives, so a nested
    `allocation` object cannot come back over the wire. One property per sleeve
    also means the generated-form fallback still works on a host with no MCP
    Apps support.

    The `x-` keys carry what the app needs to render this account. They sit at
    the schema root and are passed to the host verbatim, which is how an app gets
    its context without a second round trip.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            **{
                s.key: {
                    "type": "number",
                    "title": s.label,
                    "minimum": 0,
                    "maximum": 100,
                    "description": f"{s.label} weight, percent (policy {s.target:.1f}%).",
                }
                for s in account.sleeves
            },
            "approved": {
                "type": "boolean",
                "title": "Approved",
                "description": "True once the advisor has approved the trades.",
            },
        },
        "required": [s.key for s in account.sleeves] + ["approved"],
    }
    return attach_context(
        schema,
        "approved",
        {
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
            "portfolio_value": account.value,
            "tax_rate": account.tax_rate,
        },
    )


@mcp.tool(app=AppConfig(resource_uri=REBALANCE_URI, prefers_border=False))
def propose_rebalance(
    account_id: Annotated[str, Field(description="The account to rebalance.")],
    ctx: Context,
) -> dict[str, Any] | InputRequiredResult:
    """Propose a rebalance and let the advisor set the target allocation.

    This tool renders its own UI: a host that supports MCP Apps shows allocation
    sliders for every sleeve, constrained to total 100%, with drift from policy
    and estimated tax drag updating as they move. Call it with only the account
    id. Do NOT propose weights yourself, ask which sleeves to change, or describe
    the trades first: the advisor sets them in the app and the resulting trade
    set comes back as the result.
    """
    account = _account(account_id)
    if account is None:
        return _unknown(account_id)

    answer = answer_for(ctx, "rebalance")
    if answer is None:
        return ask(
            "rebalance",
            f"{account.household} ({account.id}), ${account.value:,.0f}. "
            "Set the target allocation.",
            _rebalance_schema(account),
        )

    if answer.action != "accept":
        return {"status": "not_rebalanced", "reason": f"The advisor {answer.action}ed."}

    content = answer.content or {}
    allocation = {
        s.key: float(content.get(s.key, s.weight))
        for s in account.sleeves
        if isinstance(content.get(s.key, s.weight), (int, float))
    }
    total = sum(allocation.values())
    if abs(total - 100.0) > 0.5:
        return {"error": f"Allocation totals {total:.1f}%, not 100%. Nothing was submitted."}

    by_key = {s.key: s for s in account.sleeves}
    trades, tax = [], 0.0
    for key, weight in allocation.items():
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
            sum(abs(allocation.get(s.key, s.weight) - s.target) for s in account.sleeves), 1
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


# Roughly 2k tokens of base64. Above this the model is being asked to copy more
# than it reliably can, and the picture is not worth the context.
MAX_INLINE_CHARS = int(os.getenv("MERIDIAN_MAX_INLINE", "8000"))

# Below this a provider rejects the image outright and the 400 kills the run.
_MIN_IMAGE_EDGE = 16


def _png_bytes(data_uri: str) -> bytes | None:
    """The PNG bytes out of a data URI, or None if it is not one."""
    _, _, payload = (data_uri or "").partition("base64,")
    if not payload:
        return None
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None


def _renderable(png: bytes) -> bool:
    """Whether a provider will accept this PNG, judged from its IHDR."""
    if len(png) < 24 or png[12:16] != b"IHDR":
        return False
    return (
        int.from_bytes(png[16:20], "big") >= _MIN_IMAGE_EDGE
        and int.from_bytes(png[20:24], "big") >= _MIN_IMAGE_EDGE
    )


@mcp.tool(app=AppConfig(resource_uri=SIGNATURE_URI, prefers_border=False))
def sign_document(
    account_id: Annotated[str, Field(description="The account the document belongs to.")],
    document: Annotated[str, Field(description="What is being signed, e.g. 'IPS amendment'.")],
    ctx: Context,
) -> dict[str, Any] | InputRequiredResult:
    """Capture a client's wet signature on an advisory document.

    This tool renders its own UI: a host that supports MCP Apps shows a signature
    pad. Never ask the client to type a signature or supply `signed_by` yourself.

    The result carries the signature as an IMAGE you can look at, and as
    `signature_data_uri`, a complete `data:image/png;base64,...` value of a few
    KB. To put it in a document, copy that value verbatim into
    `<img src="{signature_data_uri}">`. Copy every character: do not truncate it,
    do not abbreviate it with an ellipsis, and do not invent one.
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
    png = _png_bytes(capture.signature)
    _SIGNED[f"{account.id}:{document}"] = {"signed_by": capture.signed_by, "png": png}

    summary: dict[str, Any] = {
        "status": "signed",
        "account_id": account.id,
        "document": document,
        "signed_by": capture.signed_by,
        "signed_at": capture.signed_at,
        "reference": f"MW-DOC-{abs(hash((account.id, document))) % 100000:05d}",
        "signature_data_uri": capture.signature,
    }
    if len(capture.signature) > MAX_INLINE_CHARS:
        summary["signature_data_uri"] = None
        summary["note"] = f"Signature is {len(capture.signature) // 1024}KB, too large to inline."

    content: list[Any] = [json.dumps(summary)]
    if png and _renderable(png):
        content.append(Image(data=png, format="png").to_image_content())
    return ToolResult(content=content, structured_content=summary)


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
