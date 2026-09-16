"""Drive the software-factory demo end to end, as the people who would really use it.

A marketing colleague raises a request in one sentence, answers the intake agent's
questions, and a product owner approves the brief. The product owner then edits the spec
in the browser the way the document editor does, quotes a passage back at the agent, and
asks for the acceptance criteria. After each step this asserts on the DOCUMENTS and their
REVISIONS, because that is the product: a chat log that looks plausible while the request
folder stays empty is the failure this script exists to catch.

Needs a running Agent Server (`./run.sh`, or `langgraph dev`) and the seeded assistant
(`scripts/seed_sdlc_demo.py`). Real model calls, so it takes a few minutes and costs
tokens.

    uv run python scripts/e2e_sdlc.py
    uv run python scripts/e2e_sdlc.py --keep   # leave the documents repo in place
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from langgraph_sdk import get_client
from langsmith.utils import LangSmithNotFoundError

from custom_demo.config import load_env, scoped_client
from custom_demo.resources import docs as D

ASSISTANT_NAME = "Mary Kay Software Factory"
DOCS_REPO = "mary-kay-factory-docs"
GRAPH_ID = "dashboard_agent"

# One turn can draft a whole document, and a document is a few thousand tokens.
RUN_TIMEOUT = 600

# How many exchanges the intake conversation is allowed before this gives up. Elicitation
# is meant to take five to ten minutes of a person's time, which is a handful of turns; a
# script that waited indefinitely would hide an agent that never stops asking questions.
MAX_INTAKE_TURNS = 5

# What the requester says, in order. The first line is all anyone ever arrives with.
REQUESTER = [
    "I want our consultants to be able to offer a gift with purchase during the holiday campaign.",
    (
        "The consultants have the problem. Today they hand-write a note on the order and "
        "someone in customer care applies a discount later, which goes wrong a lot. "
        "Afterwards a consultant could add the gift herself at checkout and the customer "
        "would see it straight away. It is a change to the existing consultant ordering "
        "app. Customer care and the fulfilment team are affected because they pick the "
        "gift. It has to be live before the holiday campaign starts on the first of "
        "November, and it has to work with our existing promotions engine."
    ),
    (
        "We would know it worked if customer care stops getting those manual discount "
        "requests. Anything else you need, make a sensible assumption and flag it."
    ),
    "Yes, that is right. Please write it up.",
]


def _client():
    """SDK client for the local deployment, carrying the app token when one is set."""
    return get_client(
        url=os.getenv("E2E_URL", "http://127.0.0.1:2024"),
        api_key=os.getenv("APP_SHARED_SECRET") or None,
    )


def _text(message: dict) -> str:
    """The readable text of one message, whatever content shape it arrived in."""
    content = message.get("content")
    if isinstance(content, str):
        return content

    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))

    return "\n".join(parts)


def _reply(result: dict, since: int = 0) -> str:
    """The assistant's final answer among the messages this run added.

    Bounded by `since` because the thread accumulates: scanning all of it let a run that
    paused on an interrupt, and therefore answered nothing, report the previous turn's
    reply as its own.
    """
    messages = (result.get("messages") or [])[since:]
    for message in reversed(messages):
        if message.get("type") == "ai" and not message.get("tool_calls"):
            text = _text(message).strip()
            if text:
                return text

    return ""


def _tools_used(result: dict, since: int = 0) -> list[str]:
    """Tools this run called, in order, so a claim of editing can be checked."""
    used = []
    for message in (result.get("messages") or [])[since:]:
        for call in message.get("tool_calls") or []:
            used.append(str(call.get("name") or ""))

    return used


def _interrupts(result: dict) -> list[dict]:
    """Pending human-in-the-loop interrupts in a run's result, whatever shape it used."""
    raw = result.get("__interrupt__") or []
    out = []
    for item in raw if isinstance(raw, list) else [raw]:
        value = item.get("value") if isinstance(item, dict) else None
        if isinstance(value, dict):
            out.append(value)

    return out


def _pick(options: list[str], fallback: str) -> str:
    """The answer a person would give to a multiple-choice question.

    Prefers a real option over an escape hatch, because picking "Something else" would
    leave the agent exactly as uninformed as before and the conversation would loop.
    """
    real = [o for o in options if o.strip().lower() not in {"something else", "other", "none"}]
    return (real or options or [fallback])[0]


class Failed(RuntimeError):
    """An end-to-end expectation that did not hold, named where it broke."""


def check(condition: bool, what: str) -> None:
    """Assert one expectation, reporting it either way so the log reads as a run sheet."""
    if not condition:
        raise Failed(what)

    print(f"    ok  {what}")


