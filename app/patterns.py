"""Detection of common laundering shapes on the traced graph."""
from __future__ import annotations

import networkx as nx

PASS_THROUGH_RATIO = 0.9      # >= 90% of what came in went out again
PASS_THROUGH_DWELL_H = 24     # ... within this many hours
FAN_DEGREE = 3                # >= 3 distinct counterparties
FAN_WINDOW_H = 6
PEEL_MAIN_SHARE = 0.85        # the "main" output keeps >= 85% of the value
PEEL_MIN_LENGTH = 3           # >= 3 consecutive peel hops

SKIP_TYPES = {"exchange", "deposit", "mixer", "bridge", "source"}


def _io(g: nx.DiGraph, node: str):
    ins = [e for _, _, e in g.in_edges(node, data=True)]
    outs = [(dst, e) for _, dst, e in g.out_edges(node, data=True)]
    return ins, outs


def detect_patterns(g: nx.DiGraph) -> list[dict]:
    found: list[dict] = []
    passes: list[tuple[str, float]] = []
    for node, data in g.nodes(data=True):
        if data.get("type") in SKIP_TYPES:
            continue
        ins, outs = _io(g, node)
        in_total = sum(e["amount"] for e in ins)
        out_total = sum(e["amount"] for _, e in outs)

        if ins and outs and in_total and out_total / in_total >= PASS_THROUGH_RATIO:
            dwell_h = (max(e["last_ts"] for _, e in outs) - min(e["first_ts"] for e in ins)) / 3600
            if dwell_h <= PASS_THROUGH_DWELL_H:
                passes.append((node, dwell_h))

        if len(outs) >= FAN_DEGREE:
            span_h = (max(e["first_ts"] for _, e in outs) - min(e["first_ts"] for _, e in outs)) / 3600
            if span_h <= FAN_WINDOW_H:
                found.append({
                    "type": "fan_out", "addresses": [node] + [d for d, _ in outs],
                    "detail": f"split into {len(outs)} wallets within {span_h:.1f} h",
                })

        senders = {s for s, _ in g.in_edges(node)}
        if len(senders) >= FAN_DEGREE:
            found.append({
                "type": "fan_in", "addresses": [node] + sorted(senders),
                "detail": f"received from {len(senders)} distinct wallets "
                          f"({in_total:,.0f} total)",
            })

    if passes:
        passes.sort(key=lambda x: x[1])
        found.insert(0, {
            "type": "pass_through", "addresses": [a for a, _ in passes],
            "detail": f"{len(passes)} wallet(s) forwarded >= {PASS_THROUGH_RATIO:.0%} of what "
                      f"they received within {PASS_THROUGH_DWELL_H} h "
                      f"(fastest {passes[0][1]:.1f} h)",
        })
    found.extend(_peel_chains(g))
    for p in found:
        # fan patterns are anchored on their hub; pass-through and peel tag every wallet
        tagged = p["addresses"][:1] if p["type"] in {"fan_in", "fan_out"} else p["addresses"]
        for a in tagged:
            g.nodes[a].setdefault("patterns", []).append(p["type"])
    return found


def _peel_step(g: nx.DiGraph, node: str) -> str | None:
    """If `node` keeps most value moving to one address and peels small amounts
    off to others, return the main next address."""
    if g.nodes[node].get("type") in SKIP_TYPES:
        return None
    outs = [(dst, e["amount"]) for _, dst, e in g.out_edges(node, data=True)]
    if len(outs) < 2:
        return None
    total = sum(a for _, a in outs)
    dst, amt = max(outs, key=lambda x: x[1])
    return dst if total and amt / total >= PEEL_MAIN_SHARE else None


def _peel_chains(g: nx.DiGraph) -> list[dict]:
    chains, used = [], set()
    starts = [n for n in g.nodes if _peel_step(g, n)]
    # begin only at chain heads (no peel step leads into them)
    heads = [n for n in starts if not any(_peel_step(g, p) == n for p in g.predecessors(n))]
    for head in heads:
        path, node = [head], head
        while (nxt := _peel_step(g, node)) and nxt not in path:
            path.append(nxt)
            node = nxt
        peel_hops = len(path) - 1
        if peel_hops >= PEEL_MIN_LENGTH and head not in used:
            used.update(path)
            peeled = sum(e["amount"] for n in path[:-1]
                         for _, d, e in g.out_edges(n, data=True) if d not in path)
            chains.append({
                "type": "peel_chain", "addresses": path,
                "detail": f"{peel_hops} consecutive hops, {peeled:,.0f} peeled off to side wallets",
            })
    return chains
