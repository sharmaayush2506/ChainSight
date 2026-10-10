"""Blockchain data providers. Each returns normalized `Transfer` records."""
from __future__ import annotations

from ..config import settings
from .base import ChainProvider, Transfer, is_valid_address, normalize_address
from .demo import DemoProvider
from .ethereum import EthereumProvider
from .tron import TronProvider

SUPPORTED_CHAINS = ("ethereum", "tron")


def get_provider(chain: str, *, demo: bool) -> ChainProvider:
    if chain not in SUPPORTED_CHAINS:
        raise ValueError(f"Unsupported chain: {chain}")
    if demo:
        return DemoProvider(chain)
    if chain == "ethereum":
        return EthereumProvider(settings.etherscan_api_key)
    return TronProvider(settings.trongrid_api_key)


__all__ = ["ChainProvider", "EthereumProvider", "TronProvider", "Transfer", "get_provider", "is_valid_address",
           "normalize_address", "SUPPORTED_CHAINS"]
