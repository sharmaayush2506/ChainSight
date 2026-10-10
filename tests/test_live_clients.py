"""Live API clients tested against mocked HTTP responses (shapes from the provider docs)."""
import asyncio
import json

import httpx
import pytest

from app import tinyfish
from app.chains import ethereum, tron


def _patch_client(monkeypatch, module, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)
    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


def test_tinyfish_search_fetch_agent(monkeypatch):
    def handler(req: httpx.Request):
        assert req.headers["X-API-Key"] == "k"
        if req.url.host == "api.search.tinyfish.ai":
            return httpx.Response(200, json={"results": [{"position": 1, "url": "https://a", "title": "t"}]})
        if req.url.host == "api.fetch.tinyfish.ai":
            body = json.loads(req.content)
            return httpx.Response(200, json={"results": [{"url": body["urls"][0], "text": "hello"}], "errors": []})
        sse = ('data: {"type":"STARTED","runId":"r1"}\n\n'
               'data: {"type":"STREAMING_URL","streaming_url":"https://live/x"}\n\n'
               'data: {"type":"PROGRESS","purpose":"clicking legal"}\n\n'
               'data: {"type":"COMPLETE","status":"COMPLETED","result":{"portal_url":"https://p"}}\n\n')
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
    _patch_client(monkeypatch, tinyfish, handler)
    tf = tinyfish.TinyFishClient("k", retries=0)
    assert asyncio.run(tf.search("q"))[0]["url"] == "https://a"
    assert asyncio.run(tf.fetch(["https://a"]))["results"][0]["text"] == "hello"
    events = []
    out = asyncio.run(tf.agent("https://x", "goal", on_event=events.append))
    assert out == {"result": {"portal_url": "https://p"}, "streaming_url": "https://live/x"}
    assert [e["type"] for e in events] == ["STREAMING_URL", "PROGRESS"]
    assert all(c["ok"] for c in tf.calls)


def test_tinyfish_agent_failure_is_retried_then_raised(monkeypatch):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, text='data: {"type":"COMPLETE","status":"FAILED","error":"blocked"}\n\n')
    _patch_client(monkeypatch, tinyfish, handler)

    async def no_sleep(_):
        return None
    monkeypatch.setattr(tinyfish.asyncio, "sleep", no_sleep)
    tf = tinyfish.TinyFishClient("k", retries=2)
    with pytest.raises(tinyfish.TinyFishError):
        asyncio.run(tf.agent("https://x", "goal"))
    assert len(calls) == 3 and not any(c["ok"] for c in tf.calls)


def test_trongrid_parsing(monkeypatch):
    a, b = "T" + "A" * 33, "T" + "B" * 33

    def handler(req):
        assert "only_from" in req.url.params
        return httpx.Response(200, json={"data": [
            {"transaction_id": "h1", "from": a, "to": b, "value": "1500000000", "block_timestamp": 1700000000000,
             "token_info": {"symbol": "USDT", "decimals": 6}},
            {"transaction_id": "h2", "from": a, "to": b, "value": "1", "block_timestamp": 1700000000000,
             "token_info": {"symbol": "FAKEUSDT", "decimals": 6}},
        ]})
    _patch_client(monkeypatch, tron, handler)
    out = asyncio.run(tron.TronProvider().outgoing(a))
    assert len(out) == 1 and out[0].amount == 1500 and out[0].timestamp == 1700000000


def test_etherscan_parsing(monkeypatch):
    me, other = "0x" + "a" * 40, "0x" + "b" * 40

    def handler(req):
        if req.url.params["action"] == "txlist":
            return httpx.Response(200, json={"status": "1", "result": [
                {"hash": "0x1", "from": me, "to": other, "value": str(2 * 10**18), "timeStamp": "1700000000", "isError": "0"}]})
        return httpx.Response(200, json={"status": "0", "message": "No transactions found", "result": []})
    _patch_client(monkeypatch, ethereum, handler)
    out = asyncio.run(ethereum.EthereumProvider("k").outgoing(me))
    assert len(out) == 1 and out[0].amount == 2 and out[0].token == "ETH"
