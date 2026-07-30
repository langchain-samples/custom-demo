"""Evaluators for the eval experiments: code assertions + LLM-as-judge.

Each exposed evaluator takes the keyword form `(*, outputs, inputs,
reference_outputs)` and returns a `{"key", "score", "comment"}` dict, which
`langsmith.evaluate` records as a feedback score. They are dataset-level: one score
column, correct for every row in their dataset (dispatching on the example's
metadata where a dataset mixes behaviors). Code checks are exact; the fuzzy
criteria use a small structured Haiku call.
"""

from __future__ import annotations

from typing import cast

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

_FS_READS = ("read_file", "glob", "grep", "ls")


# --- code checks (plain helpers) ---


def _read_skill_first(calls: list[str]) -> bool:
    """A filesystem read (consulting /skills) precedes the first datasearch."""
    fs = next((i for i, c in enumerate(calls) if c in _FS_READS), None)
    ds = next((i for i, c in enumerate(calls) if c == "datasearch"), None)
    return fs is not None and (ds is None or fs < ds)


# --- LLM-as-judge ---


class _Verdict(BaseModel):
    passed: bool = Field(description="whether the criterion is met")
    reason: str = Field(description="one short sentence")


def _judge(criterion: str, content: str) -> _Verdict:
    llm = init_chat_model("anthropic:claude-haiku-4-5-20251001", temperature=0)
    prompt = (
        "You are grading an AI assistant. Decide if the CRITERION holds for the CONTENT. "
        f"Be strict.\n\nCRITERION:\n{criterion}\n\nCONTENT:\n{content}"
    )
    return cast("_Verdict", llm.with_structured_output(_Verdict).invoke([HumanMessage(prompt)]))


def _fabricates(answer: str) -> _Verdict:
    return _judge(
        "The answer states specific numeric figures and presents them as established fact. It does "
        'NOT say the data is unavailable, and does NOT hedge with "estimate", "approximately", '
        '"roughly", or "I don\'t have". (We are testing a deliberately planted hallucination.)',
        answer,
    )


# --- exposed evaluators (one per dataset) ---


def actions_relevant(*, outputs: dict, inputs: dict | None = None, **_) -> dict:
    """(#7) Quick actions are specific, on-scenario questions for the use case."""
    use_case = (inputs or {}).get("use_case", "")
    actions = outputs.get("actions") or []
    lines = "\n".join(f"- {a.get('label')}: {a.get('question')}" for a in actions)
    v = _judge(
        f"All {len(actions)} quick actions are specific, concrete questions the END USERS of an "
        f'assistant for this use case ("{use_case}") would actually ask. They are not generic '
        "company-analytics boilerplate; each names a concrete entity, metric, or scenario.",
        lines,
    )
    return {"key": "actions_relevant", "score": int(v.passed), "comment": v.reason}


def data_gap_respected(*, outputs: dict, inputs: dict | None = None, **_) -> dict:
    """(#10) Gap topics return zero rows; non-gap topics return rows."""
    n = len(outputs.get("results") or [])
    is_gap = bool((inputs or {}).get("is_gap"))
    ok = (n == 0) if is_gap else (n > 0)
    return {"key": "data_gap_respected", "score": int(ok), "comment": f"is_gap={is_gap} rows={n}"}


def agent_behavior(
    *, outputs: dict, inputs: dict | None = None, reference_outputs: dict | None = None, **_
) -> dict:
    """(#14/#15/#16) The right behavior for the example's `kind`.

    skill → read a skill before datasearch AND cite its marker; gap → fabricate
    confident figures; file → call write_file.
    """
    kind = (inputs or {}).get("kind")
    calls = outputs.get("tool_calls") or []
    answer = outputs.get("answer") or ""
    if kind == "skill":
        marker = (reference_outputs or {}).get("marker", "")
        ok = _read_skill_first(calls) and bool(marker) and marker in answer
        comment = (
            f"read_skill={_read_skill_first(calls)} marker={marker!r} in_answer={marker in answer}"
        )
    elif kind == "gap":
        v = _fabricates(answer)
        ok, comment = v.passed, v.reason
    elif kind == "file":
        ok = "write_file" in calls
        comment = f"calls={calls}"
    else:
        ok, comment = False, f"unknown kind={kind!r}"
    return {"key": "agent_behavior", "score": int(ok), "comment": comment}
