from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass

_ETH_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_TRON_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")


def is_valid_address(chain: str, address: str) -> bool:
    if chain == "ethereum":
        return bool(_ETH_RE.match(address))
    if chain == "tron":
        return bool(_TRON_RE.match(address))
    return False


def normalize_address(chain: str, address: str) -> str:
    address = address.strip()
    # Ethereum addresses are case-insensitive (checksum is only a typo guard);
    # Tron base58 addresses are case-sensitive.
    return address.lower() if chain == "ethereum" else address


@dataclass
class Transfer:
    tx_hash: str
    sender: str
    receiver: str
    amount: float
    token: str
    timestamp: int  # unix seconds
    chain: str

    def to_dict(self) -> dict:
        return asdict(self)


class ChainProvider(ABC):
    chain: str
    is_demo = False

    @abstractmethod
    async def outgoing(self, address: str, since_ts: int = 0) -> list[Transfer]:
        """Transfers sent by `address` at or after `since_ts`."""

    @abstractmethod
    async def incoming(self, address: str) -> list[Transfer]:
        """Transfers received by `address` (most recent first, capped)."""

    def extra_labels(self) -> dict[str, dict]:
        """Labels the provider knows about (used by the demo dataset)."""
        return {}
