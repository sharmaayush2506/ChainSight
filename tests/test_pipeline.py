import asyncio
import time

from fastapi.testclient import TestClient

from app import orchestrator
from app.chains import is_valid_address
from app.chains.demo import DEMO_SEEDS, DemoProvider
from app.main import app
from app.patterns import detect_patterns
from app.tracing import nearest_vasps, trace_funds


def test_address_validation():
    assert is_valid_address("tron", DEMO_SEEDS["tron"])
    assert is_valid_address("ethereum", "0x28C6c06298d514Db089934071355E5743bf21d60")
    assert not is_valid_address("ethereum", "0x123")
    assert not is_valid_address("tron", "T0OIl" + "a" * 29)  # 0 O I l are not base58


def _demo_graph(chain="tron"):
    p = DemoProvider(chain)
    seed = DEMO_SEEDS[chain]
    p.bind(seed)
    return asyncio.run(trace_funds(p, seed, max_hops=8, max_branch=4, max_nodes=80)), seed


def test_trace_finds_exchanges_via_deposit_addresses():
    g, seed = _demo_graph()
    vasps = nearest_vasps(g, seed)
    assert [v["entity"] for v in vasps] == ["okx", "binance"]
    assert all(v["address_type"] == "deposit" for v in vasps)
    assert vasps[0]["hops"] == 3 and vasps[0]["amount_estimate"] == 8990


def test_patterns_detected():
    g, seed = _demo_graph("ethereum")
    kinds = {p["type"] for p in detect_patterns(g)}
    assert {"pass_through", "fan_in", "fan_out", "peel_chain"} <= kinds


def test_hop_limit_marks_frontier():
    p = DemoProvider("tron")
    seed = DEMO_SEEDS["tron"]
    p.bind(seed)
    g = asyncio.run(trace_funds(p, seed, max_hops=2, max_branch=4, max_nodes=80))
    assert any(d.get("frontier") for _, d in g.nodes(data=True))
    assert nearest_vasps(g, seed) == []


def test_full_case_demo():
    case = orchestrator.new_case("tron", DEMO_SEEDS["tron"], demo=True, complaint_ref="T-1",
                                 p2p_url="https://p2p.example/s/1")
    case = asyncio.run(orchestrator.run_case(case))
    assert case["status"] == "done", case.get("traceback")
    assert case["risk"]["level"] == "High"
    assert case["vasps"][0]["name"] == "OKX"
    assert case["contacts"]["okx"]["portal_url"]
    assert case["intel"]["p2p"]["profile"]["seller_name"]
    assert len(case["evidence"]) >= 3 and all(len(e["sha256"]) == 64 for e in case["evidence"])
    assert "Ref T-1" in case["freeze_request"]


def test_api_end_to_end():
    with TestClient(app) as client:
        assert client.post("/api/cases", json={"chain": "tron", "address": "0xnotvalidtronaddress00"}
                           ).status_code == 422
        r = client.post("/api/cases", json={"chain": "tron", "address": DEMO_SEEDS["tron"], "demo": True})
        assert r.status_code == 202
        cid = r.json()["id"]
        deadline = time.time() + 30
        while client.get(f"/api/cases/{cid}").json()["status"] not in ("done", "failed"):
            assert time.time() < deadline
            time.sleep(0.2)
        assert client.get(f"/api/cases/{cid}").json()["status"] == "done"
        pdf = client.get(f"/api/cases/{cid}/report.pdf")
        assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
        ev = client.get(f"/api/cases/{cid}/evidence/1")
        import hashlib
        assert hashlib.sha256(ev.content).hexdigest() == ev.headers["x-evidence-sha256"]
        actions = [a["action"] for a in client.get(f"/api/cases/{cid}/audit").json()]
        assert actions[:1] == ["case_created"] and "report_downloaded" in actions
