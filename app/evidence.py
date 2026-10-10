"""Timestamped, hashed evidence snapshots of every web page the findings rely on."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .config import settings


class EvidenceLocker:
    def __init__(self, case_id: str, simulated: bool):
        self.case_id = case_id
        self.simulated = simulated
        self.dir = settings.evidence_dir / case_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.items: list[dict] = []

    def capture(self, url: str, content: str, *, purpose: str, final_url: str | None = None,
                method: str = "TinyFish Fetch") -> dict:
        idx = len(self.items) + 1
        body = content.encode("utf-8")
        item = {
            "id": idx,
            "url": url,
            "final_url": final_url or url,
            "purpose": purpose,
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
            "method": method + (" (simulated)" if self.simulated else ""),
            "file": f"{idx:03d}.md",
        }
        (self.dir / item["file"]).write_bytes(body)
        self.items.append(item)
        (self.dir / "manifest.json").write_text(json.dumps(self.items, indent=2))
        return item
