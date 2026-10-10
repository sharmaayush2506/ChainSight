"""Tron via TronGrid. Traces TRC-20 transfers (USDT-TRC20 is the main scam rail)."""
from __future__ import annotations

import httpx

from .base import ChainProvider, Transfer

API = "https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
TRACKED_TOKENS = {"USDT", "USDC", "USDD", "TUSD"}


class TronProvider(ChainProvider):
    chain = "tron"

    def __init__(self, api_key: str = ""):
        self.headers = {"TRON-PRO-API-KEY": api_key} if api_key else {}

    async def _fetch(self, address: str, direction: str, since_ts: int = 0) -> list[Transfer]:
        params = {"limit": 200, direction: "true", "order_by": "block_timestamp,asc"}
        if since_ts:
            params["min_timestamp"] = since_ts * 1000
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as client:
            r = await client.get(API.format(address=address), params=params)
            r.raise_for_status()
            data = r.json()
        out = []
        for tx in data.get("data", []):
            info = tx.get("token_info", {})
            symbol = info.get("symbol", "")
            if symbol not in TRACKED_TOKENS:
                continue  # skip spam tokens used for address poisoning
            value = int(tx["value"]) / 10 ** int(info.get("decimals", 6))
            if value <= 0:
                continue
            out.append(Transfer(tx["transaction_id"], tx["from"], tx["to"], value, symbol,
                                int(tx["block_timestamp"]) // 1000, self.chain))
        return out

    async def outgoing(self, address: str, since_ts: int = 0) -> list[Transfer]:
        return await self._fetch(address, "only_from", since_ts)

    async def incoming(self, address: str) -> list[Transfer]:
        txs = await self._fetch(address, "only_to")
        return sorted(txs, key=lambda t: -t.timestamp)[:100]
