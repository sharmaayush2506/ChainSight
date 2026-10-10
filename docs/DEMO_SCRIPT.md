# Demo script (about 4 minutes)

## Before you present

1. `python scripts/check_live.py --agent`: confirm the keys work and note the timings.
2. Start the app: `uvicorn app.main:app`. Run the demo case once so it's cached in "Recent cases" in case the venue Wi-Fi fails.
3. If you have a real reported Tron/Ethereum address that's safe to show, trace it live once beforehand and keep that case open in a tab.

## Walkthrough

| Time | Show | Say |
|---|---|---|
| 0:00 | Empty dashboard | "A victim reports this wallet. Today an investigator spends hours or days finding which exchange got the money, and then finding how to contact them." |
| 0:20 | Click **Load demo case**; the pipeline steps light up | "On-chain tracing and TinyFish web agents run in parallel." |
| 0:40 | **Fund-flow graph** | "Victims pay one wallet (fan-in). It forwards everything within minutes (pass-through), splits into three mules (fan-out), and one branch peels small amounts off at each hop (peel chain)." |
| 1:10 | Click the orange **deposit** node | "This address sweeps into an exchange hot wallet, so it's a customer deposit address at OKX. That's what the exchange can freeze and identify." |
| 1:40 | **Off-chain intelligence** | "TinyFish Search found public reports of this wallet. Fetch read each page and confirmed the address is really on it. That's how we classify it as a task scam." |
| 2:10 | **VASP contact** card | "The TinyFish agent navigated the exchange's site and found the law-enforcement portal, steps and required documents. There's no API for this, and every exchange's site is different." (In live mode, click **Watch the agent's browser session**.) |
| 2:40 | **Evidence trail** | "Every page we relied on is saved with a timestamp and a SHA-256 hash, so it can support a case." |
| 3:00 | **Draft freeze request**, then **Download PDF report** | "One report, ready to send. The time to report is measured: X seconds here instead of hours." |
| 3:30 | **Human review** banner, **Why this score** | "The score is a lead, not proof. Every point is explained, and a human always decides." |

## Likely judge questions

- **Where does the exchange data come from?** Public labels (limited, and we say so) plus deposit-address inference from sweep behaviour. A commercial label feed plugs into `data/labels.json`.
- **What if the agent fails?** It retries with backoff. If it still fails, it falls back to the registry entry, flags the result for *manual review*, and logs every failure.
- **How do you scale to more exchanges?** One JSON entry each. The agent finds the current page itself, and results are cached for 7 days.
- **Cross-chain?** Bridges are detected and flagged today. Following funds across chains is on the roadmap.
