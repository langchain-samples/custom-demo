"""The deployment-served SPA: which paths skip the shared-token check, and when it mounts."""

import base64

import pytest

from custom_demo.web.spa import basic_auth_ok, is_spa_path, spa_routes


@pytest.mark.parametrize("path", ["/", "/ui", "/ui/", "/ui/config.js", "/ui/assets/index.js"])
def test_spa_paths_are_open(path):
    assert is_spa_path(path)


@pytest.mark.parametrize("path", ["/uiX", "/uiconfig.js", "/tools", "/assistants/search", "/a2a/x"])
def test_api_paths_are_not_spa_paths(path):
    assert not is_spa_path(path)


def test_no_spa_routes_without_a_build(monkeypatch):
    monkeypatch.delenv("SPA_DIR", raising=False)
    assert spa_routes() == []


def test_a_missing_build_refuses_to_start(monkeypatch, tmp_path):
    monkeypatch.setenv("SPA_DIR", str(tmp_path / "absent"))
    with pytest.raises(RuntimeError):
        spa_routes()


def _basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


@pytest.mark.parametrize(
    ("header", "ok"),
    [
        (_basic("anyone", "s3cret"), True),
        (_basic("", "s3cret"), True),
        (_basic("s3cret", "wrong"), False),
        (None, False),
        ("Bearer s3cret", False),
        ("Basic not-base64!", False),
    ],
)
def test_basic_auth_needs_the_secret_as_password(header, ok):
    assert basic_auth_ok(header, "s3cret") is ok


def test_basic_auth_is_open_without_a_secret():
    assert basic_auth_ok(None, "")