def documents() -> dict[str, str]:
    """Every document in the demo's repo right now, by repo-relative path."""
    listing = D.list_documents(DOCS_REPO)
    out = {}
    for entry in listing.entries:
        out[D.repo_path(entry.path)] = D.read_document(DOCS_REPO, entry.path).content

    return out


def folder_of(paths: list[str]) -> str:
    """The single request folder the demo created, failing loudly if it made several."""
    folders = sorted({path.split("/")[0] for path in paths if "/" in path})
    if len(folders) != 1:
        raise Failed(f"expected exactly one request folder, found {folders}")

    return folders[0]


async def turn(client, thread_id: str, assistant_id: str, question: str, label: str) -> dict:
    """Send one message as a person, answer any interrupt, and report what THIS run did."""
    print(f"\n  {label}: {question[:96]}{'...' if len(question) > 96 else ''}")
    state = await client.threads.get_state(thread_id)
    since = len((state.get("values") or {}).get("messages") or [])
    result = await client.runs.wait(
        thread_id,
        assistant_id,
        input={"messages": [{"role": "user", "content": question}]},
        config={"recursion_limit": 60},
    )
    if not isinstance(result, dict):
        raise Failed(f"{label}: the run returned {type(result).__name__}, not a state")

    # Answer any interrupt the way the SPA's cards do, so a paused run is a question
    # being answered rather than a turn that silently produced nothing.
    for _ in range(4):
        pending = _interrupts(result)
        if not pending:
            break

        question = pending[0]
        options = [str(o) for o in (question.get("options") or [])]
        answer = _pick(options, "Make a sensible assumption and flag it")
        print(f"    interrupt: {str(question.get('question'))[:90]} -> {answer!r}")
        result = await client.runs.wait(
            thread_id, assistant_id, command={"resume": {"answer": answer}}
        )

    tools = _tools_used(result, since)
    if tools:
        print(f"    tools: {', '.join(tools)}")

    answer = _reply(result, since)
    print(f"    agent: {answer[:220].replace(chr(10), ' ')}{'...' if len(answer) > 220 else ''}")
    # The run's OWN tools and answer, not the thread's: an assertion about what this
    # turn did must not be satisfiable by what an earlier turn did.
    return {"tools": tools, "answer": answer, "state": result}


