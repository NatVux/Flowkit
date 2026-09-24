"""Integration-style tests for the Flow extension transport boundary."""

import asyncio
import json

import pytest

from agent.services.flow_client import FlowClient
from agent.services.flow_protocol import FlowProtocolError, serialize_request


class FakeWebSocket:
    def __init__(self, on_send=None):
        self.sent = []
        self.on_send = on_send

    async def send(self, message):
        self.sent.append(message)
        if self.on_send:
            await self.on_send(message)


@pytest.mark.asyncio
async def test_successful_request_routes_response_to_its_owner():
    client = FlowClient()
    websocket = None

    async def respond(message):
        request = json.loads(message)
        await client.handle_message({"id": request["id"], "data": {"ok": True}}, websocket)

    websocket = FakeWebSocket(respond)
    client.set_extension(websocket)
    result = await client.request("batch_rpc", {"rpcid": "rpc"}, timeout=1)

    assert result["data"]["ok"] is True
    assert not client._pending
    assert json.loads(websocket.sent[0])["method"] == "batch_rpc"


@pytest.mark.asyncio
async def test_timeout_cleans_pending_request():
    client = FlowClient()
    websocket = FakeWebSocket()
    client.set_extension(websocket)

    result = await client.request("batch_rpc", {}, timeout=0.01)

    assert "Timeout" in result["error"]
    assert not client._pending
    assert not client._pending_ws


@pytest.mark.asyncio
async def test_disconnect_fails_owned_request_and_reconnect_works():
    client = FlowClient()
    first = FakeWebSocket()
    client.set_extension(first)
    pending = asyncio.create_task(client.request("batch_rpc", {}, timeout=5))
    await asyncio.sleep(0)
    client.clear_extension(first)
    disconnected = await pending
    assert "disconnected" in disconnected["error"].lower()

    second = FakeWebSocket()
    client.set_extension(second)

    async def respond(message):
        request = json.loads(message)
        await client.handle_message({"id": request["id"], "data": "reconnected"}, second)

    second.on_send = respond
    result = await client.request("batch_rpc", {}, timeout=1)
    assert result["data"] == "reconnected"


@pytest.mark.asyncio
async def test_malformed_unknown_and_duplicate_responses_are_ignored():
    client = FlowClient()
    websocket = FakeWebSocket()
    client.set_extension(websocket)

    assert await client.handle_message({"type": "unexpected"}, websocket) is False
    assert client.deliver_response({"id": "not-a-uuid", "data": 1}, websocket) is False
    assert client.deliver_response({"id": "00000000-0000-4000-8000-000000000001", "data": 1}, websocket) is False

    request_id = "00000000-0000-4000-8000-000000000002"
    future = asyncio.get_running_loop().create_future()
    client._pending[request_id] = future
    client._pending_ws[request_id] = websocket
    response = {"id": request_id, "data": {"value": 1}}
    assert client.deliver_response(response, websocket) is True
    assert client.deliver_response(response, websocket) is False
    assert await future == response


@pytest.mark.asyncio
async def test_response_from_another_extension_cannot_cross_talk():
    client = FlowClient()
    owner = FakeWebSocket()
    other = FakeWebSocket()
    client.set_extension(owner)
    client.set_extension(other)

    request_id = "00000000-0000-4000-8000-000000000003"
    future = asyncio.get_running_loop().create_future()
    client._pending[request_id] = future
    client._pending_ws[request_id] = owner
    response = {"id": request_id, "data": "wrong socket"}

    assert client.deliver_response(response, other) is False
    assert not future.done()
    assert client.deliver_response({"id": request_id, "data": "right socket"}, owner) is True
    assert (await future)["data"] == "right socket"


def test_serializer_rejects_invalid_ids_and_protocol_shape():
    with pytest.raises(FlowProtocolError):
        serialize_request("bad", "batch_rpc", {})
    with pytest.raises(FlowProtocolError):
        serialize_request("00000000-0000-4000-8000-000000000004", "", {})


def test_failover_is_limited_to_read_operations():
    from agent.services import flow_batch as fb

    assert FlowClient._is_failover_safe("get_status", {}) is True
    assert FlowClient._is_failover_safe("batch_rpc", {"rpcid": fb.RPC_MEDIA}) is True
    assert FlowClient._is_failover_safe("batch_rpc", {"rpcid": fb.RPC_GEN_IMAGE}) is False
    assert FlowClient._is_failover_safe("batch_rpc", {"rpcid": fb.RPC_GEN_VIDEO}) is False
