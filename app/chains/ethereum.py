"""Ethereum via the Etherscan V2 multichain API (native ETH + major stablecoins)."""
from __future__ import annotations

import httpx

from .base import ChainProvider, Transfer

API = "https://api.etherscan.io/v2/api"
TRACKED_TOKENS = {"USDT", "USDC", "DAI", "WETH"}


class EthereumProvider(ChainProvider):
    chain = "ethereum"

    def __init__(self, api_key: str):
        if not api_key:
            raise RuntimeError("ETHERSCAN_API_KEY is required for live Ethereum tracing")
        self.api_key = api_key

    async def _call(self, action: str, address: str) -> list[dict]:
        params = {"chainid": 1, "module": "account", "action": action, "address": address,
                  "page": 1, "offset": 200, "sort": "asc", "apikey": self.api_key}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(API, params=params)
            r.raise_for_status()
            data = r.json()
        if data.get("status") != "1":
            # "No transactions found" is status 0 with an empty list
            if isinstance(data.get("result"), list):
                return []
            raise RuntimeError(f"Etherscan error: {data.get('message')} {data.get('result')}")
        return data["result"]

    async def _transfers(self, address: str) -> list[Transfer]:
        out: list[Transfer] = []
        for tx in await self._call("txlist", address):
            value = int(tx.get("value", "0")) / 1e18
            if value <= 0 or tx.get("isError") == "1" or not tx.get("to"):
                continue
            out.append(Transfer(tx["hash"], tx["from"].lower(), tx["to"].lower(), value, "ETH",
                                int(tx["timeStamp"]), self.chain))
        for tx in await self._call("tokentx", address):
            symbol = tx.get("tokenSymbol", "")
            if symbol not in TRACKED_TOKENS:
                continue  # ignore spam / address-poisoning tokens
            value = int(tx["value"]) / 10 ** int(tx.get("tokenDecimal") or 18)
            if value <= 0:
                continue
            out.append(Transfer(tx["hash"], tx["from"].lower(), tx["to"].lower(), value, symbol,
                                int(tx["timeStamp"]), self.chain))
        return out

    async def outgoing(self, address: str, since_ts: int = 0) -> list[Transfer]:
        return [t for t in await self._transfers(address)
                if t.sender == address and t.timestamp >= since_ts]

    async def incoming(self, address: str) -> list[Transfer]:
        txs = [t for t in await self._transfers(address) if t.receiver == address]
        return sorted(txs, key=lambda t: -t.timestamp)[:100]
