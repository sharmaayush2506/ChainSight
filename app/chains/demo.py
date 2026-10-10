"""Synthetic but realistic laundering scenario, generated deterministically from the
seed address. Lets the whole pipeline run offline (no API keys) for demos and tests.

Shape of the scenario (amounts in USDT):

  victims --(fan-in)--> SEED --(fast pass-through)--> P1 --(fan-out)--> M1, M2, M3
  M1 --peel--> K1 --peel--> K2 --peel--> K3 --> DEP_BINANCE --(sweep)--> Binance hot wallet
  M2 --> DEP_OKX --(sweep)--> OKX hot wallet
  M3 --> BRIDGE (cross-chain bridge contract; tracing stops, flagged for roadmap)
"""
from __future__ import annotations

import hashlib
import time

from .base import ChainProvider, Transfer

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

DEMO_SEEDS = {
    "tron": "TDemoScamWa11etXqR7pLk2Vn9sHbYcEu4",
    "ethereum": "0xdeadbeef5ca10000000000000000000000000001",
}


def _addr(chain: str, seed: str, name: str) -> str:
    digest = hashlib.sha256(f"{seed}:{name}".encode()).digest()
    if chain == "ethereum":
        return "0x" + digest.hex()[:40]
    n = int.from_bytes(digest, "big")
    chars = []
    while len(chars) < 33:
        n, r = divmod(n, 58)
        chars.append(_B58[r])
    return "T" + "".join(chars)


def _tx(seed: str, i: int, chain: str) -> str:
    h = hashlib.sha256(f"{seed}:tx:{i}".encode()).hexdigest()
    return "0x" + h if chain == "ethereum" else h


class DemoProvider(ChainProvider):
    is_demo = True

    def __init__(self, chain: str):
        self.chain = chain
        self._cache: dict[str, tuple[list[Transfer], dict[str, dict]]] = {}
        self._seed: str | None = None

    def _scenario(self, seed: str) -> tuple[list[Transfer], dict[str, dict]]:
        if seed in self._cache:
            return self._cache[seed]
        c = self.chain
        a = lambda name: _addr(c, seed, name)  # noqa: E731
        token = "USDT"
        t0 = int(time.time()) - 3 * 86400  # three days ago
        minute = 60
        flows: list[tuple[str, str, float, int]] = [
            # fan-in from victims
            (a("victim1"), seed, 5_000, t0),
            (a("victim2"), seed, 12_000, t0 + 40 * minute),
            (a("victim3"), seed, 8_000, t0 + 95 * minute),
            # fast pass-through: everything leaves within ~15 minutes
            (seed, a("P1"), 24_950, t0 + 110 * minute),
            # fan-out to three mules
            (a("P1"), a("M1"), 8_200, t0 + 125 * minute),
            (a("P1"), a("M2"), 9_000, t0 + 127 * minute),
            (a("P1"), a("M3"), 7_700, t0 + 131 * minute),
            # peel chain M1 -> K1 -> K2 -> K3 with small side "peels"
            (a("M1"), a("K1"), 7_900, t0 + 4 * 60 * minute),
            (a("M1"), a("peel1"), 290, t0 + 4 * 60 * minute + 2 * minute),
            (a("K1"), a("K2"), 7_550, t0 + 6 * 60 * minute),
            (a("K1"), a("peel2"), 340, t0 + 6 * 60 * minute + 3 * minute),
            (a("K2"), a("K3"), 7_200, t0 + 9 * 60 * minute),
            (a("K2"), a("peel3"), 335, t0 + 9 * 60 * minute + 1 * minute),
            (a("K3"), a("DEP_BINANCE"), 7_180, t0 + 11 * 60 * minute),
            # exchange deposit address sweeps into the exchange hot wallet
            (a("DEP_BINANCE"), a("HOT_BINANCE"), 7_180, t0 + 11 * 60 * minute + 20 * minute),
            (a("M2"), a("DEP_OKX"), 8_990, t0 + 5 * 60 * minute),
            (a("DEP_OKX"), a("HOT_OKX"), 8_990, t0 + 5 * 60 * minute + 12 * minute),
            (a("M3"), a("BRIDGE"), 7_690, t0 + 7 * 60 * minute),
        ]
        transfers = [Transfer(_tx(seed, i, c), s, r, amt, token, ts, c)
                     for i, (s, r, amt, ts) in enumerate(flows)]
        labels = {
            a("HOT_BINANCE"): {"entity": "binance", "name": "Binance hot wallet (demo)",
                               "type": "exchange", "source": "DEMO synthetic label"},
            a("HOT_OKX"): {"entity": "okx", "name": "OKX hot wallet (demo)",
                           "type": "exchange", "source": "DEMO synthetic label"},
            a("BRIDGE"): {"entity": "bridge", "name": "Cross-chain bridge (demo)",
                          "type": "bridge", "source": "DEMO synthetic label"},
        }
        self._cache[seed] = (transfers, labels)
        return transfers, labels

    def bind(self, seed: str) -> None:
        """Generate the scenario around the reported wallet."""
        self._seed = seed
        self._scenario(seed)

    async def outgoing(self, address: str, since_ts: int = 0) -> list[Transfer]:
        transfers, _ = self._scenario(self._seed)
        return [t for t in transfers if t.sender == address and t.timestamp >= since_ts]

    async def incoming(self, address: str) -> list[Transfer]:
        transfers, _ = self._scenario(self._seed)
        return sorted([t for t in transfers if t.receiver == address], key=lambda t: -t.timestamp)

    def extra_labels(self) -> dict[str, dict]:
        return self._scenario(self._seed)[1]
