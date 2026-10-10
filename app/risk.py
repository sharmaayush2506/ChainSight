"""Rule-based risk scoring. The score is a LEAD for a human investigator, not proof."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

import networkx as nx

TYPOLOGY_NAMES = {
    "investment_scam": "Investment scam",
    "task_scam": "Task-based / job scam",
    "sextortion": "Sextortion",
    "phishing": "Phishing / wallet drainer",
    "ransomware": "Ransomware",
}


def score(g: nx.DiGraph, seed: str, patterns: list[dict], vasps: list[dict],
          intel: dict) -> dict:
    reasons: list[tuple[int, str]] = []
    kinds = Counter(p["type"] for p in patterns)

    confirmed = [m for m in intel.get("mentions", []) if m.get("confirmed")]
    if confirmed:
        reasons.append((min(30, 15 * len(confirmed)),
                        f"{len(confirmed)} public report(s) mention this wallet"))

    seed_sanction = g.nodes[seed].get("sanctioned")
    if seed_sanction:
        reasons.append((40, f"Wallet is on {seed_sanction['list']}"))
    exposed = [n for n, d in g.nodes(data=True) if d.get("sanctioned") and n != seed]
    if exposed:
        reasons.append((25, f"Funds touched {len(exposed)} sanctioned address(es)"))

    passes = next((p for p in patterns if p["type"] == "pass_through"), None)
    if passes:
        reasons.append((10, f"{len(passes['addresses'])} fast pass-through wallet(s) (layering)"))
    if kinds["fan_out"]:
        reasons.append((10, "Fan-out: funds split across several wallets"))
    if kinds["fan_in"]:
        reasons.append((10, "Fan-in: wallet collects from many senders"))
    if kinds["peel_chain"]:
        reasons.append((15, "Peel chain detected"))

    risky = [d for _, d in g.nodes(data=True) if d.get("type") in {"mixer", "bridge"}]
    if risky:
        reasons.append((10, f"Exposure to {', '.join(sorted({d['type'] for d in risky}))}"))

    if vasps and vasps[0].get("minutes_from_seed") is not None \
            and vasps[0]["minutes_from_seed"] <= 24 * 60:
        reasons.append((10, f"Reached {vasps[0]['name']} within "
                            f"{vasps[0]['minutes_from_seed'] / 60:.1f} h of leaving the wallet"))

    total = min(100, sum(p for p, _ in reasons))
    level = "High" if total >= 70 else "Medium" if total >= 40 else "Low"

    votes: Counter = Counter()
    for m in confirmed or intel.get("mentions", []):
        votes.update(m.get("typology", {}))
    typology = TYPOLOGY_NAMES.get(votes.most_common(1)[0][0]) if votes else None

    return {
        "score": total,
        "level": level,
        "category": typology or "Unknown (no public reports found)",
        "reasons": [{"points": p, "reason": r} for p, r in reasons],
        "disclaimer": "Lead for investigation only. A human investigator must review the "
                      "evidence; this score is not proof of guilt.",
    }


def next_actions(g: nx.DiGraph, vasps: list[dict], contacts: dict[str, dict]) -> list[str]:
    actions = []
    for v in vasps:
        c = contacts.get(v["entity"], {})
        where = c.get("portal_url") or c.get("contact_email") or "their law-enforcement channel"
        actions.append(
            f"Send a freeze + KYC disclosure request to {v['name']} via {where} for "
            f"{'deposit address' if v['address_type'] == 'deposit' else 'wallet'} {v['address']} "
            f"(≈{v['amount_estimate']:,.0f} {v['token']}, {v['hops']} hop(s) from the reported wallet).")
    if any(d.get("frontier") for _, d in g.nodes(data=True)):
        actions.append("Some branches hit the hop/size limit: extend the trace on those addresses.")
    if any(d.get("type") == "bridge" for _, d in g.nodes(data=True)):
        actions.append("Funds entered a cross-chain bridge: continue tracing on the destination "
                       "chain (manual for now, on the roadmap).")
    if any(c.get("manual_review") for c in contacts.values()):
        actions.append("Verify VASP contact details marked 'manual review' before sending.")
    if not vasps:
        actions.append("No exchange identified within the hop limit: monitor the wallet and "
                       "re-run when funds move.")
    return actions


def freeze_request(case: dict) -> str:
    """Draft text of a freeze / disclosure request for the nearest VASP."""
    if not case.get("vasps"):
        return ""
    v = case["vasps"][0]
    contact = case.get("contacts", {}).get(v["entity"], {})
    edges = case["graph"]["edges"]
    path = v["path"]
    txs = [h for e in edges for h in e["tx_hashes"]
           if (e["source"], e["target"]) in set(zip(path, path[1:]))]
    when = datetime.fromtimestamp(v["arrived_ts"], timezone.utc).strftime("%d %b %Y %H:%M UTC")
    tx_lines = "\n".join(f"  - {h}" for h in txs)
    return f"""To: {v['name']} - Law Enforcement / Compliance Team
Via: {contact.get('portal_url') or contact.get('contact_email') or '[verify contact]'}
Subject: URGENT - Request to freeze funds and disclose KYC - Ref {case.get('complaint_ref') or '[complaint no.]'}

Dear Compliance Team,

This office is investigating a reported cyber-fraud ({case['risk']['category']}). Funds from the
reported wallet {case['address']} ({case['chain'].title()}) were traced on-chain to an address on
your platform:

  Address on your platform : {v['address']}
  Approx. amount           : {v['amount_estimate']:,.2f} {v['token']}
  Arrived                  : {when}
  Hops from reported wallet: {v['hops']}
  Transaction path:
{tx_lines}

We request that you (1) place an immediate hold on the account(s) linked to this address and
any remaining balance, and (2) disclose the KYC details, login IP logs, linked bank / UPI
accounts and withdrawal history for the account(s).

The formal notice under the applicable law [e.g. Sec. 94 BNSS / Sec. 91 CrPC] is attached.
Supporting evidence (fund-flow report, hashed page snapshots) is enclosed.

[Officer name, designation]
[Unit / Police station, official email, phone]
"""
