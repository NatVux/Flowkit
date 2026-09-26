"""GET /files (read only, inside the output directory) and POST /api/system/open-folder."""

from unittest.mock import patch

import httpx
import pytest

from agent.api import files as files_api
from agent.services import output_files


@pytest.fixture
def out(tmp_path, monkeypatch):
    root = tmp_path / "output"
    (root / "khu_rung_anh_sang" / "scenes").mkdir(parents=True)
    (root / "khu_rung_anh_sang" / "khu_rung_anh_sang_c2fea6d6_final.mp4").write_bytes(b"0123456789")
    (root / "khu_rung_anh_sang" / "scenes" / "cảnh 1.mp4").write_bytes(b"clip")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    monkeypatch.setattr("agent.config.OUTPUT_DIR", root)
    return root


@pytest.fixture
async def api(out):
    from agent.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


class TestFileUrl:
    def test_a_file_inside_gets_a_quoted_url(self, out):
        assert output_files.file_url(out / "khu_rung_anh_sang" / "scenes" / "cảnh 1.mp4") == \
            "/files/khu_rung_anh_sang/scenes/c%E1%BA%A3nh%201.mp4"

    @pytest.mark.parametrize("path", [None, "", "../secret.txt"])
    def test_nothing_outside_or_empty_gets_a_url(self, out, path):
        assert output_files.file_url(path) is None

    def test_an_absolute_path_outside_gets_no_url(self, out):
        assert output_files.file_url(out.parent / "secret.txt") is None
        assert output_files.file_url(out) is None  # the root itself is not a file


class TestGetFile:
    async def test_serves_a_file_with_range_support(self, api):
        r = await api.get("/files/khu_rung_anh_sang/khu_rung_anh_sang_c2fea6d6_final.mp4")
        assert r.status_code == 200 and r.content == b"0123456789"
        r = await api.get("/files/khu_rung_anh_sang/khu_rung_anh_sang_c2fea6d6_final.mp4",
                          headers={"Range": "bytes=2-5"})
        assert r.status_code == 206 and r.content == b"2345"  # the video player can seek

    async def test_serves_a_quoted_unicode_name(self, api):
        r = await api.get("/files/khu_rung_anh_sang/scenes/c%E1%BA%A3nh%201.mp4")
        assert r.status_code == 200 and r.content == b"clip"

    @pytest.mark.parametrize("path", [
        "/files/%2E%2E/secret.txt", "/files/..%5Csecret.txt", "/files/khu_rung_anh_sang/..%2F..%2Fsecret.txt",
        "/files/khu_rung_anh_sang", "/files/khu_rung_anh_sang/", "/files/", "/files/nope.mp4",
    ])
    async def test_no_escape_no_listing(self, api, path):
        r = await api.get(path)
        assert r.status_code == 404
        assert b"outside" not in r.content

    async def test_a_dot_dot_the_client_normalises_never_reaches_the_file(self, api):
        # httpx turns /files/../secret.txt into /secret.txt: no longer a /files path at all
        r = await api.get("/files/../secret.txt")
        assert b"outside" not in r.content

    async def test_only_get(self, api):
        path = "/files/khu_rung_anh_sang/khu_rung_anh_sang_c2fea6d6_final.mp4"
        for method in ("POST", "PUT", "DELETE"):
            assert (await api.request(method, path)).status_code == 405


class TestOpenFolder:
    async def test_reveals_a_file_inside(self, api, out):
        with patch.object(files_api, "_reveal") as reveal:
            r = await api.post("/api/system/open-folder",
                               json={"path": str(out / "khu_rung_anh_sang" / "khu_rung_anh_sang_c2fea6d6_final.mp4")})
        assert r.status_code == 200, r.text
        reveal.assert_called_once_with((out / "khu_rung_anh_sang" / "khu_rung_anh_sang_c2fea6d6_final.mp4").resolve())

    async def test_a_relative_folder_inside_is_opened(self, api, out):
        with patch.object(files_api, "_reveal") as reveal:
            r = await api.post("/api/system/open-folder", json={"path": "khu_rung_anh_sang/scenes"})
        assert r.status_code == 200
        reveal.assert_called_once_with((out / "khu_rung_anh_sang" / "scenes").resolve())

    @pytest.mark.parametrize("path, status", [
        ("../secret.txt", 400), ("C:/Windows/System32", 400), ("khu_rung_anh_sang/missing.mp4", 404),
    ])
    async def test_refused_outside_or_missing(self, api, out, path, status):
        with patch.object(files_api, "_reveal") as reveal:
            r = await api.post("/api/system/open-folder", json={"path": path})
        assert r.status_code == status
        reveal.assert_not_called()
