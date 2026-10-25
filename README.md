# Solana Early Gem Scanner — All-Features MVP

This version combines:
- Early-pair market screening from DEX Screener's public API.
- A heuristic 0–100 screening score with risk notes.
- Basic Solana mint/freeze authority checks through RPC.
- Top-10 token-account concentration indicator (a rough whale/concentration signal).
- Optional Telegram alerts for qualifying WATCH-labelled candidates.

## Deploy/update on Render
1. Upload/replace these files in the root of your existing GitHub repository: `app.py`, `requirements.txt`, `render.yaml`, and `README.md`.
2. Commit the changes.
3. In Render, confirm the latest deploy is Live.

## Optional Telegram alerts
1. In Telegram, open the official BotFather account and create a bot. Keep its bot token private.
2. Send a message to your new bot, or add it to a group/channel where it has permission to post.
3. Find the chat ID using a trusted Telegram bot/API method. For a channel, add your bot as an administrator with permission to post.
4. In Render Dashboard → your web service → Environment, add:
   - `TELEGRAM_BOT_TOKEN` = your private bot token
   - `TELEGRAM_CHAT_ID` = your chat/group/channel ID
   - optionally `ALERT_MIN_SCORE` = `70`
5. Save changes and redeploy. The dashboard should show Telegram alerts Enabled if both values are present.

Never put your real bot token in GitHub, screenshots, or chat messages. If exposed, revoke it with BotFather and create a new one.

## Important limitations
This is an experimental screening tool, not financial advice. Scores are heuristic and can be manipulated. The top-10 figure is based on token accounts, not deduplicated owners; liquidity pools, exchanges, and burn accounts may distort it. This version does not prove sellability or fully verify liquidity locks/burns, creator-wallet history, all Token-2022 extensions, malicious contracts, or all honeypot/rug-pull patterns. A WATCH label is not a buy signal. No wallet private key is needed; no automatic trading is implemented.

Data can be incomplete, delayed, rate-limited, or unavailable. Keep API usage reasonable and monitor Render logs.
