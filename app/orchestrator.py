"""Runs a case end to end. On-chain tracing and off-chain intelligence run in parallel,
then VASP contact discovery runs for each exchange found, then scoring and the report.

The prototype uses in-process asyncio tasks; swapping in Redis + a worker (Celery/RQ/arq)
only changes how `run_case` is dispatched, not the pipeline itself.
"""
from __future__ import annotations

import asyncio
import time
import traceback
import uuid

import networkx as nx

from . import db
from .chains import get_provider, normalize_address
from .config import settings
from .evidence import EvidenceLocker
from .intel import p2p_seller_profile, wallet_mentions
from .labels import sanctions_hit
from .patterns import detect_patterns
from .risk import freeze_request, next_actions, score
from .tinyfish import make_client
from .tracing import nearest_vasps, trace_funds
from .vasp import discover_contact

STEPS = [
    ("trace", "On-chain tracing"),
    ("offchain", "Off-chain intelligence (TinyFish Search + Fetch)"),
    ("vasp", "VASP contact discovery (TinyFish Agent)"),
    ("evidence", "Evidence capture"),
    ("report", "Risk scoring and report"),
]

_tasks: set[asyncio.Task] = set()


def new_case(chain: str, address: str, *, demo: bool, complaint_ref: str = "",
             p2p_url: str = "", notes: str = "") -> dict:
    address = normalize_address(chain, address)
    case = {
        "id": uuid.uuid4().hex[:10],
        "created_at": int(time.time()),
        "status": "queued",
        "chain": chain,
        "address": address,
        "complaint_ref": complaint_ref,
        "p2p_url": p2p_url,
        "notes": notes,
        "demo": demo or not settings.chain_live(chain),
        "steps": {k: {"label": label, "status": "pending", "log": []} for k, label in STEPS},
        "timings": {},
    }
    db.save_case(case)
    db.audit(case["id"], "case_created", f"{chain}:{address}", actor="investigator")
    return case


def start(case: dict) -> None:
    task = asyncio.create_task(run_case(case))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def run_case(case: dict) -> dict:
    t_start = time.monotonic()
    case["status"] = "running"
    demo = case["demo"]
    seed = case["address"]
    tf = make_client(demo, seed)
    case["tinyfish_mode"] = "simulated" if tf.simulated else "live"
    evidence = EvidenceLocker(case["id"], simulated=tf.simulated)

    def step(key: str, status: str | None = None, msg: str | None = None):
        s = case["steps"][key]
        if status:
            s["status"] = status
            if status == "running":
                s["_t"] = time.monotonic()
            elif "_t" in s:
                case["timings"][key] = round(time.monotonic() - s.pop("_t"), 2)
        if msg:
            s["log"].append({"ts": int(time.time()), "msg": msg})
        db.save_case(case)

    async def do_trace():
        step("trace", "running", f"Tracing {case['chain']} wallet "
                                 f"({'demo dataset' if demo else 'live API'})")
        provider = get_provider(case["chain"], demo=demo)
        if provider.is_demo:
            provider.bind(seed)
        g = await trace_funds(provider, seed, max_hops=settings.max_hops,
                              max_branch=settings.max_branch, max_nodes=settings.max_nodes,
                              log=lambda m: step("trace", msg=m))
        pats = detect_patterns(g)
        vasps = nearest_vasps(g, seed)
        step("trace", "done", f"{g.number_of_nodes()} addresses, {g.number_of_edges()} flows, "
                              f"{len(pats)} pattern(s), {len(vasps)} VASP(s) reached")
        return g, pats, vasps

    async def do_offchain():
        step("offchain", "running", "Searching public scam reports, forums and social pages")
        out = await wallet_mentions(tf, seed, evidence, log=lambda m: step("offchain", msg=m))
        sanc = sanctions_hit(seed)
        out["sanctions"] = sanc
        step("offchain", msg=f"Sanctions / abuse lists: {'HIT ' + sanc['list'] if sanc else 'no match'}")
        if case.get("p2p_url"):
            step("offchain", msg="Extracting P2P seller profile with TinyFish Agent")
            out["p2p"] = await p2p_seller_profile(tf, case["p2p_url"], evidence)
        step("offchain", "done" if not out["errors"] else "partial",
             "; ".join(out["errors"])[:300] or None)
        return out

    try:
        (g, pats, vasps), intel = await asyncio.gather(do_trace(), do_offchain())

        step("vasp", "running", f"Finding law-enforcement contacts for "
                                f"{', '.join(v['name'] for v in vasps) or 'no VASP'}")
        contacts_list = await asyncio.gather(*(
            discover_contact(tf, v["entity"], evidence, log=lambda m: step("vasp", msg=m))
            for v in vasps[:3]))
        contacts = {c["entity"]: c for c in contacts_list}
        step("vasp", "partial" if any(c.get("manual_review") for c in contacts_list) else "done")

        step("evidence", "running")
        step("evidence", "done", f"{len(evidence.items)} page snapshot(s), SHA-256 hashed")

        step("report", "running")
        case.update({
            "graph": graph_json(g),
            "patterns": pats,
            "vasps": vasps,
            "contacts": contacts,
            "intel": intel,
            "evidence": evidence.items,
            "tinyfish_calls": tf.calls,
        })
        case["risk"] = score(g, seed, pats, vasps, intel)
        case["next_actions"] = next_actions(g, vasps, contacts)
        case["freeze_request"] = freeze_request(case)
        step("report", "done", f"Risk {case['risk']['level']} ({case['risk']['score']}/100)")
        case["status"] = "done"
    except Exception as exc:  # noqa: BLE001
        case["status"] = "failed"
        case["error"] = f"{type(exc).__name__}: {exc}"
        case["traceback"] = traceback.format_exc()[-2000:]
        for s in case["steps"].values():
            if s["status"] == "running":
                s["status"] = "failed"
    case["timings"]["total"] = round(time.monotonic() - t_start, 2)
    db.save_case(case)
    db.audit(case["id"], "case_completed", case["status"])
    return case


def graph_json(g: nx.DiGraph) -> dict:
    nodes = []
    for n, d in g.nodes(data=True):
        nodes.append({"id": n, "type": d.get("type"), "hop": d.get("hop"),
                      "entity": d.get("entity"), "label": d.get("label"),
                      "label_source": d.get("label_source"),
                      "patterns": sorted(set(d.get("patterns", []))),
                      "sanctioned": d.get("sanctioned"), "frontier": d.get("frontier", False),
                      "leaf": d.get("leaf", False),
                      "omitted_branches": d.get("omitted_branches", 0)})
    edges = [{"source": s, "target": t, "amount": round(e["amount"], 6), "token": e["token"],
              "first_ts": e["first_ts"], "last_ts": e["last_ts"],
              "tx_hashes": [x["tx_hash"] for x in e["txs"]]}
             for s, t, e in g.edges(data=True)]
    return {"nodes": nodes, "edges": edges}
