"""Fieldlink Logistics — the demo MCP server the assistant connects to.

A small FastMCP server standing in for a customer's own field-operations system,
written against the **modern (stateless) MCP spec** so it exercises everything
`langchain.mcp` gained with it:

  * **Stateless HTTP.** No session is opened, nothing is pinned to one process,
    so the ngrok tunnel (or a redeploy behind it) can drop and reconnect without
    killing an in-flight conversation.
  * **Cacheable tool list.** `cache_ttl` is the server's own freshness hint, so a
    client with a cache serves `tools/list` from it instead of a round trip per
    run. Ours does (see `dashboard_agent/mcp_servers.py`).
  * **Elicitation.** `schedule_delivery` and `collect_signature` stop mid-call to
    ask the caller something. On the modern spec that is a retry-able round, not
    a held-open socket, which is what lets the agent surface it as a LangGraph
    interrupt and resume later.
  * **An MCP App.** `collect_signature` binds a `ui://` HTML resource, so a host
    that understands the Apps extension renders a real signature pad instead of
    a text field for a data URI.

Run it with `python -m mcp_demo_server` (see `__main__.py`), or expose it with
`./scripts/run_mcp_server.sh --tunnel`.

Deliberately NOT part of the deployment: the hatch wheel packages only
`dashboard_agent`, so nothing here ships to LangGraph Platform. It is the
*other* side of the connection.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from fastmcp.apps import UI_MIME_TYPE, AppConfig
from mcp.types import (
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    InputRequiredResult,
)
from pydantic import BaseModel, Field

SIGNATURE_URI = "ui://fieldlink/signature.html"
"""The MCP App resource `collect_signature` renders (MCP Apps: `_meta.ui.resourceUri`)."""

_APP_HTML = Path(__file__).with_name("signature_app.html")

# How long a client may serve `tools/list` from its cache before re-asking. Short
# enough that adding a tool shows up in the next run or two, long enough that a
# busy thread is not re-discovering four tools it already knows. SEP-2549.
CACHE_TTL_SECONDS = int(os.getenv("FIELDLINK_CACHE_TTL", "300"))

mcp = FastMCP(
    "Fieldlink Logistics",
    instructions=(
        "Fieldlink is the field-operations system of record: shipments, delivery "
        "scheduling and proof of delivery. Look shipments up here rather than "
        "guessing at tracking numbers or delivery dates."
    ),
    version="1.0.0",
    # `public` because the catalogue is identical for every caller — there is no
    # per-user tool visibility to leak between principals.
    cache_ttl=CACHE_TTL_SECONDS,
    cache_scope="public",
)


# --------------------------------------------------------------------------- #
# The pretend system of record.                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Shipment:
    """One consignment in the fake warehouse."""

    tracking_id: str
    destination: str
    status: str
    pallets: int
    contents: str
    eta_days: int


_SHIPMENTS: tuple[Shipment, ...] = (
    Shipment("FL-4417", "Nairobi, KE", "in_transit", 12, "Water purification units", 2),
    Shipment("FL-4418", "Kisumu, KE", "customs_hold", 4, "Cold-chain vaccine carriers", 6),
    Shipment("FL-4501", "Kampala, UG", "out_for_delivery", 9, "Emergency shelter kits", 0),
    Shipment("FL-4502", "Goma, CD", "in_transit", 21, "Therapeutic food, RUTF", 4),
    Shipment("FL-4390", "Mombasa, KE", "delivered", 7, "Generator spares", -3),
)

_BY_ID = {s.tracking_id: s for s in _SHIPMENTS}

# Proof-of-delivery records written by `collect_signature`. Module-level rather
# than session state on purpose: the server is stateless, so there is no session
# to hang it off, and a demo only ever runs one process.
_DELIVERIES: dict[str, dict[str, Any]] = {}


def _as_dict(shipment: Shipment) -> dict[str, Any]:
    """One shipment as the model should see it, with the ETA already resolved."""
    eta = date.today() + timedelta(days=shipment.eta_days)
    return {
        "tracking_id": shipment.tracking_id,
        "destination": shipment.destination,
        "status": shipment.status,
        "pallets": shipment.pallets,
        "contents": shipment.contents,
        "eta": eta.isoformat(),
        "signed_for": shipment.tracking_id in _DELIVERIES,
    }


# --------------------------------------------------------------------------- #
# Plain tools.                                                                 #
# --------------------------------------------------------------------------- #


@mcp.tool(annotations={"readOnlyHint": True})
def find_shipments(
    status: Annotated[
        Literal["any", "in_transit", "customs_hold", "out_for_delivery", "delivered"],
        Field(description="Narrow to one status, or 'any' for the whole book."),
    ] = "any",
) -> dict[str, Any]:
    """List consignments currently tracked by Fieldlink, newest ETA first.

    Use this to find a tracking id before scheduling a delivery or collecting a
    signature, and to answer questions about what is moving and where.
    """
    rows = [s for s in _SHIPMENTS if status == "any" or s.status == status]
    rows.sort(key=lambda s: s.eta_days)
    return {"count": len(rows), "shipments": [_as_dict(s) for s in rows]}


@mcp.tool(annotations={"readOnlyHint": True})
def get_shipment(
    tracking_id: Annotated[str, Field(description="A Fieldlink tracking id, e.g. FL-4417.")],
) -> dict[str, Any]:
    """Look one consignment up by tracking id, including any proof of delivery."""
    shipment = _BY_ID.get(tracking_id.strip().upper())
    if shipment is None:
        known = ", ".join(sorted(_BY_ID))
        return {"error": f"No shipment {tracking_id!r} in Fieldlink. Known ids: {known}."}
    record = _as_dict(shipment)
    if proof := _DELIVERIES.get(shipment.tracking_id):
        record["proof_of_delivery"] = {k: v for k, v in proof.items() if k != "signature"}
    return record


# --------------------------------------------------------------------------- #
# Elicitation: the server stops mid-call and asks.                             #
# --------------------------------------------------------------------------- #
#
# Both tools below are GUARD TOOLS (SEP-2322), not `ctx.elicit()` callers. On the
# modern stateless protocol there is no open session for a server to push a
# question down, so `ctx.elicit()` fails with "elicitation via server-initiated
# requests is unavailable". Instead a round of asking is an ordinary result: the
# tool returns an `InputRequiredResult` naming what it needs, the client re-calls
# the same tool with `input_responses` attached, and `ctx.input_responses` tells
# the body which round it is in.
#
# That shape is exactly why `langchain.mcp` can surface it as a LangGraph
# `interrupt()`: a retry-able round survives the pause, an open socket would not.


def _ask(key: str, message: str, schema: type[BaseModel]) -> InputRequiredResult:
    """One round of asking, as the result of this leg of the call.

    `key` is how the answer comes back in `ctx.input_responses`, and it is also
    what the host resumes against, so it has to stay stable across rounds.
    """
    return InputRequiredResult(
        input_requests={
            key: ElicitRequest(
                params=ElicitRequestFormParams(
                    message=message,
                    requested_schema=schema.model_json_schema(),
                )
            )
        }
    )


def _answer(ctx: Context, key: str) -> ElicitResult | None:
    """The client's answer to `key`, or None on the round where nothing was asked yet."""
    responses = ctx.input_responses
    if not responses:
        return None
    answer = responses.get(key)
    return answer if isinstance(answer, ElicitResult) else None


