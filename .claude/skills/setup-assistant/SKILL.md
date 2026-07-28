---
name: setup-assistant
description: Guided setup of a new Dashboard Agent customer assistant — asks for the LangSmith workspace and customer, auto-fetches brand logo + accent color and generates persona quick-actions (you confirm), optionally seeds a system/data prompt into that workspace's Prompt Hub (with an optional built-in hallucination demo bug), then creates the assistant with per-assistant branding + trace routing. Use when the user says "set up an assistant", "new customer assistant", "onboard a customer", or invokes /setup-assistant.
---

# Setup a customer assistant

You are configuring one **assistant** (a configuration instance of the `dashboard_agent`
graph) for a specific customer. Branding lives in the assistant's `metadata`; trace routing
and prompts live in its `context`. The SPA loads all of this when the assistant is selected.

Work through the steps **interactively** — confirm with the user before the write step.

## 0. Preflight
- Ensure the Agent Server is up: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:2024/ok`
  → if not `200`, tell the user to run `./run.sh` first (or invoke the `run` skill).
- Trace routing uses `LS_CROSS_WORKSPACE_KEY` (org-scoped, in `.env`). Confirm workspaces load:
  `curl -s http://127.0.0.1:2024/workspaces` → a non-empty `workspaces` list.

## 1. Collect the basics (ask the user)
- **Workspace**: show the names from `GET /workspaces` and ask which one. Keep its `id`.
- **Customer**: the customer/org name → becomes the assistant `name` AND the default trace project.
- **Owner**: default to the current user (Josiah Coad) unless told otherwise.
- **Industry**: one of the panel's industries (Governmental, Healthcare, Financial Services,
  Technology, Non-profit / NGO, Education, Retail, Manufacturing, Energy & Utilities,
  Logistics & Transport, Media & Entertainment, Other) — infer from the customer, confirm.

## 2. Fetch branding, then confirm
- **Logo**: WebSearch/WebFetch for the customer's logo; prefer a stable URL (e.g. their site's
  logo, a Wikipedia asset, or Logo.dev `https://img.logo.dev/<domain>?token=<pk>`).
  Fall back to an emoji if none is found.
- **Accent color**: find the brand's primary hex (from brand guidelines / logo). Fall back to
  `#0072BC`.
- **Display name**: a short human title (e.g. "Acme Health — Insights"). Default to the customer.
- **Quick actions**: generate **3** persona questions tailored to the customer + industry
  (mirror the humanitarian demo's donor / affected / technical framing but domain-appropriate),
  each `{label, question}`.
- **Show the proposed branding to the user and let them tweak before continuing.**

## 3. Prompt choices (ask the user)
- **Push a system prompt to Prompt Hub?** If yes, choose a handle (default `<slug>-system`) and it
  gets pushed to the chosen workspace's Hub; the assistant references it by `prompt_name`. If no,
  the assistant uses the deployment default (or an inline `prompt`).
- **Build in the hallucination demo?** If yes: the system prompt = grounded base + the
  hallucination override clause, and a **synthetic** data prompt that withholds a data gap — so
  the agent fabricates confident numbers over missing data (the classic demo bug). This sets
  `dataset: "synthetic"`. The helper builds these texts automatically when `hallucination: true`.
  If the customer should just work normally, leave it off.

## 4. Create it (the write step)
Build a JSON payload and run the deterministic helper (it pushes prompts + creates the assistant):

```bash
cat > /tmp/assistant.json <<'JSON'
{
  "workspace": "<workspace-id>",
  "customer": "Acme Health",
  "owner": "Josiah Coad",
  "industry": "Healthcare",
  "display_name": "Acme Health — Insights",
  "accent": "#0a7d55",
  "logo": "https://acme.example/logo.png",
  "actions": [
    {"label": "Exec: quarterly outcomes", "question": "What were Acme Health's key patient outcomes last quarter?"},
    {"label": "Ops: capacity & wait times", "question": "Show current capacity and average wait times by facility."},
    {"label": "Analyst: cost trends", "question": "How have per-patient costs trended over the last year?"}
  ],
  "hallucination": false,
  "push_prompts": true,
  "system_prompt_name": "acme-health-system",
  "data_prompt_name": "acme-health-data"
}
JSON
chat-langchain-lite/.venv/bin/python scripts/setup_assistant.py /tmp/assistant.json
```

Notes on the payload (see `scripts/setup_assistant.py` for the full schema):
- Omit `system_prompt_name`/`data_prompt_name` + set `push_prompts:false` to send prompts inline.
- Set `hallucination:true` to auto-build the buggy system prompt + withholding data prompt
  (no need to supply the texts); it also flips `dataset` to `"synthetic"`.
- Provide `system_prompt_text`/`data_prompt_text` to override the auto-built texts.

## 5. Report
Relay the helper's JSON output: the new `assistant_id`, any Prompt Hub URLs, and the routing
`context`. Tell the user to open the SPA (http://127.0.0.1:3000), pick the workspace, then select
the new assistant in the ⚙️ panel — its branding applies and traces route to that workspace /
the customer-named project. If `hallucination` was on, a question about the withheld data will
show the agent fabricating (the demo bug); removing the override clause in Prompt Hub fixes it live.
