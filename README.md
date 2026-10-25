# Solana Early Gem Scanner (MVP)

A read-only Solana token-pair scanner with a browser dashboard. It does not trade, ask for a wallet connection, or store private keys.

## Features
- Periodically reads DEX Screener's latest token profiles.
- Looks up Solana pairs for a limited number of those profiles.
- Filters by pair age and liquidity.
- Ranks candidates using visible liquidity, pair age, 5-minute volume, transactions, buy/sell count and price change.
- Displays DEX chart, Solscan and Jupiter swap links.
- Dashboard refreshes automatically.

## Important limitations
This is a useful starter MVP, not a complete launch detector. DEX Screener's latest token-profile endpoint is not a complete feed of every newly launched token. Unprofiled launches may be missed and detection may occur after the first trades. To detect launches within seconds across launchpads, integrate a dedicated real-time on-chain/indexing provider (some providers require API keys or paid plans).

The market score is a heuristic, not a probability of profit. This MVP does NOT check mint/freeze authority, holder concentration, honeypot/sell restrictions, liquidity lock/burn status or creator-wallet history. A WATCH label is not a buy recommendation. Do not use this alone to trade newly launched tokens.

## Run locally
Requires Python 3.10+.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:8080.

On Android, local execution is possible with a Python environment, but it will stop when the process/app is stopped and is not 24/7. For continuous operation, deploy to a host that supports Python web services.

## Deploy on Render
1. Create a GitHub account and a new repository.
2. Upload `app.py`, `requirements.txt`, and `render.yaml` to the repository.
3. Sign in to Render and create a new Web Service from that repository.
4. The included `render.yaml` supplies a basic configuration; review the current plan limits and sleeping/usage behavior before deploying.
5. After deployment, open the generated service URL from your phone.

Free hosting policies can change and may sleep or have usage limits, so confirm the provider's current terms. If it sleeps, the scanner will not run continuously while asleep.

## Environment variables
- `POLL_SECONDS` (default 60; minimum 30)
- `MAX_PROFILES_PER_SCAN` (default 10; capped at 15)
- `MIN_LIQUIDITY_USD` (default 5000)
- `MAX_PAIR_AGE_MINUTES` (default 60)
- `MIN_TXNS_5M` (default 10)
- `PORT` (set by many hosts automatically)

## Data source
DEX Screener public API documentation: https://docs.dexscreener.com/api/reference
