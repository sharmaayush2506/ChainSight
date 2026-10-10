# ChainSight

**Crypto-fraud fund tracing and VASP contact discovery for investigators.**

You enter a wallet address that a victim reported. ChainSight:

1. Follows the money hop by hop on-chain.
2. Spots laundering shapes (pass-through, fan-out/fan-in, peel chains).
3. Finds the exchange (VASP) that received the funds.
4. Uses **TinyFish** web agents to check public scam reports and find that exchange's law-enforcement request process.
5. Captures hashed evidence snapshots of the pages it used.
6. Produces one investigator report: a fund-flow graph, a risk lead score, the nearest VASP, suggested next actions, an evidence trail and a draft freeze request.

> A human investigator always reviews the output. The risk score is a lead, not proof of guilt.

---

## Quick start (2 minutes, no API keys)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
# open http://localhost:8000 and click "Load demo case"
```

Demo mode uses a synthetic but realistic laundering scenario: victims pay a scam wallet (fan-in), the wallet forwards everything within minutes (pass-through), the funds split across mules (fan-out), and then:

- one branch goes through a peel chain into a Binance deposit address;
- one goes straight to an OKX deposit address;
- one goes into a bridge.

TinyFish replies are simulated in demo mode, and every simulated item is labelled that way in the UI and the PDF.

Run the tests:

```bash
python -m pytest -q
```

## Live mode

```bash
cp .env.example .env
# fill in:
#   TINYFISH_API_KEY   from https://agent.tinyfish.ai/api-keys
#   ETHERSCAN_API_KEY  from https://etherscan.io/myapikey (needed for Ethereum)
#   TRONGRID_API_KEY   optional; Tron works without it but is rate limited
uvicorn app.main:app
```

`CHAINSIGHT_MODE`:

- `auto` (default): live wherever a key is available, demo otherwise.
- `live`: always use the real APIs.
- `demo`: always use the offline dataset.

The badges at the top of the dashboard show which parts are live. Unticking "Use demo dataset" in the form traces a real address.

## How it maps to the pitch

| Pitch step | Code | What it does |
|---|---|---|
| Input (dashboard / API) | `app/main.py`, `app/static/` | Form plus REST API (`POST /api/cases`), with an optional `X-API-Key` |
| Orchestrator | `app/orchestrator.py` | Runs on-chain tracing and TinyFish off-chain intel **in parallel** (asyncio), then VASP discovery, scoring and the report. Each step's status and timing is recorded |
| Step 1: on-chain tracing | `app/tracing.py`, `app/chains/` | Builds a hop-by-hop NetworkX graph and follows funds forward in time (largest branches first). Stops at known exchanges, mixers and bridges |
| Exchange identification | `app/tracing.py`, `data/labels.json` | Matches known exchange wallets. **Deposit-address inference**: an unlabelled address that sweeps ≥80% of its outflow into an exchange wallet is treated as that exchange's customer deposit address, which is the freeze target |
| Pattern detection | `app/patterns.py` | Fast pass-through, fan-out, fan-in, peel chain |
| Step 2: off-chain intel | `app/intel.py` | TinyFish **Search** for the address on scam-report sites and forums. TinyFish **Fetch** reads each page and confirms the address actually appears on it. Also checks typology keywords, sanctions/abuse lists and (optionally) a P2P seller profile via the **Agent** |
| Step 3: VASP contact discovery | `app/vasp.py`, `data/vasps.json` | The TinyFish **Web Agent** navigates the exchange's site and returns the portal, process steps, required documents and contact as JSON. Results are cached for 7 days. If the agent fails, the registry fallback is used and marked *manual review* |
| Step 4: evidence capture | `app/evidence.py` | Every page used is saved with a UTC timestamp and a SHA-256 hash. `GET /api/cases/{id}/evidence/{n}` serves it byte for byte so the hash can be re-verified |
| Risk engine | `app/risk.py` | Rule-based score with an itemised "why" list, a fraud category, next actions and a draft freeze / KYC request |
| Step 5: report | `app/report.py` | PDF (graph, score, VASP contacts, evidence table, transaction hashes, draft letter) plus JSON |

### TinyFish usage

| Piece | Endpoint | Used for |
|---|---|---|
| Search | `GET https://api.search.tinyfish.ai?query=` | Finding mentions of the wallet on scam-report sites, forums and social pages; finding a starting page for VASPs not in the registry |
| Fetch | `POST https://api.fetch.tinyfish.ai` | Reading those pages to confirm the address and classify the scam type; snapshotting the VASP's law-enforcement page as evidence |
| Web Agent | `POST https://agent.tinyfish.ai/v1/automation/run-sse` | Navigating exchange sites to find the law-enforcement process and contact; extracting P2P seller profiles. The agent's live browser `streaming_url` is linked in the UI |
| Browser | not used yet | Stretch goal: full-page screenshots for evidence. Today, evidence is Fetch-rendered page text plus a hash |

