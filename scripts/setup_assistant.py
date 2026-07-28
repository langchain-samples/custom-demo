"""Create a customer assistant (branding + trace routing + optional demo bug).

Deterministic write-half of the `/setup-assistant` skill: pushes prompt(s) to a
workspace's Prompt Hub (optional) and creates the assistant via the assistants
API. The skill collects/confirms the inputs (workspace, customer, branding,
prompt choices); this script just does the LangSmith writes reliably.

Reads a JSON payload from a file arg or stdin:

    python scripts/setup_assistant.py payload.json
    echo '{...}' | python scripts/setup_assistant.py

Payload keys:
    workspace        (required) workspace id to create the assistant + prompts in
    customer         (required) becomes the assistant `name` (and default project)
    owner, industry  metadata
    display_name, accent, logo, actions   branding (stored in metadata)
    dataset          "humanitarian" (default) | "synthetic"
    hallucination    bool — build the demo bug (buggy system prompt + withholding
                     data prompt) unless explicit texts are given below
    push_prompts     bool (default true) — push prompt texts to the workspace Hub;
                     if false, prompts are sent inline in the assistant context
    system_prompt_name / system_prompt_text
    data_prompt_name / data_prompt_text
    langgraph_url    (default http://127.0.0.1:2024) Agent Server to create on

Env: LS_CROSS_WORKSPACE_KEY (preferred) or LANGSMITH_API_KEY; LANGSMITH_ENDPOINT.
Prints JSON: {assistant_id, name, prompt_urls, context, metadata}.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langgraph_sdk import get_sync_client
from langsmith import Client

from dashboard_agent.config import load_env
from dashboard_agent.prompt import (
    _FALLBACK_CORE,
    DATA_FALLBACK_PROMPT,
    FALLBACK_PROMPT,
    HALLUCINATION_CLAUSE,
)

GRAPH_ID = "dashboard_agent"


def _client(workspace_id: str | None):
    load_env()
    key = os.getenv("LS_CROSS_WORKSPACE_KEY") or os.getenv("LANGSMITH_API_KEY")
    api_url = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    return Client(api_key=key, api_url=api_url, workspace_id=workspace_id or None)


def _push_prompt(ws_client, name: str, text: str) -> str:
    prompt = ChatPromptTemplate.from_messages([("system", text)])
    return ws_client.push_prompt(name, object=prompt)


def main() -> None:
    raw = Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else sys.stdin.read()
    p = json.loads(raw)

    workspace = p.get("workspace")
    customer = (p.get("customer") or "").strip()
    if not workspace or not customer:
        sys.exit("payload requires 'workspace' and 'customer'")

    load_env()
    ws_client = _client(workspace)

    # Resolve prompt texts (auto-build the demo bug when requested). The buggy
    # prompt uses the grounded CORE with the hallucination clause replacing the
    # grounding clause (never both — the contradiction suppresses the bug).
    hallu = bool(p.get("hallucination"))
    sys_text = p.get("system_prompt_text") or (
        _FALLBACK_CORE + HALLUCINATION_CLAUSE if hallu else FALLBACK_PROMPT
    )
    data_text = p.get("data_prompt_text") or (DATA_FALLBACK_PROMPT if hallu else None)
    push = p.get("push_prompts", True)

    context: dict = {"ls_workspace": workspace}
    prompt_urls: dict = {}

    if sys_text:
        name = p.get("system_prompt_name")
        if push and name:
            prompt_urls["system"] = _push_prompt(ws_client, name, sys_text)
            context["prompt_name"] = name
        else:
            context["prompt"] = sys_text  # inline override
    if data_text:
        name = p.get("data_prompt_name")
        if push and name:
            prompt_urls["data"] = _push_prompt(ws_client, name, data_text)
            context["data_prompt_name"] = name
        else:
            context["data_prompt"] = data_text
    dataset = p.get("dataset") or ("synthetic" if (data_text or hallu) else None)
    if dataset:
        context["dataset"] = dataset

    metadata = {
        "owner_name": p.get("owner", ""),
        "customer": customer,
        "industry": p.get("industry", ""),
        "display_name": p.get("display_name") or customer,
        "accent": p.get("accent", ""),
        "logo": p.get("logo", ""),
        "actions": p.get("actions", []),
    }

    lg = get_sync_client(url=p.get("langgraph_url", "http://127.0.0.1:2024"))
    a = lg.assistants.create(
        graph_id=GRAPH_ID,
        name=customer,
        context=context,
        metadata=metadata,
        if_exists="do_nothing",
    )
    print(json.dumps({
        "assistant_id": a["assistant_id"],
        "name": a.get("name"),
        "prompt_urls": prompt_urls,
        "context": context,
        "metadata": metadata,
    }, indent=2))


if __name__ == "__main__":
    main()
