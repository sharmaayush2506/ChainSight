"""Smoke-test the live integrations with your real keys before a demo.

    python scripts/check_live.py                      # checks TinyFish + chain APIs
    python scripts/check_live.py --tron T... --agent  # also traces a real address / runs one agent

Reads keys from .env. Each check prints OK / FAIL with timing, so you can quote
measured numbers in the deck.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.chains import EthereumProvider, TronProvider  # noqa: E402
from app.config import settings  # noqa: E402
from app.tinyfish import TinyFishClient  # noqa: E402

BINANCE_TRON = "TNXoiAJ3dct8Fjg4M9fkLFh9S2v9TXc32G"
BINANCE_ETH = "0x28c6c06298d514db089934071355e5743bf21d60"


async def check(name: str, coro):
    t = time.monotonic()
    try:
        detail = await coro
        print(f"  OK    {name:<34} {time.monotonic() - t:6.1f}s  {detail}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  {name:<34} {time.monotonic() - t:6.1f}s  {type(exc).__name__}: {exc}")
        return False


async def main(args) -> int:
    results = []
    print("Blockchain data")
    tron = TronProvider(settings.trongrid_api_key)
    results.append(await check("TronGrid outgoing transfers",
                               _count(tron.outgoing(args.tron or BINANCE_TRON))))
    if settings.etherscan_api_key:
        eth = EthereumProvider(settings.etherscan_api_key)
        results.append(await check("Etherscan V2 transfers", _count(eth.outgoing(BINANCE_ETH))))
    else:
        print("  SKIP  Etherscan (ETHERSCAN_API_KEY not set)")

    print("TinyFish")
    if not settings.tinyfish_api_key:
        print("  SKIP  TINYFISH_API_KEY not set")
    else:
        tf = TinyFishClient(settings.tinyfish_api_key, retries=0)
        results.append(await check("Search", _count(tf.search(f'"{BINANCE_TRON}"'))))
        results.append(await check("Fetch", _fetch(tf)))
        if args.agent:
            results.append(await check("Web Agent (Binance LE page)", _agent(tf)))
        else:
            print("  SKIP  Web Agent (pass --agent; it is metered)")
    return 0 if all(results) else 1


async def _count(coro):
    items = await coro
    return f"{len(items)} item(s)"


async def _fetch(tf):
    out = await tf.fetch(["https://www.binance.com/en/support/law-enforcement"])
    pages = out.get("results", [])
    return f"{len(pages)} page(s), {sum(len(p.get('text') or '') for p in pages)} chars, " \
           f"{len(out.get('errors', []))} error(s)"


async def _agent(tf):
    from app.vasp import GOAL
    out = await tf.agent("https://www.binance.com/en/support/law-enforcement", GOAL)
    return f"portal_url={(out['result'] or {}).get('portal_url')!r}"


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tron", help="a Tron address to fetch transfers for")
    p.add_argument("--agent", action="store_true", help="also run one (metered) agent task")
    sys.exit(asyncio.run(main(p.parse_args())))