Every TinyFish call is retried with backoff and logged with its duration. The dashboard shows this log, so you can quote **measured** timings in the deck.

## Scaling by configuration

- **New exchange:** add an entry to `data/vasps.json`. The `start_url` is only a hint, because the agent finds the current process itself. For an exchange that isn't listed, TinyFish Search finds a starting page.
- **New exchange wallets:** add them to `data/labels.json`, or load a larger label dataset.
- **New chain:** add a provider in `app/chains/` that implements `outgoing()` and `incoming()`.
- **Tracing limits:** set `TRACE_MAX_HOPS`, `TRACE_MAX_BRANCH` and `TRACE_MAX_NODES` in `.env`.

## REST API

```bash
curl -X POST localhost:8000/api/cases -H 'content-type: application/json' \
  -d '{"chain":"tron","address":"T...","complaint_ref":"NCRP-123"}'
# -> {"id":"ab12cd34ef","status":"queued"}

curl localhost:8000/api/cases/ab12cd34ef                      # status + full results
curl -O localhost:8000/api/cases/ab12cd34ef/report.pdf
curl localhost:8000/api/cases/ab12cd34ef/report.json
curl localhost:8000/api/cases/ab12cd34ef/freeze-request.txt
curl localhost:8000/api/cases/ab12cd34ef/audit                 # audit log
```

Interactive docs are at `http://localhost:8000/docs`.

## Honest limits (say these in the deck)

- **Exchange labels are limited.** `data/labels.json` is a small seed list of publicly labelled wallets, and labels go stale. Verify them before relying on them. Deposit-address inference is a heuristic.
- **Tracing is simplified.** It follows the largest outgoing branches forward in time, using a bottleneck amount estimate rather than a full taint analysis. Tron traces TRC-20 stablecoins only (USDT is the main scam rail). Ethereum traces ETH, USDT, USDC, DAI and WETH. Spam tokens are ignored to resist address poisoning.
- **Bridges and cross-chain flows** are detected and flagged, but not followed (roadmap).
- **Agents are slower than APIs** and can fail on unusual sites. They are used only where no API exists, with retries, caching and a manual-review fallback.
- **This is a prototype, not production infrastructure.** It uses SQLite and in-process async jobs. The pitch's Postgres + Redis/Celery setup is a drop-in change to `db.py` and `orchestrator.start()`.
- **Legal and ethical use:** authorised investigative use only. Every case action is audit-logged. Agents are instructed never to log in or submit forms.

## Project layout

```
app/
  main.py            FastAPI app + dashboard
  orchestrator.py    parallel pipeline, step tracking, timings
  chains/            ethereum.py (Etherscan V2), tron.py (TronGrid), demo.py (offline scenario)
  tracing.py         graph building, deposit-address inference, nearest VASP
  patterns.py        pass-through / fan-out / fan-in / peel chain
  tinyfish.py        Search / Fetch / Agent (SSE) client + simulated client for demos
  intel.py           wallet mentions, typology, P2P seller profile
  vasp.py            law-enforcement contact discovery
  evidence.py        timestamped, hashed snapshots
  risk.py            score, next actions, draft freeze request
  report.py          PDF report
  static/            dashboard (plain JS + Cytoscape.js, vendored so it works offline)
data/                labels.json, vasps.json, sanctions.json
tests/               pipeline tests + mocked-HTTP tests for the live clients
```