class DeliverySlot(BaseModel):
    """What the caller must supply before a delivery can be booked."""

    delivery_date: str = Field(description="Delivery date, YYYY-MM-DD.")
    window: Literal["morning", "afternoon", "evening"] = Field(
        description="Which part of the day the site can receive the consignment."
    )
    site_contact: str = Field(description="Name and phone number of whoever signs on site.")


@mcp.tool
def schedule_delivery(
    tracking_id: Annotated[str, Field(description="The consignment to book in.")],
    ctx: Context,
) -> dict[str, Any] | InputRequiredResult:
    """Book a delivery slot for a consignment, asking the user for the details.

    Only the tracking id is needed to start: the date, time window and site
    contact are ELICITED from the user mid-call, so do not invent them and do not
    ask for them yourself first. The call pauses, the user fills them in, and the
    booked slot comes back as the result.
    """
    shipment = _BY_ID.get(tracking_id.strip().upper())
    if shipment is None:
        return {"error": f"No shipment {tracking_id!r} in Fieldlink."}
    if shipment.status == "delivered":
        return {"error": f"{shipment.tracking_id} was already delivered; nothing to schedule."}

    answer = _answer(ctx, "slot")
    if answer is None:
        # Nothing above this line does real work, which matters: the client
        # re-calls this tool from the top with the answer attached, so cheap
        # lookups repeat harmlessly where a write would repeat too.
        return _ask(
            "slot",
            f"When should {shipment.tracking_id} ({shipment.contents}) be delivered to "
            f"{shipment.destination}?",
            DeliverySlot,
        )

    if answer.action == "decline":
        return {"status": "not_scheduled", "reason": "The user declined to pick a slot."}
    if answer.action == "cancel":
        return {"status": "cancelled", "reason": "The user cancelled the booking."}

    slot = DeliverySlot.model_validate(answer.content or {})
    return {
        "status": "scheduled",
        "tracking_id": shipment.tracking_id,
        "destination": shipment.destination,
        "delivery_date": slot.delivery_date,
        "window": slot.window,
        "site_contact": slot.site_contact,
        "booked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


# --------------------------------------------------------------------------- #
# MCP App: the same pause, rendered by HTML the server ships.                  #
# --------------------------------------------------------------------------- #


class SignatureCapture(BaseModel):
    """The proof-of-delivery payload the signature pad posts back."""

    signature: str = Field(description="The drawn signature as a PNG data URI.")
    signed_by: str = Field(description="Printed name of the person receiving the consignment.")
    signed_at: str = Field(description="ISO-8601 timestamp of when it was signed.")


@mcp.tool(app=AppConfig(resource_uri=SIGNATURE_URI, prefers_border=False))
def collect_signature(
    tracking_id: Annotated[str, Field(description="The consignment being handed over.")],
    ctx: Context,
) -> dict[str, Any] | InputRequiredResult:
    """Capture a recipient's handwritten signature as proof of delivery.

    This tool renders its own UI: a host that supports MCP Apps shows the
    signature pad at `ui://fieldlink/signature.html` while the call is paused.
    Call it with only the tracking id. Never ask the user to type a signature,
    describe one, or supply `signed_by` yourself: the pad collects all of it and
    the signed record comes back as the result.
    """
    shipment = _BY_ID.get(tracking_id.strip().upper())
    if shipment is None:
        return {"error": f"No shipment {tracking_id!r} in Fieldlink."}

    answer = _answer(ctx, "signature")
    if answer is None:
        return _ask(
            "signature",
            f"Signature for {shipment.tracking_id}, {shipment.pallets} pallets of "
            f"{shipment.contents} at {shipment.destination}.",
            SignatureCapture,
        )

    if answer.action != "accept":
        return {
            "status": "unsigned",
            "tracking_id": shipment.tracking_id,
            "reason": f"The recipient {answer.action}ed the signature request.",
        }

    capture = SignatureCapture.model_validate(answer.content or {})
    record = {
        "tracking_id": shipment.tracking_id,
        "signed_by": capture.signed_by,
        "signed_at": capture.signed_at,
        "destination": shipment.destination,
        "pallets": shipment.pallets,
        # Kept out of everything the model reads: a base64 PNG is thousands of
        # useless tokens. `get_shipment` strips it too.
        "signature": capture.signature,
    }
    _DELIVERIES[shipment.tracking_id] = record

    return {
        "status": "signed",
        "tracking_id": shipment.tracking_id,
        "signed_by": capture.signed_by,
        "signed_at": capture.signed_at,
        "pod_reference": f"POD-{shipment.tracking_id}-{capture.signed_at[:10]}",
        "note": "Signature image stored against the consignment.",
    }


@mcp.resource(SIGNATURE_URI, mime_type=UI_MIME_TYPE, name="Signature pad")
def signature_app() -> str:
    """Serve the signature pad's HTML to a host that supports MCP Apps."""
    return _APP_HTML.read_text(encoding="utf-8")
