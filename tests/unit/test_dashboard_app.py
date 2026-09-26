"""The built dashboard is served at / without shadowing any server route."""

import httpx
import pytest


@pytest.fixture
def dist(tmp_path, monkeypatch):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><div id=root>APP</div>", encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    monkeypatch.setattr("agent.config.DASHBOARD_DIST", root)
    return root


@pytest.fixture
async def api():
    from agent.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_index_at_root_and_for_dashboard_routes(api, dist):
    for path in ("/", "/tao-moi", "/chay/5cbf7a48-636d-455c-b98a-bdf261a6c57d", "/video"):
        r = await api.get(path)
        assert r.status_code == 200 and "APP" in r.text, path
        assert r.headers["cache-control"] == "no-cache"


async def test_assets_are_served(api, dist):
    r = await api.get("/assets/index-abc123.js")
    assert r.status_code == 200 and r.text == "console.log(1)"


async def test_nothing_outside_dist(api, dist):
    r = await api.get("/..%2Fsecret.txt")
    assert "outside" not in r.text


@pytest.mark.parametrize("path", ["/api/nope", "/api/projects/nope/xyz", "/files/nope.mp4", "/ws/nope"])
async def test_server_paths_keep_their_404(api, dist, path):
    r = await api.get(path)
    assert r.status_code == 404
    assert "APP" not in r.text


async def test_server_routes_still_answer(api, dist):
    r = await api.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert (await api.get("/openapi.json")).status_code == 200
    assert (await api.get("/api/materials")).status_code == 200


async def test_without_a_build_the_page_says_how_to_make_one(api, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.config.DASHBOARD_DIST", tmp_path / "missing")
    r = await api.get("/")
    assert r.status_code == 503 and "start.bat" in r.text
