"""TinyFish client: Search, Fetch and Web Agent over the public REST API.

Endpoints (one API key, header `X-API-Key`):
  Search  GET  https://api.search.tinyfish.ai?query=...
  Fetch   POST https://api.fetch.tinyfish.ai              {"urls": [...], "format": "markdown"}
  Agent   POST https://agent.tinyfish.ai/v1/automation/run-sse   {"url": ..., "goal": ...}

Agents are slower than APIs and can fail on unusual sites, so every call is retried
with backoff and failures are surfaced to the investigator for manual follow-up.
`SimulatedTinyFish` mirrors the interface with canned replies for offline demos.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from .config import settings

SEARCH_URL = "https://api.search.tinyfish.ai"
FETCH_URL = "https://api.fetch.tinyfish.ai"
AGENT_URL = "https://agent.tinyfish.ai/v1/automation/run-sse"


class TinyFishError(RuntimeError):
    pass


class TinyFishClient:
    simulated = False

    def __init__(self, api_key: str, retries: int = 2):
        self.api_key = api_key
        self.retries = retries
        self.calls: list[dict] = []  # audit trail of every call (also gives measured timings)

    @property
    def _headers(self) -> dict:
        return {"X-API-Key": self.api_key, "Content-Type": "application/json"}

    async def _with_retries(self, kind: str, target: str, fn):
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            started = time.monotonic()
            try:
                result = await fn()
                self._record(kind, target, started, True, attempt=attempt)
                return result
            except Exception as exc:  # noqa: BLE001 - surfaced to the investigator
                last = exc
                self._record(kind, target, started, False, error=str(exc)[:200], attempt=attempt)
                if attempt < self.retries:
                    await asyncio.sleep(2 ** (attempt + 1))
        raise TinyFishError(f"{kind} failed after {self.retries + 1} attempts: {last}")

    def _record(self, kind, target, started, ok, **extra):
        self.calls.append({"kind": kind, "target": target, "ok": ok,
                           "ms": round((time.monotonic() - started) * 1000),
                           "at": int(time.time()), **extra})

    async def search(self, query: str) -> list[dict]:
        async def go():
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(SEARCH_URL, params={"query": query}, headers=self._headers)
                r.raise_for_status()
                return r.json().get("results", [])
        return await self._with_retries("search", query, go)

    async def fetch(self, urls: list[str], fmt: str = "markdown") -> dict:
        async def go():
            async with httpx.AsyncClient(timeout=150) as c:
                r = await c.post(FETCH_URL, json={"urls": urls, "format": fmt},
                                 headers=self._headers)
                r.raise_for_status()
                return r.json()
        return await self._with_retries("fetch", ", ".join(urls)[:200], go)

    async def agent(self, url: str, goal: str, on_event=None) -> dict:
        """Run a web agent and return {"result": ..., "streaming_url": ...}."""
        async def go():
            streaming_url = None
            timeout = httpx.Timeout(settings.agent_timeout_s, connect=30)
            async with httpx.AsyncClient(timeout=timeout) as c:
                async with c.stream("POST", AGENT_URL, json={"url": url, "goal": goal},
                                    headers=self._headers) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        etype = event.get("type")
                        if etype == "STREAMING_URL":
                            streaming_url = event.get("streaming_url") or event.get("streamingUrl")
                        if on_event and etype in {"PROGRESS", "STREAMING_URL"}:
                            on_event(event)
                        if etype == "ERROR":
                            raise TinyFishError(event.get("message") or str(event))
                        if etype == "COMPLETE":
                            if event.get("status") not in (None, "COMPLETED"):
                                raise TinyFishError(f"agent ended with {event.get('status')}: "
                                                    f"{event.get('error')}")
                            result = event.get("result", event.get("resultJson"))
                            return {"result": _as_json(result), "streaming_url": streaming_url}
            raise TinyFishError("agent stream ended without a COMPLETE event")
        return await self._with_retries("agent", url, go)


def _as_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {"text": value}
    return value


class SimulatedTinyFish(TinyFishClient):
    """Offline stand-in with canned, clearly-labelled replies (demo mode only)."""
    simulated = True

    def __init__(self, seed_address: str = ""):
        super().__init__(api_key="", retries=0)
        self.seed = seed_address

    async def search(self, query: str) -> list[dict]:
        async def go():
            await asyncio.sleep(0.4)
            if self.seed not in query:
                return []
            return [
                {"position": 1, "site_name": "Scam report site (demo)",
                 "title": f"Reported address {self.seed[:10]}… — task-based job scam",
                 "url": f"https://scam-reports.example/address/{self.seed}",
                 "snippet": f"Victim was added to a Telegram 'prepaid task' group, asked to deposit "
                            f"USDT to {self.seed} to unlock commissions."},
                {"position": 2, "site_name": "Community forum (demo)",
                 "title": "Lost 12,000 USDT in fake investment app — wallet inside",
                 "url": "https://forum.example/t/fake-investment-app-usdt/4821",
                 "snippet": f"After withdrawing was blocked they asked for a 'tax' payment to "
                            f"{self.seed}. Investment platform was a clone site."},
            ]
        return await self._with_retries("search", query, go)

    async def fetch(self, urls: list[str], fmt: str = "markdown") -> dict:
        async def go():
            await asyncio.sleep(0.6)
            results = []
            for u in urls:
                if "scam-reports" in u:
                    text = (f"# Address report\n\nAddress: {self.seed}\nCategory: Task scam / "
                            f"job fraud\nReports: 3\n\nVictim reports: joined a part-time 'like "
                            f"and review' job on Telegram, paid in USDT (TRC-20) to {self.seed} "
                            f"for 'premium tasks', withdrawal never allowed.")
                elif "forum" in u:
                    text = (f"# Lost 12,000 USDT in fake investment app\n\nThe app showed fake "
                            f"profits; to withdraw I had to pay a fee to {self.seed}. Sharing so "
                            f"others can avoid it. Investment scam, crypto trading.")
                else:
                    text = "Law enforcement requests: submit via the official request portal. " \
                           "Requests must come from an official government email domain."
                results.append({"url": u, "final_url": u, "title": "(demo page)", "text": text})
            return {"results": results, "errors": []}
        return await self._with_retries("fetch", ", ".join(urls)[:200], go)

    async def agent(self, url: str, goal: str, on_event=None) -> dict:
        async def go():
            for step in ("Opening site", "Looking for legal / law-enforcement section",
                         "Extracting request process and contact"):
                await asyncio.sleep(0.5)
                if on_event:
                    on_event({"type": "PROGRESS", "purpose": step})
            from .labels import vasp_registry
            entry = next((v for v in vasp_registry().values() if v["start_url"] == url), {})
            fb = entry.get("fallback", {})
            if "seller" in goal.lower():
                return {"result": {
                    "seller_name": "QuickUSDT_Trader (demo)", "completed_trades": 412,
                    "joined": "2024-11", "payment_methods": ["UPI", "IMPS"],
                    "payment_details_visible": "UPI handle partially masked on listing",
                    "listing_price": "₹93.5 / USDT"}, "streaming_url": None}
            return {"result": {
                "portal_url": fb.get("portal_url", url),
                "process_steps": [
                    "Use an official government / police email address",
                    "Submit the request through the law-enforcement portal",
                    "Attach the legal notice (e.g. Sec. 94/BNSS or 91/CrPC) on letterhead",
                    "Include deposit address, transaction hashes and the asset/amount to freeze",
                ],
                "contact_email": None,
                "required_documents": ["Signed legal notice on letterhead",
                                       "Officer ID / designation", "FIR / complaint reference"],
                "response_time": "Not stated publicly",
                "notes": "DEMO reply. Run in live mode to have the TinyFish agent read the "
                         "real page.",
            }, "streaming_url": None}
        return await self._with_retries("agent", url, go)


def make_client(demo: bool, seed: str) -> TinyFishClient:
    if demo or not settings.tinyfish_live:
        return SimulatedTinyFish(seed)
    return TinyFishClient(settings.tinyfish_api_key, retries=settings.tinyfish_retries)
