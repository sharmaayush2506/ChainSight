"""Off-chain intelligence (TinyFish Search + Fetch + Agent)."""
from __future__ import annotations

import asyncio
import re

from .evidence import EvidenceLocker
from .tinyfish import TinyFishClient, TinyFishError

TYPOLOGIES = {
    "investment_scam": ["invest", "trading", "profit", "returns", "pig butcher", "withdraw"],
    "task_scam": ["task", "part-time", "part time", "job", "like and review", "commission",
                  "telegram"],
    "sextortion": ["sextortion", "nude", "intimate", "video call", "blackmail"],
    "phishing": ["phishing", "drainer", "seed phrase", "airdrop", "fake site", "approve"],
    "ransomware": ["ransomware", "ransom", "decrypt"],
}

MAX_PAGES = 4


def classify(text: str) -> dict[str, int]:
    low = text.lower()
    return {t: sum(low.count(k) for k in kws) for t, kws in TYPOLOGIES.items()
            if any(k in low for k in kws)}


def _context(text: str, needle: str, width: int = 220) -> str:
    m = re.search(re.escape(needle), text, re.IGNORECASE)
    if not m:
        return ""
    s = max(0, m.start() - width)
    return ("…" if s else "") + text[s:m.end() + width].replace("\n", " ").strip() + "…"


async def wallet_mentions(tf: TinyFishClient, address: str, evidence: EvidenceLocker,
                          log=lambda m: None) -> dict:
    queries = [f'"{address}"', f"{address} scam", f"{address} fraud report"]
    results = await asyncio.gather(*(tf.search(q) for q in queries), return_exceptions=True)
    hits, errors = {}, []
    for q, res in zip(queries, results):
        if isinstance(res, Exception):
            errors.append(f"search '{q}': {res}")
            continue
        for r in res:
            if r.get("url") and r["url"] not in hits:
                hits[r["url"]] = {"title": r.get("title"), "url": r["url"],
                                  "site": r.get("site_name"), "snippet": r.get("snippet"),
                                  "query": q}
    log(f"Search: {len(hits)} candidate page(s) from {len(queries)} queries")

    mentions = []
    candidates = list(hits.values())[:MAX_PAGES]
    if candidates:
        try:
            fetched = await tf.fetch([c["url"] for c in candidates])
            pages = {p.get("url"): p for p in fetched.get("results", [])}
            errors += [f"fetch: {e}" for e in fetched.get("errors", [])]
        except TinyFishError as exc:
            pages = {}
            errors.append(str(exc))
        for c in candidates:
            page = pages.get(c["url"])
            text = (page or {}).get("text") or ""
            confirmed = address.lower() in text.lower()
            m = {**c, "confirmed": confirmed, "typology": classify(text + " " + (c["snippet"] or "")),
                 "context": _context(text, address) or c["snippet"]}
            if page and confirmed:
                ev = evidence.capture(c["url"], text, final_url=page.get("final_url"),
                                      purpose="Public report mentioning the wallet")
                m["evidence_id"] = ev["id"]
            mentions.append(m)
    log(f"Fetch: {sum(m['confirmed'] for m in mentions)} page(s) confirmed to mention the wallet")
    return {"mentions": mentions, "errors": errors}


async def p2p_seller_profile(tf: TinyFishClient, url: str, evidence: EvidenceLocker) -> dict:
    goal = ("This is a peer-to-peer crypto marketplace page linked to a fraud investigation. "
            "Without logging in or trading, extract the seller's public profile. Return JSON with "
            "keys: seller_name, completed_trades, joined, payment_methods (list), "
            "payment_details_visible, listing_price, other_notes.")
    try:
        out = await tf.agent(url, goal)
        evidence.capture(url, str(out["result"]), purpose="P2P seller profile",
                         method="TinyFish Web Agent")
        return {"url": url, "profile": out["result"], "streaming_url": out.get("streaming_url")}
    except TinyFishError as exc:
        return {"url": url, "error": str(exc), "manual_review": True}
