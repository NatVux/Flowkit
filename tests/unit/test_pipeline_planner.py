"""Pure pipeline planning: selection, waves, estimate, failure classes, spend."""

import json

import pytest

from agent.services.pipeline import planner as pl

U = [f"{i:08d}-1111-4111-8111-111111111111" for i in range(10)]


def scene(i, *, chain="ROOT", parent=None, image=False, video=False, names=(), p="vertical"):
    return {"id": f"s{i}", "display_order": i, "chain_type": chain, "parent_scene_id": parent,
            "character_names": json.dumps(list(names), ensure_ascii=False),
            f"{p}_image_status": "COMPLETED" if image else "PENDING",
            f"{p}_image_media_id": U[i] if image else None,
            f"{p}_video_status": "COMPLETED" if video else "PENDING",
            f"{p}_video_media_id": U[i] if video else None}


class TestSelection:
    def test_uuid_rules(self):
        assert pl.is_uuid(U[0]) and not pl.is_uuid("CAMSxyz123") and not pl.is_uuid(None)
        assert pl.uuid_from_url(f"https://flow-content.google/image/{U[3]}?Expires=1") == U[3]
        assert pl.uuid_from_url("https://example.test/nothing") is None

    def test_refs_skip_entities_that_have_a_uuid_but_not_cams(self):
        entities = [{"id": "a", "media_id": U[0]}, {"id": "b", "media_id": "CAMSabc"}, {"id": "c", "media_id": None}]
        assert [e["id"] for e in pl.refs_needed(entities)] == ["b", "c"]

    def test_waves_follow_the_chain(self):
        scenes = [scene(0), scene(1, chain="CONTINUATION", parent="s0"),
                  scene(2, chain="CONTINUATION", parent="s1"), scene(3)]
        tasks = pl.image_waves(scenes, "vertical")
        assert [(t.scene_id, t.wave, t.request_type) for t in tasks] == [
            ("s0", 0, "GENERATE_IMAGE"), ("s3", 0, "GENERATE_IMAGE"),
            ("s1", 1, "EDIT_IMAGE"), ("s2", 2, "EDIT_IMAGE")]

    def test_child_of_a_finished_parent_goes_in_the_first_wave(self):
        scenes = [scene(0, image=True), scene(1, chain="CONTINUATION", parent="s0")]
        assert [(t.scene_id, t.wave, t.request_type) for t in pl.image_waves(scenes, "vertical")] == \
            [("s1", 0, "EDIT_IMAGE")]

    def test_parallel_chains_are_independent(self):
        scenes = [scene(0), scene(1), scene(2, chain="CONTINUATION", parent="s0"),
                  scene(3, chain="CONTINUATION", parent="s1")]
        waves = {t.scene_id: t.wave for t in pl.image_waves(scenes, "vertical")}
        assert waves == {"s0": 0, "s1": 0, "s2": 1, "s3": 1}

    def test_cams_image_id_counts_as_not_done(self):
        s = scene(0, image=True)
        s["vertical_image_media_id"] = "CAMS123"
        assert [t.scene_id for t in pl.image_waves([s], "vertical")] == ["s0"]

    def test_missing_parent_and_cycles_are_plan_errors(self):
        with pytest.raises(pl.PlanError, match="not in this video"):
            pl.image_waves([scene(0, chain="CONTINUATION", parent="elsewhere")], "vertical")
        with pytest.raises(pl.PlanError, match="cycle"):
            pl.image_waves([scene(0, chain="CONTINUATION", parent="s1"),
                            scene(1, chain="CONTINUATION", parent="s0")], "vertical")

    def test_orientation_prefix_is_respected(self):
        s = scene(0, image=True, p="horizontal")
        assert pl.image_waves([s], "horizontal") == [] and len(pl.image_waves([s], "vertical")) == 1

    def test_scene_uses_entity_by_name_or_slug(self):
        s = scene(0, names=["Mèo Con", "cho_dem"])
        assert pl.uses_entity(s, {"name": "Mèo Con", "slug": "meo_con"})
        assert pl.uses_entity(s, {"name": "Chợ Đêm", "slug": "cho_dem"})
        assert not pl.uses_entity(s, {"name": "Bà Cụ", "slug": "ba_cu"})


