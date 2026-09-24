"""Focused worker safety tests."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from agent.worker import processor


@pytest.mark.asyncio
async def test_rate_limiter_releases_slot_when_waiter_is_cancelled():
    limiter = processor.APIRateLimiter(max_concurrent=1, cooldown_seconds=0)
    await limiter.acquire()
    waiter = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    limiter.release()

    await asyncio.wait_for(limiter.acquire(), timeout=1)
    limiter.release()


@pytest.mark.asyncio
async def test_cancelled_worker_task_returns_claimed_request_to_pending():
    controller = processor.WorkerController()
    request = {
        "id": "request-1",
        "project_id": "project-1",
        "video_id": "video-1",
        "scene_id": "scene-1",
        "type": "GENERATE_IMAGE",
        "retry_count": 0,
        "started_at": "2026-09-23T00:00:00.000000Z",
    }
    with patch.object(processor, "_process_one", side_effect=asyncio.CancelledError), \
         patch.object(processor, "_set_request_status", new_callable=AsyncMock) as set_status:
        with pytest.raises(asyncio.CancelledError):
            await controller._run_one(request)

    set_status.assert_awaited_once_with(
        request, "PENDING",
        error_message="worker task cancelled",
        last_failure_reason="worker task cancelled",
    )
    assert controller.active_count == 0


@pytest.mark.asyncio
async def test_shutdown_drain_cancels_overdue_tasks():
    controller = processor.WorkerController()
    task = asyncio.create_task(asyncio.sleep(60))
    controller._tasks.add(task)
    await controller.drain(timeout=0)
    assert task.cancelled()


def test_permanent_errors_are_not_retryable():
    assert processor._is_retryable_error("permission denied by provider") is False
    assert processor._is_retryable_error("UNSUPPORTED_ON_BATCH_API") is False
    assert processor._is_retryable_error("temporary timeout") is True


def test_retry_backoff_includes_jitter(monkeypatch):
    monkeypatch.setattr(processor, "RETRY_JITTER_SECONDS", 2)
    monkeypatch.setattr(processor.random, "uniform", lambda lower, upper: upper)
    retry_at = processor._retry_at(10)
    assert retry_at.endswith("Z")
