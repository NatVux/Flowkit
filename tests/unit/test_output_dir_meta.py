import io
import json
from types import SimpleNamespace

import pytest

from agent.api import projects as projects_api


PID = "6130c05e-b8f9-40d5-88a4-f231d693f0dd"
VID = "c2fea6d6-d57f-43ad-8ac1-8d205cb1185d"
NAME = "Khu Rừng Ánh Sáng"


class FakeRepo:
    async def get_project(self, pid):
        return SimpleNamespace(name=NAME, material="3d_pixar")

    async def list_videos(self, pid):
        return [SimpleNamespace(id=VID, orientation="VERTICAL")]

    async def list_scenes(self, video_id):
        return [object(), object(), object()]


@pytest.fixture
def output_env(monkeypatch, tmp_path):
    monkeypatch.setattr(projects_api, "_get_repo", lambda: FakeRepo())
    monkeypatch.setattr(projects_api, "BASE_DIR", tmp_path)
    # Simulate Windows, where text files default to cp1252 and cannot hold "ừ".
    real_text_encoding = io.text_encoding
    monkeypatch.setattr(io, "text_encoding", lambda enc, *a: "cp1252" if enc is None else real_text_encoding(enc, *a))
    return tmp_path / "output" / "khu_rung_anh_sang"


@pytest.mark.asyncio
async def test_vietnamese_project_name_round_trips_through_meta_json(output_env):
    result = await projects_api.get_output_dir(PID)

    meta_path = output_env / "meta.json"
    meta = json.loads(meta_path.read_bytes().decode("utf-8"))
    assert result["slug"] == "khu_rung_anh_sang"
    assert meta["project_name"] == NAME
    assert meta["orientation"] == "VERTICAL"
    assert meta["scene_count"] == 3
    assert not (output_env / "meta.json.tmp").exists()

    # A second call reads the file back and keeps the original created_at.
    again = await projects_api.get_output_dir(PID)
    assert again["meta"]["project_name"] == NAME
    assert again["meta"]["created_at"] == meta["created_at"]


@pytest.mark.asyncio
async def test_empty_meta_json_left_by_an_old_failed_write_is_rebuilt(output_env):
    output_env.mkdir(parents=True)
    (output_env / "meta.json").write_bytes(b"")

    result = await projects_api.get_output_dir(PID)

    assert result["meta"]["orientation"] == "VERTICAL"
    assert json.loads((output_env / "meta.json").read_text(encoding="utf-8"))["project_name"] == NAME


@pytest.mark.asyncio
async def test_failed_write_leaves_previous_meta_json_intact(output_env, monkeypatch):
    await projects_api.get_output_dir(PID)
    before = (output_env / "meta.json").read_bytes()

    def fail_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(projects_api.os, "replace", fail_replace)
    with pytest.raises(OSError):
        await projects_api.get_output_dir(PID)

    assert (output_env / "meta.json").read_bytes() == before