class TestEstimate:
    def test_fresh_project(self):
        entities = [{"name": n, "slug": n.lower(), "media_id": None} for n in ("A", "B", "C")]
        scenes = [scene(0, names=["A"]), scene(1, chain="CONTINUATION", parent="s0", names=["A"]), scene(2)]
        est = pl.estimate(entities, scenes, "VERTICAL")
        assert est["kind"] == "minimum" and "retries" in est["note"]
        assert (est["refs"], est["images"], est["image_waves"], est["videos"]) == (3, 3, [2, 1], 3)
        assert est["total_generations"] == 9 and est["concat"] == 0

    def test_a_new_reference_resets_the_scenes_that_use_it(self):
        entities = [{"name": "A", "slug": "a", "media_id": None}, {"name": "B", "slug": "b", "media_id": U[1]}]
        scenes = [scene(0, image=True, video=True, names=["A"]), scene(1, image=True, video=True, names=["B"])]
        est = pl.estimate(entities, scenes, "VERTICAL")
        assert (est["refs"], est["images"], est["videos"], est["scenes_reset_by_new_refs"]) == (1, 1, 1, 1)

    def test_nothing_left(self):
        entities = [{"name": "A", "slug": "a", "media_id": U[0]}]
        est = pl.estimate(entities, [scene(0, image=True, video=True)], "VERTICAL")
        assert est["total_generations"] == 0


class TestFailures:
    @pytest.mark.parametrize("error, action, code", [
        ("RpcError: x [PUBLIC_ERROR_UNSAFE_GENERATION]", "USER", "UNSAFE_GENERATION"),
        ("bad input PUBLIC_ERROR_MINOR_INPUT_IMAGE", "USER", "MINOR_INPUT_IMAGE"),
        ("FlowBatchError: ogiZ0b: PUBLIC_ERROR_UNUSUAL_ACTIVITY local cooldown active", "PAUSE", "UNUSUAL_ACTIVITY"),
        ("UNSUPPORTED_ON_BATCH_API: start+end-frame chaining", "USER", "UNSUPPORTED_ON_BATCH_API"),
        ("RpcError: eb1hJf failed: [7, None, [['type.googleapis.com/google.rpc.ErrorInfo', "
         "['PUBLIC_ERROR_MODEL_ACCESS_DENIED']]]]", "USER", "MODEL_ACCESS_DENIED"),
        ("model access denied", "USER", "FAILED_AFTER_RETRIES"),
        ("not found after 2 media re-upload recoveries: x", "USER", "MEDIA_NOT_FOUND"),
        ("Internal error encountered", "USER", "FAILED_AFTER_RETRIES"),
    ])
    def test_classes(self, error, action, code):
        got = pl.classify_failed_request({"error_message": error})
        assert (got.action, got.code) == (action, code)

    def test_model_access_denied_says_why(self):
        got = pl.classify_failed_request({"error_message": "x ['PUBLIC_ERROR_MODEL_ACCESS_DENIED'] y"})
        assert got.message.startswith("Model video không khả dụng với gói tài khoản")

    @pytest.mark.parametrize("req, spent", [
        (None, 0),
        ({"type": "GENERATE_IMAGE", "started_at": None}, 0),
        ({"type": "GENERATE_IMAGE", "started_at": "t", "retry_count": 0}, 1),
        ({"type": "EDIT_IMAGE", "started_at": "t", "retry_count": 2}, 3),
        ({"type": "GENERATE_IMAGE", "started_at": "t", "error_message": "skipped: already completed"}, 0),
        ({"type": "GENERATE_VIDEO", "started_at": "t", "retry_count": 3, "request_id": "op"}, 1),
        ({"type": "GENERATE_VIDEO", "started_at": "t", "retry_count": 1, "request_id": None}, 0),
        ({"type": "GENERATE_CHARACTER_IMAGE", "started_at": "t", "retry_count": 1}, 2),
    ])
    def test_generations_spent(self, req, spent):
        assert pl.generations_spent(req) == spent
