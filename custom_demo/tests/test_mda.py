"""The agent on Managed Deep Agents: tokens, sandbox naming, Hub routing, seeding, factory."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import jwt
import pytest
from langgraph.prebuilt.tool_node import ToolCallRequest
from starlette.applications import Starlette
from starlette.testclient import TestClient

import custom_demo.runtime.seed as seed
from custom_demo import agent_auth
from custom_demo.agent_auth import AUDIENCE, JWKS_PATH, AgentAuthNotConfiguredError
from custom_demo.auth import authenticate
from custom_demo.core.ctx import Context
from custom_demo.resources.mda_sandbox import thread_sandbox_name
from custom_demo.runtime.hub_routes import HubRoutes
from custom_demo.web.spa import SESSION_COOKIE, session_value, spa_routes
from custom_demo.webapp import app

_REPO = Path(__file__).resolve().parents[2]
_ISSUER = "https://lsd.example"


@pytest.fixture(scope="module")
def private_key() -> str:
    """One RSA key for the module: generating 2048-bit keys per test is slow."""
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415 - test-only
    from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: PLC0415 - test-only

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return pem.replace("\n", "\\n")  # as scripts/agent_auth_key.py writes it


@pytest.fixture
def issuer_env(monkeypatch, private_key):
    monkeypatch.setenv("AGENT_JWT_PRIVATE_KEY", private_key)
    monkeypatch.setenv("AGENT_JWT_ISSUER", _ISSUER)
    monkeypatch.setenv("AGENT_URL", "https://mda.example")


# --- tokens ---


def test_a_minted_token_verifies_against_the_published_jwks(issuer_env):
    token, expires = agent_auth.mint_token()
    key = jwt.PyJWKSet.from_dict(agent_auth.jwks()).keys[0]
    claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=AUDIENCE, issuer=_ISSUER)
    assert claims["sub"] == "demo"
    assert claims["exp"] == expires
    assert jwt.get_unverified_header(token)["kid"] == key.key_id


def test_no_signing_key_refuses_rather_than_minting(monkeypatch):
    monkeypatch.delenv("AGENT_JWT_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("AGENT_JWT_ISSUER", _ISSUER)
    with pytest.raises(AgentAuthNotConfiguredError):
        agent_auth.mint_token()


def test_the_jwks_is_public_but_the_token_route_is_not(monkeypatch, issuer_env):
    monkeypatch.setenv("APP_SHARED_SECRET", "s3cret")
    assert asyncio.run(authenticate(path=JWKS_PATH)) == {"identity": "anonymous"}
    with pytest.raises(Exception, match="401|token"):
        asyncio.run(authenticate(path="/agent-auth/token"))


def test_the_token_route_answers_with_the_agent_url(issuer_env):
    response = TestClient(app).post("/agent-auth/token")
    assert response.status_code == 200
    body = response.json()
    assert body["url"] == "https://mda.example"
    assert jwt.decode(body["token"], options={"verify_signature": False})["aud"] == AUDIENCE


def test_without_an_agent_url_the_token_route_says_the_agent_is_here(monkeypatch):
    monkeypatch.delenv("AGENT_URL", raising=False)
    assert TestClient(app).post("/agent-auth/token").status_code == 404


def test_config_js_hands_the_spa_the_agent_connection(monkeypatch, tmp_path, issuer_env):
    (tmp_path / "index.html").write_text("<!doctype html>")
    monkeypatch.setenv("SPA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_SHARED_SECRET", "s3cret")
    client = TestClient(Starlette(routes=spa_routes()), base_url="https://demo.test")
    client.cookies.set(SESSION_COOKIE, session_value("s3cret"), path="/ui")
    config = client.get("/ui/config.js").text
    assert '"agent": {"url": "https://mda.example"' in config


# --- the thread sandbox's name ---


def test_the_thread_sandbox_name_follows_mdas_rule(monkeypatch):
    """Pins the private naming function `resources/mda_sandbox.py` depends on."""
    monkeypatch.delenv("AGENT_DEPLOYMENT_NAME", raising=False)
    digest = hashlib.sha256(b"thread:t-123").hexdigest()[:12]
    assert thread_sandbox_name("t-123") == f"dashboard-agent--{digest}"


# --- Hub routing ---


def _request(name: str, args: dict, **ctx) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": "call-1", "type": "tool_call"},
        tool=None,
        state={},
        runtime=cast("Any", SimpleNamespace(context=Context(**ctx))),
    )


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("read_file", {"file_path": "/skills/forecast/SKILL.md"}),
        ("ls", {"path": "/skills"}),
        ("write_file", {"file_path": "/workspace/artifacts/brief.md", "content": "x"}),
    ],
)
def test_file_calls_on_hub_mounts_go_to_the_hub(name, args):
    routed = HubRoutes()._route(_request(name, args, skills_repo="s", docs_repo="d"))
    assert routed.tool is not None
    assert routed.tool.name == name


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("read_file", {"file_path": "/workspace/data/sales.csv"}),
        ("execute", {"command": "cat /skills/x/SKILL.md"}),
        ("write_file", {"file_path": "/workspace/artifacts/brief.md", "content": "x"}),
    ],
)
def test_everything_else_stays_on_the_platform_backend(name, args):
    # No docs_repo here, so the artifacts directory is the sandbox's own.
    routed = HubRoutes()._route(_request(name, args, skills_repo="s"))
    assert routed.tool is None


def test_a_hub_only_assistant_sends_every_file_call_to_the_hub():
    routed = HubRoutes()._route(_request("ls", {"path": "/"}, agent_repo="legacy"))
    assert routed.tool is not None


# --- seeding the thread's sandbox ---


class _FakeBox:
    def __init__(self, seeded: bool = False):
        self.seeded = seeded
        self.commands: list[str] = []

    async def adownload_files(self, paths):
        error = None if self.seeded else "file_not_found"
        return [SimpleNamespace(path=paths[0], error=error, content=b"")]

    async def aexecute(self, command):
        self.commands.append(command)
        return SimpleNamespace(output="seeded", exit_code=0)

    async def awrite(self, path, content):
        self.seeded = True
        return SimpleNamespace(error=None)


_SEED = [{"name": "sales.csv", "kind": "csv", "columns": ["a"], "rows": [["1"]]}]


def _seed_runtime(box):
    return SimpleNamespace(backend=box, context=Context(sandbox_seed=_SEED))


def test_a_new_thread_is_seeded_once(monkeypatch):
    monkeypatch.setattr(seed, "_thread_id", lambda: "t-seed")
    seed._seeded_threads.discard("t-seed")
    box = _FakeBox()
    middleware = seed.SeedThreadSandbox()
    asyncio.run(middleware.abefore_agent({}, _seed_runtime(box)))
    asyncio.run(middleware.abefore_agent({}, _seed_runtime(box)))
    assert len(box.commands) == 1
    assert "sales.csv" in box.commands[0]


def test_a_box_that_already_has_the_marker_is_not_reseeded(monkeypatch):
    monkeypatch.setattr(seed, "_thread_id", lambda: "t-warm")
    seed._seeded_threads.discard("t-warm")
    box = _FakeBox(seeded=True)
    asyncio.run(seed.SeedThreadSandbox().abefore_agent({}, _seed_runtime(box)))
    assert box.commands == []


def test_no_sandbox_means_nothing_to_seed():
    runtime = SimpleNamespace(backend=None, context=Context())
    assert asyncio.run(seed.SeedThreadSandbox().abefore_agent({}, runtime)) is None


# --- the agent factory ---


def _factory():
    spec = importlib.util.spec_from_file_location("mda_agent_entry", _REPO / "agent.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.agent


def _definition(factory, **ctx):
    runtime = SimpleNamespace(execution_runtime=SimpleNamespace(context=ctx))
    return factory(runtime).config


def test_the_factory_keeps_the_declared_sandbox_unless_it_must_not(monkeypatch):
    factory = _factory()
    monkeypatch.setenv("SANDBOX_ENABLED", "1")
    assert "sandbox" not in _definition(factory, skills_repo="s")
    assert _definition(factory, agent_repo="legacy")["sandbox"] is None
    monkeypatch.setenv("SANDBOX_ENABLED", "0")
    assert _definition(factory, skills_repo="s")["sandbox"] is None


# --- the Hub-only backend under MDA ---


def test_a_managed_run_without_mounts_lists_no_skills_rather_than_reading_state():
    """`HubMounts` must not fall back to a StateBackend: MDA's graph has no `files` channel."""
    from custom_demo.runtime.backends import UnmountedBackend, _resolve_backends  # noqa: PLC0415

    default, routes = _resolve_backends(SimpleNamespace(context=Context()), acquire_vm=False)
    assert isinstance(default, UnmountedBackend)
    assert routes == {}
    assert default.ls("/skills/").entries == []


