"""A resolved demo scenario and its LangSmith resource handles.

These records describe preparation, not publication: the browser creates the stored
assistant after preparation returns. Nested action, skill and seed dictionaries retain
provider extensions and caller-supplied fields; they are not a second validation schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass
class LsArtifacts:
    """Named cleanup targets, not proof of exclusive ownership or successful creation.

    Project and annotation-queue names may precede their creation. Customer-derived
    repositories and content-addressed datasets may be shared. Empty handles are skipped
    by cleanup, which must also accept omitted and null fields from stored manifests.
    """

    workspace: str | None = None
    project: str | None = ""
    agent_repo: str | None = ""
    skills_repo: str | None = ""
    skills: list[str] | None = field(default_factory=list)
    eval_dataset: str | None = ""
    eval_rule_id: str | None = ""
    eval_evaluator_id: str | None = ""
    eval_judge_prompt: str | None = ""
    annotation_queue: str | None = ""

    @classmethod
    def from_mapping(cls, body: Mapping[str, Any]) -> LsArtifacts:
        """Read recognized handles without coercing or rejecting legacy field values."""
        return cls(**{item.name: body.get(item.name) for item in fields(cls)})

    def to_dict(self) -> dict[str, Any]:
        """Serialize the ten-field assistant metadata contract."""
        return asdict(self)

    def tagging_targets(self) -> dict[str, Any]:
        """Project the resource handles supported by application tagging."""
        return {
            "workspace": self.workspace,
            "project": self.project,
            "dataset": self.eval_dataset,
            "prompts": (self.eval_judge_prompt,) if self.eval_judge_prompt else (),
            "agents": tuple(name for name in (self.agent_repo, self.skills_repo) if name),
            "evaluator_id": self.eval_evaluator_id,
        }


@dataclass(frozen=True)
class DemoPlan:
    """Resolved scenario shared by provisioning, evaluation and presenter output.

    Policy resolution happens before resource creation. The wire-shaped collections
    preserve their existing fields and are shared read-only by the downstream consumers.
    """

    workspace: str
    customer: str
    owner: str
    industry: str
    use_case: str
    failure_mode: str
    display_name: str
    slug: str
    sandbox_key: str
    enabled_tools: list[str]
    seed_files: list[dict]
    skills: list[dict]
    actions: list[dict]
    planted_gap: str
    branding: dict
    push_prompts: bool
    demo_traffic: bool

    @property
    def agent_repo(self) -> str:
        """The customer-derived prompt repository name used during preparation."""
        return f"{self.slug}-agent"

    @property
    def dashboard_mode(self) -> str:
        """Whether prompt instructions refer to the curated dashboard skill."""
        return "skill" if "push_widget" in self.enabled_tools else "inline"

    def runtime_context(self) -> dict:
        """Build the configuration supplied to both prewarming and agent runs."""
        context = {
            "ls_workspace": self.workspace,
            "customer": self.customer,
            "ls_project": self.customer,
            "enabled_tools": self.enabled_tools,
            "sandbox_seed": self.seed_files,
            "sandbox_key": self.sandbox_key,
        }
        if self.industry:
            context["industry"] = self.industry

        return context

    def metadata(self, artifacts: LsArtifacts) -> dict:
        """Build display configuration and cleanup targets for the stored assistant."""
        return {
            "owner_name": self.owner,
            "customer": self.customer,
            "industry": self.industry,
            "display_name": self.display_name,
            **self.branding,
            "actions": self.actions,
            "failure_mode": self.failure_mode,
            "voice": {},
            "ls_artifacts": artifacts.to_dict(),
        }