async def run(keep: bool) -> int:
    """Walk the whole process and assert on the documents at every step."""
    load_env()
    client = _client()
    found = await client.assistants.search(graph_id=GRAPH_ID, limit=100)
    assistant = next((a for a in found if a.get("name") == ASSISTANT_NAME), None)
    if assistant is None:
        raise Failed(f"no assistant named {ASSISTANT_NAME!r}. Run scripts/seed_sdlc_demo.py first.")

    assistant_id = str(assistant["assistant_id"])
    # Start from an empty store. Left in place, the previous run's request folder is
    # correctly recognised by the agent as a request already in flight, and it offers to
    # review that one instead of raising a new one - right behaviour, wrong starting
    # point for a test that asserts on a request being raised from nothing.
    try:
        scoped_client(None).delete_agent(DOCS_REPO)
        print(f"cleared    {DOCS_REPO}")
    except LangSmithNotFoundError:
        print(f"clean      {DOCS_REPO} did not exist")

    thread = await client.threads.create()
    thread_id = str(thread["thread_id"])
    print(f"assistant {assistant_id}\nthread    {thread_id}")

    # --- Phase 1: a colleague raises a request and the agent elicits the brief ---
    print("\n=== Phase 1: intake and approval ===")
    brief_path = ""
    for index, said in enumerate(REQUESTER):
        await turn(client, thread_id, assistant_id, said, f"marketing colleague {index + 1}")
        files = documents()
        brief = next((p for p in files if p.endswith("intake-brief.md")), "")
        if brief:
            brief_path = brief
            break

        if index == 0:
            check(not files, "no document written before the questions were answered")

    if not brief_path:
        raise Failed(f"no intake brief after {len(REQUESTER)} exchanges")

    files = documents()
    folder = folder_of(list(files))
    print(f"\n  request folder: {folder}")
    brief = files[brief_path]
    check(folder.startswith("req-"), f"the folder is a request id ({folder})")
    for section in ("What is being asked for", "Why it matters", "Who is affected"):
        check(section in brief, f"the brief has a {section!r} section")

    check("consultant" in brief.lower(), "the brief uses the requester's own word, consultant")
    check("promotions engine" in brief.lower(), "the brief kept the named constraint")

    versions = D.list_versions(DOCS_REPO, brief_path)
    check(bool(versions), "the brief has a revision in Context Hub")

    # --- The product owner approves, which is the gate into product definition ---
    print("\n=== Phase 1 gate: the product owner approves ===")
    await turn(
        client,
        thread_id,
        assistant_id,
        "This is Kevin, the product owner. The brief looks right. Approved. Please draft the "
        "functional spec.",
        "product owner",
    )
    files = documents()
    spec_path = next((p for p in files if p.endswith("functional-spec.md")), "")
    check(bool(spec_path), "the functional spec was drafted after approval")
    spec = files[spec_path]
    for section in ("In scope", "Out of scope", "Acceptance criteria"):
        check(section in spec, f"the spec has an {section!r} section")

    check(
        "Assumption" in spec or "Assumed" in spec,
        "the spec marks its assumptions rather than burying them",
    )

    progress_path = next((p for p in files if p.endswith("progress.html")), "")
    check(bool(progress_path), "the request folder carries a progress diagram")
    progress = files[progress_path]
    check("mermaid" in progress, "the progress diagram renders with mermaid")
    check("class intake done" in progress, "intake is marked done now that it was approved")

    # --- The product owner edits the spec in the browser, as the editor does ---
    print("\n=== The product owner edits the spec in the browser ===")
    before = D.read_document(DOCS_REPO, spec_path)
    edited = before.content.rstrip() + (
        "\n\n## Product owner note\n\n"
        "The gift is one per order, not one per item. Confirmed with the campaign team.\n"
    )
    saved = D.write_document(
        DOCS_REPO,
        spec_path,
        edited,
        message="Confirmed the gift is one per order",
        author="Product Owner",
        base_version=before.version,
    )
    check(saved.version != before.version, "the browser save created a new revision")
    try:
        D.write_document(
            DOCS_REPO, spec_path, "clobbered", base_version=before.version, author="Someone else"
        )
        raise Failed("a save against the stale revision was accepted")
    except D.DocumentConflict:
        print("    ok  a second save against the stale revision was refused")

    versions = D.list_versions(DOCS_REPO, spec_path)
    authors = [v.author for v in versions]
    check("Product Owner" in authors, f"the history attributes the human revision ({authors})")
    check(
        len(versions) >= 2, f"the spec has both the draft and the human revision ({len(versions)})"
    )

    # --- A quoted passage, shaped exactly as the composer sends one ---
    print("\n=== The product owner quotes a passage and asks for a change ===")
    passage = "The gift is one per order, not one per item."
    quoted = (
        "Say this in the consultant's words rather than the campaign team's.\n\n"
        "[The user selected 1 passage in a document they are reading. Edit the document "
        "itself when the request is a change to it.]\n\n"
        f'From {D.ARTIFACTS_PREFIX}{spec_path}:\n"""\n{passage}\n"""'
    )
    turned = await turn(client, thread_id, assistant_id, quoted, "product owner")
    check(
        "edit_file" in turned["tools"] or "write_file" in turned["tools"],
        "the agent changed the document rather than answering in chat",
    )
    after = D.read_document(DOCS_REPO, spec_path)
    check(after.version != saved.version, "the quoted passage produced a new revision")
    check(after.content != edited, "the document text actually changed")

    # --- Phase 2 completes with the Gherkin the next phase reads ---
    print("\n=== Phase 2: acceptance criteria in Gherkin ===")
    await turn(
        client,
        thread_id,
        assistant_id,
        "The spec is good now. Please write the acceptance criteria.",
        "product manager",
    )
    files = documents()
    bdd_path = next((p for p in files if p.endswith("bdd-features.md")), "")
    check(bool(bdd_path), "the BDD features document was written")
    bdd = files[bdd_path]
    check("```gherkin" in bdd, "the scenarios are in fenced gherkin blocks")
    check("Feature:" in bdd and "Scenario" in bdd, "there is at least one feature and scenario")
    for step in ("Given", "When", "Then"):
        check(step in bdd, f"the scenarios use {step}")

    check(
        bdd.count("Scenario") >= 2,
        f"more than one scenario, so refusal cases exist too ({bdd.count('Scenario')} found)",
    )

    # --- The folder is the bundle the browser groups into one band of tabs ---
    print("\n=== The request folder ===")
    paths = sorted(documents())
    for path in paths:
        print(f"    {path}")

    check(len(paths) >= 4, f"the folder holds the whole request ({len(paths)} documents)")
    check(all(p.startswith(f"{folder}/") for p in paths), "every document is in one request folder")

    if not keep:
        scoped_client(None).delete_agent(DOCS_REPO)
        print(f"\ndeleted {DOCS_REPO}")

    print("\nEND TO END PASSED")
    return 0


def main() -> int:
    """Parse arguments and report a failure as a message rather than a traceback."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="leave the documents repo in place")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.keep))
    except Failed as exc:
        print(f"\nEND TO END FAILED: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
