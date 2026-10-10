"""Runtime settings, read from environment variables (and a local .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
VAR_DIR = Path(os.getenv("CHAINSIGHT_VAR_DIR", ROOT / "var"))


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    mode: str = field(default_factory=lambda: os.getenv("CHAINSIGHT_MODE", "auto").lower())
    tinyfish_api_key: str = field(default_factory=lambda: os.getenv("TINYFISH_API_KEY", ""))
    etherscan_api_key: str = field(default_factory=lambda: os.getenv("ETHERSCAN_API_KEY", ""))
    trongrid_api_key: str = field(default_factory=lambda: os.getenv("TRONGRID_API_KEY", ""))
    api_key: str = field(default_factory=lambda: os.getenv("CHAINSIGHT_API_KEY", ""))

    max_hops: int = field(default_factory=lambda: _int("TRACE_MAX_HOPS", 8))
    max_branch: int = field(default_factory=lambda: _int("TRACE_MAX_BRANCH", 4))
    max_nodes: int = field(default_factory=lambda: _int("TRACE_MAX_NODES", 80))

    tinyfish_retries: int = field(default_factory=lambda: _int("TINYFISH_RETRIES", 2))
    agent_timeout_s: int = field(default_factory=lambda: _int("TINYFISH_AGENT_TIMEOUT", 240))

    db_path: Path = VAR_DIR / "chainsight.db"
    evidence_dir: Path = VAR_DIR / "evidence"

    def chain_live(self, chain: str) -> bool:
        """Whether on-chain data for this chain comes from a real API."""
        if self.mode == "demo":
            return False
        if self.mode == "live":
            return True
        # auto: Tron works keyless (rate limited); Ethereum needs an Etherscan key
        return chain == "tron" or bool(self.etherscan_api_key)

    @property
    def tinyfish_live(self) -> bool:
        if self.mode == "demo":
            return False
        return bool(self.tinyfish_api_key)


settings = Settings()
VAR_DIR.mkdir(parents=True, exist_ok=True)
settings.evidence_dir.mkdir(parents=True, exist_ok=True)
