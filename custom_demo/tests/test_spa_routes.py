"""The deployment-served SPA: which paths skip the shared-token check, and when it mounts."""

import pytest

from custom_demo.web.spa import is_spa_path, spa_routes


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
