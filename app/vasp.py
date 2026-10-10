"""VASP law-enforcement contact discovery with the TinyFish Web Agent."""
from __future__ import annotations

from . import db
from .evidence import EvidenceLocker
from .labels import vasp_registry
from .tinyfish import TinyFishClient, TinyFishError

GOAL = (
    "You are helping a police cyber-crime investigator. On this cryptocurrency exchange's "
    "website, find the official process for law-enforcement or government agencies to request "
    "user information or to freeze funds. Navigate legal, support, help-centre or compliance "
    "pages as needed. Do NOT log in or submit any form. Return JSON with keys: "
    "portal_url (page or form where requests are submitted), process_steps (list of strings), "
    "contact_email (string or null), required_documents (list of strings), "
    "response_time (string or null), source_urls (list of pages you used)."
)


async def discover_contact(tf: TinyFishClient, entity: str, evidence: EvidenceLocker,
                           log=lambda m: None) -> dict:
    reg = vasp_registry().get(entity)
    name = reg["name"] if reg else entity.title()
    if not tf.simulated and (cached := db.vasp_cache_get(entity)):
        log(f"{name}: using cached contact (agent ran within 7 days)")
        return {**cached, "cached": True}

    start_url = reg["start_url"] if reg else None
    if not start_url:
        # Not in the registry yet: let Search find a starting point.
        try:
            res = await tf.search(f"{name} law enforcement request portal")
            start_url = res[0]["url"] if res else None
        except TinyFishError:
            start_url = None
    if not start_url:
        return {"entity": entity, "name": name, "manual_review": True,
                "error": "No starting URL known for this VASP"}

    log(f"{name}: agent navigating {start_url}")
    try:
        out = await tf.agent(start_url, GOAL)
        result = out["result"] or {}
        contact = {"entity": entity, "name": name, "start_url": start_url,
                   "streaming_url": out.get("streaming_url"), **result,
                   "manual_review": False, "simulated": tf.simulated}
    except TinyFishError as exc:
        fb = (reg or {}).get("fallback", {})
        log(f"{name}: agent failed, using registry fallback ({exc})")
        return {"entity": entity, "name": name, "start_url": start_url, **fb,
                "error": str(exc), "manual_review": True, "simulated": tf.simulated}

    portal = contact.get("portal_url") or start_url
    try:
        page = await tf.fetch([portal])
        text = next((p.get("text") for p in page.get("results", []) if p.get("text")), "")
        if text:
            ev = evidence.capture(portal, text, purpose=f"{name} law-enforcement process page")
            contact["evidence_id"] = ev["id"]
    except TinyFishError as exc:
        log(f"{name}: evidence snapshot failed ({exc})")
    if reg:
        contact["jurisdiction"] = reg.get("jurisdiction")
    if not tf.simulated:
        db.vasp_cache_put(entity, contact)
    return contact
