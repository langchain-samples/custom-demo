"""The deployment-served SPA: which paths skip the shared-token check, and when it mounts."""

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from custom_demo.web.spa import SESSION_COOKIE, is_spa_path, session_ok, session_value, spa_routes


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


def _client(monkeypatch, tmp_path, secret="s3cret"):
    """The SPA's routes over a built-SPA stand-in, behind a test client."""
    (tmp_path / "index.html").write_text("<!doctype html><title>app</title>")
    monkeypatch.setenv("SPA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_SHARED_SECRET", secret)
    return TestClient(Starlette(routes=spa_routes()), base_url="https://demo.test")


def test_without_a_session_every_spa_path_goes_to_the_login_page(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    for path in ("/ui/", "/ui/config.js", "/ui/index.html", "/ui/x/login"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/ui/login"

    page = client.get("/ui/login")
    assert page.status_code == 200
    assert 'type="password"' in page.text
    assert "s3cret" not in page.text


def test_a_wrong_password_sets_no_session(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post("/ui/login", data={"password": "guess"}, follow_redirects=False)
    assert response.status_code == 401
    assert SESSION_COOKIE not in response.cookies
    assert client.get("/ui/config.js", follow_redirects=False).status_code == 303


def test_the_right_password_signs_in_until_the_secret_changes(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post("/ui/login", data={"password": "s3cret"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "Path=/ui" in cookie

    config = client.get("/ui/config.js")
    assert config.status_code == 200
    assert '"apiKey": "s3cret"' in config.text
    assert client.get("/ui/").status_code == 200

    monkeypatch.setenv("APP_SHARED_SECRET", "rotated")
    assert client.get("/ui/config.js", follow_redirects=False).status_code == 303


def test_the_session_is_an_hmac_of_the_secret_not_the_secret():
    assert session_value("s3cret") != "s3cret"
    assert session_ok(session_value("s3cret"), "s3cret")
    assert not session_ok(session_value("s3cret"), "other")
    assert not session_ok(None, "s3cret")
    assert session_ok(None, "")
