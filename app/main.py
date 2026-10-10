"""ChainSight API + dashboard.  Run:  uvicorn app.main:app --reload"""
from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, orchestrator
from .chains import is_valid_address
from .chains.demo import DEMO_SEEDS
from .config import ROOT, settings
from .labels import vasp_registry
from .report import build_pdf

app = FastAPI(title="ChainSight", version="0.1.0",
              description="Crypto fraud fund-tracing and VASP contact discovery. "
                          "Outputs are investigative leads for human review, not proof.")
STATIC = ROOT / "app" / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def require_key(x_api_key: str | None = Header(default=None)):
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(401, "Invalid or missing X-API-Key")


class CaseIn(BaseModel):
    chain: Literal["ethereum", "tron"]
    address: str = Field(..., min_length=20, max_length=80)
    complaint_ref: str = ""
    p2p_url: str = ""
    notes: str = ""
    demo: bool = False


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def config():
    return {
        "mode": settings.mode,
        "tinyfish": "live" if settings.tinyfish_live else "simulated",
        "chains": {c: "live" if settings.chain_live(c) else "demo" for c in ("ethereum", "tron")},
        "demo_addresses": DEMO_SEEDS,
        "vasps": sorted(v["name"] for v in vasp_registry().values()),
        "limits": {"max_hops": settings.max_hops, "max_branch": settings.max_branch,
                   "max_nodes": settings.max_nodes},
        "api_key_required": bool(settings.api_key),
    }


@app.post("/api/cases", status_code=202, dependencies=[Depends(require_key)])
async def create_case(body: CaseIn):
    address = body.address.strip()
    if not is_valid_address(body.chain, address):
        raise HTTPException(422, f"Not a valid {body.chain} address")
    case = orchestrator.new_case(body.chain, address, demo=body.demo,
                                 complaint_ref=body.complaint_ref, p2p_url=body.p2p_url.strip(),
                                 notes=body.notes)
    orchestrator.start(case)
    return {"id": case["id"], "status": case["status"], "demo": case["demo"]}


@app.get("/api/cases", dependencies=[Depends(require_key)])
def list_cases():
    return db.list_cases()


def _case(case_id: str) -> dict:
    case = db.get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    return case


@app.get("/api/cases/{case_id}", dependencies=[Depends(require_key)])
def get_case(case_id: str):
    return _case(case_id)


@app.get("/api/cases/{case_id}/report.pdf", dependencies=[Depends(require_key)])
def report_pdf(case_id: str):
    case = _case(case_id)
    if case["status"] != "done":
        raise HTTPException(409, "Case is not finished yet")
    db.audit(case_id, "report_downloaded", "pdf", actor="investigator")
    return Response(build_pdf(case), media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="chainsight-{case_id}.pdf"'})


@app.get("/api/cases/{case_id}/report.json", dependencies=[Depends(require_key)])
def report_json(case_id: str):
    case = _case(case_id)
    db.audit(case_id, "report_downloaded", "json", actor="investigator")
    case.pop("traceback", None)
    return JSONResponse(case, headers={
        "Content-Disposition": f'attachment; filename="chainsight-{case_id}.json"'})


@app.get("/api/cases/{case_id}/freeze-request.txt", dependencies=[Depends(require_key)])
def freeze_txt(case_id: str):
    case = _case(case_id)
    return PlainTextResponse(case.get("freeze_request") or "No VASP identified.")


@app.get("/api/cases/{case_id}/evidence/{item_id}", dependencies=[Depends(require_key)])
def evidence_item(case_id: str, item_id: int):
    case = _case(case_id)
    item = next((e for e in case.get("evidence", []) if e["id"] == item_id), None)
    if not item:
        raise HTTPException(404, "Evidence item not found")
    path = settings.evidence_dir / case_id / item["file"]
    # Served byte-for-byte so the SHA-256 in the report can be re-verified.
    return Response(path.read_bytes(), media_type="text/plain; charset=utf-8", headers={
        "X-Evidence-SHA256": item["sha256"], "X-Captured-At": item["captured_at"],
        "X-Source-URL": item["url"]})


@app.get("/api/cases/{case_id}/audit", dependencies=[Depends(require_key)])
def audit(case_id: str):
    _case(case_id)
    return db.audit_log(case_id)
