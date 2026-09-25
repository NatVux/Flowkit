"""The real startup/shutdown path: every background task (worker, pipeline runner) must
start and stop. Guards against wiring mistakes no endpoint test sees - e.g. the runner's
loop method being shadowed by a same-named operation."""

import asyncio

import pytest

from agent import main
from agent.db import crud, schema
from agent.services.pipeline import runner as runner_module
from agent.worker import processor


@pytest.fixture
async def isolated(tmp_path, monkeypatch):
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "life.db")
    monkeypatch.setattr(processor, "_controller", None)
    monkeypatch.setattr(runner_module, "_runner", None)

    async def no_ws_server():
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "run_ws_server", no_ws_server)
    yield
    await schema.close_db()


async def test_lifespan_starts_and_stops_every_background_task(isolated):
    async with main.lifespan(main.app):
        await asyncio.sleep(0.05)  # let the tasks begin
        tasks = {t.get_name(): t for t in asyncio.all_tasks()}
        running = [t for t in tasks.values() if not t.done()]
        coros = " ".join(repr(t.get_coro()) for t in running)
        assert "WorkerController.start" in coros
        assert "PipelineRunner.run_forever" in coros
        assert await crud.list_requests() == []  # startup reset + hold sweep ran on an empty DB
    await asyncio.sleep(0)
    assert all(t.done() for t in running if "run_forever" in repr(t.get_coro()))