# --- assistant writes relayed as the workspace ---


class _FakeAssistants:
    def __init__(self):
        self.calls: list[tuple] = []

    async def create(self, **body):
        self.calls.append(("create", body))
        return {"assistant_id": "a-1", **body}

    async def delete(self, assistant_id):
        self.calls.append(("delete", assistant_id))


def test_assistant_writes_are_relayed_with_only_the_writable_fields(monkeypatch, issuer_env):
    import custom_demo.web.agent_assistants as relay  # noqa: PLC0415

    assistants = _FakeAssistants()
    monkeypatch.setattr(relay, "agent_admin_client", lambda: SimpleNamespace(assistants=assistants))
    client = TestClient(app)
    created = client.post(
        "/agent-assistants",
        json={"graph_id": "dashboard-agent", "name": "Acme", "context": {}, "owner": "x"},
    )
    assert created.status_code == 200
    assert assistants.calls[0] == (
        "create",
        {"graph_id": "dashboard-agent", "name": "Acme", "context": {}},
    )
    assert client.delete("/agent-assistants/a-1").status_code == 204


def test_without_an_agent_url_the_relay_points_back_at_this_deployment(monkeypatch):
    monkeypatch.delenv("AGENT_URL", raising=False)
    assert TestClient(app).post("/agent-assistants", json={}).status_code == 404
