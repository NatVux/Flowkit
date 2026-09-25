import io
import json

import pytest

from agent.api import tts as tts_api


TEMPLATE = {
    "name": "giong_ke_chuyen",
    "audio_path": "tts_templates/giong_ke_chuyen.wav",
    "text": "Ngày xửa ngày xưa, ở một khu rừng già sau làng.",
    "instruct": "Giọng kể chuyện ấm áp, chậm rãi, truyền cảm",
    "duration": 4.2,
}


@pytest.fixture
def templates_meta(monkeypatch, tmp_path):
    path = tmp_path / "templates.json"
    monkeypatch.setattr(tts_api, "TEMPLATES_META", path)
    # Simulate Windows, where text files default to cp1252 and cannot hold "ử" or "ọ".
    real_text_encoding = io.text_encoding
    monkeypatch.setattr(io, "text_encoding", lambda enc, *a: "cp1252" if enc is None else real_text_encoding(enc, *a))
    return path


def test_vietnamese_template_round_trips_through_templates_json(templates_meta):
    tts_api._save_templates_meta({TEMPLATE["name"]: TEMPLATE, "Giọng Kể": TEMPLATE})

    on_disk = json.loads(templates_meta.read_bytes().decode("utf-8"))
    assert on_disk[TEMPLATE["name"]]["text"] == TEMPLATE["text"]
    assert tts_api._load_templates_meta() == {TEMPLATE["name"]: TEMPLATE, "Giọng Kể": TEMPLATE}
    assert not templates_meta.with_name("templates.json.tmp").exists()


def test_failed_write_leaves_previous_templates_json_intact(templates_meta, monkeypatch):
    tts_api._save_templates_meta({TEMPLATE["name"]: TEMPLATE})
    before = templates_meta.read_bytes()

    def fail_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(tts_api.os, "replace", fail_replace)
    with pytest.raises(OSError):
        tts_api._save_templates_meta({})

    assert templates_meta.read_bytes() == before
