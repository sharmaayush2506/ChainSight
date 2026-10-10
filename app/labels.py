"""Known-entity labels (exchange clusters, mixers, bridges) and sanctions lists."""
from __future__ import annotations

import json
from functools import lru_cache

from .config import DATA_DIR


@lru_cache
def _labels() -> dict[str, dict[str, dict]]:
    raw = json.loads((DATA_DIR / "labels.json").read_text())
    return {chain: {addr if chain == "tron" else addr.lower(): v for addr, v in entries.items()}
            for chain, entries in raw.items() if not chain.startswith("_")}


@lru_cache
def _sanctions() -> dict[str, dict]:
    raw = json.loads((DATA_DIR / "sanctions.json").read_text())
    return {e["address"].lower(): e for e in raw["entries"]}


@lru_cache
def vasp_registry() -> dict[str, dict]:
    raw = json.loads((DATA_DIR / "vasps.json").read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


class LabelStore:
    def __init__(self, chain: str, extra: dict[str, dict] | None = None):
        self.chain = chain
        self.labels = dict(_labels().get(chain, {}))
        self.labels.update(extra or {})

    def get(self, address: str) -> dict | None:
        key = address.lower() if self.chain == "ethereum" else address
        return self.labels.get(key)


def sanctions_hit(address: str) -> dict | None:
    return _sanctions().get(address.lower())
