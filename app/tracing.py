"""Hop-by-hop fund-flow tracing into a NetworkX graph."""
from __future__ import annotations

import asyncio
from collections import defaultdict

import networkx as nx

from .chains import ChainProvider, Transfer
from .labels import LabelStore, sanctions_hit

TERMINAL_TYPES = {"exchange", "mixer", "bridge"}
SWEEP_SHARE = 0.8  # share of outflow to an exchange that marks a deposit address


async def trace_funds(provider: ChainProvider, seed: str, *, max_hops: int, max_branch: int,
                      max_nodes: int, log=lambda msg: None) -> nx.DiGraph:
    labels = LabelStore(provider.chain, provider.extra_labels())
    g = nx.DiGraph()
    g.add_node(seed, hop=0, role="seed")
    sem = asyncio.Semaphore(4)  # stay polite with free-tier API rate limits

    incoming = await provider.incoming(seed)
    for t in incoming[:10]:
        _add_edge(g, t)
        g.nodes[t.sender].setdefault("role", "source")
        g.nodes[t.sender].setdefault("hop", -1)
    first_in = min((t.timestamp for t in incoming), default=0)
    log(f"Seed received {len(incoming)} incoming transfer(s)")

    frontier: list[tuple[str, int]] = [(seed, first_in)]
    visited = {seed}
    for hop in range(max_hops):
        next_frontier: list[tuple[str, int]] = []

        async def expand(addr: str, since: int):
            async with sem:
                return addr, await provider.outgoing(addr, since)

        expandable = [(a, s) for a, s in frontier if not _is_terminal(labels, a, seed)]
        results = await asyncio.gather(*(expand(a, s) for a, s in expandable),
                                       return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                log(f"Hop {hop + 1}: data fetch failed ({res}); branch left for manual review")
                continue
            addr, outs = res
            if not outs:
                g.nodes[addr]["leaf"] = True
                continue
            by_receiver: dict[str, list[Transfer]] = defaultdict(list)
            for t in outs:
                by_receiver[t.receiver].append(t)
            ranked = sorted(by_receiver.items(), key=lambda kv: -sum(t.amount for t in kv[1]))
            for receiver, txs in ranked[:max_branch]:
                for t in txs:
                    _add_edge(g, t)
                if receiver not in visited:
                    visited.add(receiver)
                    g.nodes[receiver]["hop"] = hop + 1
                    if len(visited) < max_nodes:
                        next_frontier.append((receiver, min(t.timestamp for t in txs)))
                    else:
                        g.nodes[receiver]["frontier"] = True
            if len(ranked) > max_branch:
                g.nodes[addr]["omitted_branches"] = len(ranked) - max_branch
        log(f"Hop {hop + 1}: expanded {len(expandable)} address(es), "
            f"{len(next_frontier)} new address(es) to follow")
        frontier = next_frontier
        if not frontier:
            break
    for addr, _ in frontier:
        if not _is_terminal(labels, addr, seed):
            g.nodes[addr]["frontier"] = True  # hop limit reached; can be traced further

    _classify(g, labels, seed)
    return g


def _is_terminal(labels: LabelStore, addr: str, seed: str) -> bool:
    label = labels.get(addr)
    return addr != seed and bool(label) and label.get("type") in TERMINAL_TYPES


def _add_edge(g: nx.DiGraph, t: Transfer) -> None:
    if g.has_edge(t.sender, t.receiver):
        e = g.edges[t.sender, t.receiver]
        if any(x["tx_hash"] == t.tx_hash for x in e["txs"]):
            return
        e["amount"] += t.amount
        e["txs"].append(t.to_dict())
        e["first_ts"] = min(e["first_ts"], t.timestamp)
        e["last_ts"] = max(e["last_ts"], t.timestamp)
    else:
        g.add_edge(t.sender, t.receiver, amount=t.amount, token=t.token, txs=[t.to_dict()],
                   first_ts=t.timestamp, last_ts=t.timestamp)


def _classify(g: nx.DiGraph, labels: LabelStore, seed: str) -> None:
    for node, data in g.nodes(data=True):
        label = labels.get(node)
        sanc = sanctions_hit(node)
        if sanc:
            data["sanctioned"] = sanc
        if node == seed:
            data["type"] = "seed"
        elif label:
            data["type"] = label["type"]
            data["entity"] = label["entity"]
            data["label"] = label["name"]
            data["label_source"] = label.get("source", "")
        elif data.get("role") == "source":
            data["type"] = "source"
        else:
            data["type"] = "intermediary"
    # Deposit-address inference: an unlabelled address that sweeps most of its
    # outflow into a known exchange wallet is that exchange's customer deposit address.
    for node, data in g.nodes(data=True):
        if data["type"] != "intermediary":
            continue
        out_total = sum(e["amount"] for _, _, e in g.out_edges(node, data=True))
        if not out_total:
            continue
        per_entity: dict[str, float] = defaultdict(float)
        for _, dst, e in g.out_edges(node, data=True):
            if g.nodes[dst].get("type") == "exchange":
                per_entity[g.nodes[dst]["entity"]] += e["amount"]
        if per_entity:
            entity, amt = max(per_entity.items(), key=lambda kv: kv[1])
            if amt / out_total >= SWEEP_SHARE:
                data["type"] = "deposit"
                data["entity"] = entity
                data["label"] = f"Deposit address (inferred, sweeps to {entity})"
                data["label_source"] = "sweep heuristic"


def nearest_vasps(g: nx.DiGraph, seed: str) -> list[dict]:
    """For each exchange reached, the shortest path from the seed and a bottleneck
    estimate of the amount that can have reached it along that path."""
    from .labels import vasp_registry

    registry = vasp_registry()
    best: dict[str, dict] = {}
    seed_out = [e["first_ts"] for _, _, e in g.out_edges(seed, data=True)]
    start_ts = min(seed_out) if seed_out else None
    for node, data in g.nodes(data=True):
        if data.get("type") not in {"deposit", "exchange"}:
            continue
        try:
            path = nx.shortest_path(g, seed, node)
        except nx.NetworkXNoPath:
            continue
        # the deposit address is what the exchange can freeze / identify
        entity = data["entity"]
        edges = list(zip(path, path[1:]))
        bottleneck = min(g.edges[e]["amount"] for e in edges)
        arrival = g.edges[edges[-1]]["first_ts"]
        cand = {
            "entity": entity,
            "name": registry.get(entity, {}).get("name", entity.title()),
            "address": node,
            "address_type": data["type"],
            "hops": len(path) - 1,
            "path": path,
            "amount_estimate": round(bottleneck, 2),
            "token": g.edges[edges[-1]]["token"],
            "arrived_ts": arrival,
            "minutes_from_seed": round((arrival - start_ts) / 60) if start_ts else None,
            "label_source": data.get("label_source", ""),
        }
        prev = best.get(entity)
        # prefer the deposit address (identifies the customer) over the hot wallet
        if (prev is None or cand["hops"] < prev["hops"]
                or (prev["address_type"] == "exchange" and cand["address_type"] == "deposit")):
            best[entity] = cand
    return sorted(best.values(), key=lambda v: (v["hops"], -v["amount_estimate"]))
