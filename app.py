import os
import time
import base64
import struct
import threading
import logging
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template_string

APP_NAME = "Solana Early Gem Scanner"
POLL_SECONDS = max(60, int(os.getenv("POLL_SECONDS", "90")))
MAX_PROFILES_PER_SCAN = max(1, min(20, int(os.getenv("MAX_PROFILES_PER_SCAN", "10"))))
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "5000"))
MAX_PAIR_AGE_MINUTES = float(os.getenv("MAX_PAIR_AGE_MINUTES", "60"))
MIN_TXNS_5M = int(os.getenv("MIN_TXNS_5M", "10"))
SECURITY_CHECK_LIMIT = max(1, min(10, int(os.getenv("SECURITY_CHECK_LIMIT", "5"))))
ALERT_MIN_SCORE = max(0, min(100, int(os.getenv("ALERT_MIN_SCORE", "70"))))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
REQUEST_TIMEOUT = 12
DEX_BASE = "https://api.dexscreener.com"
SOLANA_RPC = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("scanner")
app = Flask(__name__)
_lock = threading.Lock()
_security_cache = {}
_security_cache_lock = threading.Lock()
_alerted = set()
_alerted_lock = threading.Lock()
_state = {
    "last_scan": None, "status": "Starting",
    "message": "Waiting for first scan.", "tokens": [],
    "scan_count": 0, "errors": [], "alerts_enabled": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
}

HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Solana Early Gem Scanner</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--card:#151d32;--line:#2b3652;--muted:#9eabc7;--text:#edf2ff;--green:#47d7a0;--yellow:#ffd166;--red:#ff7070}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
header{padding:24px 18px;background:linear-gradient(120deg,#16244a,#123b43);border-bottom:1px solid var(--line)}
main{max-width:1300px;margin:auto;padding:18px}.tag{display:inline-block;border:1px solid #3b547c;border-radius:99px;padding:4px 10px;color:#c7d8ff;font-size:12px}
h1{font-size:clamp(25px,4vw,38px);margin:8px 0}p{color:var(--muted);margin:6px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:18px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px}.label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}.value{font-size:23px;font-weight:700;margin-top:5px}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin:15px 0}.controls input{background:#11182a;color:var(--text);border:1px solid var(--line);padding:10px;border-radius:9px;min-width:190px}
.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:14px}table{width:100%;border-collapse:collapse;min-width:1250px;background:var(--card)}th,td{text-align:left;padding:12px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--muted);text-transform:uppercase}tr:last-child td{border-bottom:0}
.symbol{font-weight:750}.sub{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.score{font-weight:800;font-size:17px}.good{color:var(--green)}.warn{color:var(--yellow)}.bad{color:var(--red)}a{color:#9bc4ff;text-decoration:none}a:hover{text-decoration:underline}.pill{display:inline-block;border-radius:7px;padding:3px 7px;font-size:12px;font-weight:700;background:#26334c}.foot{font-size:12px;color:var(--muted);margin:18px 2px}.empty{padding:25px;color:var(--muted);text-align:center}.risklist{margin:6px 0 0;padding-left:16px;font-size:12px;color:var(--muted)}.controls select{background:#11182a;color:var(--text);border:1px solid var(--line);padding:10px;border-radius:9px}.mini{font-size:12px;color:var(--muted)}.section-title{margin:22px 0 8px;font-size:18px}.best{border-color:#47d7a0;box-shadow:0 0 0 1px #47d7a033}
</style></head><body>
<header><main style="padding:0 18px"><span class="tag">SOLANA · SCANNER DASHBOARD</span>
<h1>Solana Early Gem Scanner</h1>
<p>Rank new Solana pairs, review risk warnings, and filter candidates. Scores are heuristic signals—not guarantees or buy recommendations.</p></main></header>
<main>
<div class="grid">
<div class="card"><div class="label">Scanner status</div><div class="value">{{ state.status }}</div><p>{{ state.message }}</p></div>
<div class="card"><div class="label">Pairs passing filters</div><div class="value">{{ state.tokens|length }}</div><p>Current scan results</p></div>
<div class="card"><div class="label">Last scan (UTC)</div><div class="value" style="font-size:17px">{{ state.last_scan or "Not yet" }}</div><p>Periodic refresh</p></div>
<div class="card"><div class="label">Scan count</div><div class="value">{{ state.scan_count }}</div><p>Since this process started</p></div>
<div class="card"><div class="label">Telegram alerts</div><div class="value">{{ "Enabled" if state.alerts_enabled else "Off" }}</div><p>{{ "Score threshold: " ~ alert_min_score ~ "/100" if state.alerts_enabled else "Not configured" }}</p></div>
<div class="card"><div class="label">WATCH candidates</div><div class="value good">{{ state.tokens|selectattr('label','equalto','WATCH')|list|length }}</div><p>Meets current heuristic screen</p></div>
<div class="card"><div class="label">CAUTION candidates</div><div class="value warn">{{ state.tokens|selectattr('label','equalto','CAUTION')|list|length }}</div><p>Review warnings carefully</p></div>
<div class="card"><div class="label">AVOID candidates</div><div class="value bad">{{ state.tokens|selectattr('label','equalto','AVOID')|list|length }}</div><p>High caution under this model</p></div>
</div>
<h2 class="section-title">Token results</h2>
<div class="controls"><input id="filter" placeholder="Search token / symbol / address…" oninput="filterRows()">
<select id="riskFilter" onchange="filterRows()"><option value="">All risk labels</option><option>WATCH</option><option>CAUTION</option><option>AVOID</option></select>
<label class="tag">Min score <input id="minScore" type="number" min="0" max="100" value="0" style="width:65px;min-width:65px;background:#11182a;color:var(--text);border:0" oninput="filterRows()"></label>
<label class="tag">Min liquidity $ <input id="minLiq" type="number" min="0" value="0" style="width:95px;min-width:95px;background:#11182a;color:var(--text);border:0" oninput="filterRows()"></label>
<span class="tag">Scanner liquidity floor: ${{ "{:,.0f}".format(min_liquidity) }}</span><span class="tag">Max age: {{ max_age }} min</span><span class="tag">Security checks/scan: {{ security_limit }}</span><span class="tag">Alert score: {{ alert_min_score }}+</span><button type="button" onclick="resetFilters()" style="background:#26334c;color:var(--text);border:1px solid var(--line);padding:9px 12px;border-radius:9px">Reset filters</button></div><p class="mini" id="visibleCount">Showing all available rows.</p>
<div class="tablewrap"><table id="tokens"><thead><tr>
<th>Token</th><th>Age</th><th>Liquidity</th><th>5m volume</th><th>Buys / sells</th><th>5m change</th><th>Risk score</th><th>Market/risk screen</th><th>Authority checks</th><th>Whale / concentration</th><th>Links</th>
</tr></thead><tbody>
{% for t in state.tokens %}<tr>
<td><div class="symbol">{{ t.symbol }}</div><div>{{ t.name }}</div><div class="sub">{{ t.address }}</div></td>
<td>{{ "%.1f"|format(t.age_min) }} min</td><td>${{ "{:,.0f}".format(t.liquidity) }}</td>
<td>${{ "{:,.0f}".format(t.vol5) }}</td><td>{{ t.buys5 }} / {{ t.sells5 }}</td>
<td class="{{ 'good' if t.change5 > 0 else 'bad' if t.change5 < 0 else '' }}">{{ "%.2f"|format(t.change5) }}%</td>
<td class="score {{ 'good' if t.score >= 70 else 'warn' if t.score >= 40 else 'bad' }}" data-score="{{ t.score }}" data-liquidity="{{ t.liquidity }}">{{ t.score }}/100</td>
<td><span class="pill {{ 'good' if t.label == 'WATCH' else 'warn' if t.label == 'CAUTION' else 'bad' }}">{{ t.label }}</span><ul class="risklist">{% for r in t.reasons %}<li>{{ r }}</li>{% endfor %}</ul></td>
<td><span class="pill {{ 'good' if t.security.status == 'No obvious mint-authority warning' else 'warn' if t.security.status in ['Partial check','Unknown','Not checked'] else 'bad' }}">{{ t.security.status }}</span>
<ul class="risklist">{% for r in t.security.warnings %}<li>{{ r }}</li>{% endfor %}</ul></td>
<td>{% if t.security.top10_pct is not none %}<b>{{ "%.1f"|format(t.security.top10_pct) }}%</b> held by top 10 token accounts<ul class="risklist">{% for r in t.security.whale_notes %}<li>{{ r }}</li>{% endfor %}</ul>{% else %}<span class="sub">Concentration not available; not proof of safety.</span>{% endif %}</td>
<td><a href="{{ t.dex_url }}" target="_blank" rel="noopener">DEX chart</a><br><a href="https://solscan.io/token/{{ t.address }}" target="_blank" rel="noopener">Solscan</a><br><a href="https://jup.ag/swap/SOL-{{ t.address }}" target="_blank" rel="noopener">Open swap</a></td>
</tr>{% else %}<tr><td colspan="11" class="empty">No pairs currently meet your filters. Discovery data is limited; this does not mean no new coins exist.</td></tr>{% endfor %}
</tbody></table></div>
<div class="card" style="margin-top:16px"><b>How to use this dashboard</b><p>Start with higher scores, then check liquidity, token age, authority warnings, and top-10 account concentration. Use the filters to narrow the list; do not treat WATCH as a buy signal.</p></div>
<div class="card" style="margin-top:16px"><b>Important risk limitations</b>
<p><b>This is a screening tool, not an investment recommendation.</b> A high score does not mean a token is safe, genuine, sellable, or likely to rise. Scores use simple heuristics and can be manipulated by wash trading, bots, concentrated liquidity, and short-lived volume.</p>
<p>On-chain checks cover basic mint/freeze authority indicators and top-10 token-account concentration. Token accounts are not necessarily unique owners; exchange/pool/burn addresses may distort concentration. This version does not prove sellability or fully check LP locks/burns, creator history, every Token-2022 extension, malicious contracts, or every honeypot/rug-pull method. RPC errors are marked unverified.</p>
<p>Telegram alerts are informational and may be delayed or repeated after a restart. They never buy or sell. No wallet private key is requested or stored.</p></div>
<div class="foot">Data: DEX Screener public API, Solana mainnet RPC, and optional Telegram Bot API. Data can be delayed, rate-limited, incomplete, or unavailable.</div>
</main><script>
function filterRows(){
 const q=document.getElementById('filter').value.toLowerCase();
 const risk=document.getElementById('riskFilter').value;
 const minScore=Number(document.getElementById('minScore').value||0);
 const minLiq=Number(document.getElementById('minLiq').value||0);
 let shown=0,total=0;
 document.querySelectorAll('#tokens tbody tr').forEach(r=>{
  const scoreCell=r.querySelector('[data-score]');
  if(!scoreCell){r.style.display='';return;}
  total++;
  const score=Number(scoreCell.dataset.score||0), liq=Number(scoreCell.dataset.liquidity||0);
  const label=(r.querySelector('td:nth-child(8) .pill')||{}).innerText||'';
  const ok=r.innerText.toLowerCase().includes(q)&&(!risk||label.trim()===risk)&&score>=minScore&&liq>=minLiq;
  r.style.display=ok?'':'none'; if(ok)shown++;
 });
 document.getElementById('visibleCount').innerText=`Showing ${shown} of ${total} token rows.`;
}
function resetFilters(){document.getElementById('filter').value='';document.getElementById('riskFilter').value='';document.getElementById('minScore').value='0';document.getElementById('minLiq').value='0';filterRows();}
document.addEventListener('DOMContentLoaded',filterRows);
</script></body></html>"""

def get_json(url):
    response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "SolanaEarlyGemScanner/2.0"})
    response.raise_for_status()
    return response.json()

def rpc(method, params):
    response = requests.post(SOLANA_RPC, json={"jsonrpc":"2.0","id":1,"method":method,"params":params},
                             timeout=REQUEST_TIMEOUT, headers={"Content-Type":"application/json"})
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(str(payload["error"])[:180])
    return payload.get("result")

def number(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
        return bool(body.get("ok"))
    except Exception as exc:
        log.warning("Telegram alert failed: %s", type(exc).__name__)
        return False

def maybe_alert(item):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    if item["label"] != "WATCH" or item["score"] < ALERT_MIN_SCORE:
        return
    # One alert per token per running process; bounded memory.
    key = item["address"]
    with _alerted_lock:
        if key in _alerted:
            return
        _alerted.add(key)
        if len(_alerted) > 2000:
            _alerted.clear()
            _alerted.add(key)
    security = item["security"]
    msg = (
        "🔎 Solana Early Gem Scanner alert\n"
        f"Token: {item['name']} ({item['symbol']})\n"
        f"Score: {item['score']}/100 — WATCH (not a buy signal)\n"
        f"Age: {item['age_min']:.1f} min | Liquidity: ${item['liquidity']:,.0f}\n"
        f"5m volume: ${item['vol5']:,.0f} | Buys/sells: {item['buys5']}/{item['sells5']}\n"
        f"Security status: {security.get('status','Unknown')}\n"
        f"Top-10 token accounts: {security.get('top10_pct') if security.get('top10_pct') is not None else 'Unknown'}%\n"
        f"Token address: {item['address']}\n"
        f"Chart: {item['dex_url']}\n\n"
        "Research independently. Scores and on-chain checks can be incomplete or manipulated."
    )
    send_telegram(msg)

def onchain_security(mint):
    now = time.time()
    with _security_cache_lock:
        cached = _security_cache.get(mint)
        if cached and now - cached["time"] < 600:
            return cached["data"]
    result = {"status":"Unknown", "warnings":["Security data unavailable; do not assume safe."],
              "mint_authority":None, "freeze_authority":None, "top10_pct":None, "whale_notes":[]}
    try:
        info = rpc("getAccountInfo", [mint, {"encoding":"base64","commitment":"confirmed"}])
        value = (info or {}).get("value")
        if not value:
            result["status"] = "Partial check"
            result["warnings"] = ["Mint account not returned by RPC."]
        else:
            owner_program = value.get("owner", "")
            encoded = (value.get("data") or [""])[0]
            raw = base64.b64decode(encoded) if encoded else b""
            if len(raw) < 82:
                result["status"] = "Partial check"
                result["warnings"] = ["Mint account layout not recognized; manual review required."]
            else:
                mint_opt = struct.unpack("<I", raw[0:4])[0]
                freeze_opt = struct.unpack("<I", raw[46:50])[0]
                result["mint_authority"] = "active" if mint_opt else "disabled"
                result["freeze_authority"] = "active" if freeze_opt else "disabled"
                warnings = []
                if mint_opt:
                    warnings.append("Mint authority appears active (additional tokens may be minted).")
                else:
                    warnings.append("Mint authority appears disabled.")
                if freeze_opt:
                    warnings.append("Freeze authority appears active (token accounts may be frozen).")
                else:
                    warnings.append("Freeze authority appears disabled.")
                result["warnings"] = warnings
                result["status"] = "Authority warning" if mint_opt or freeze_opt else "No obvious mint-authority warning"
                result["token_program"] = owner_program
                try:
                    largest = rpc("getTokenLargestAccounts", [mint, {"commitment":"confirmed"}])
                    supply = rpc("getTokenSupply", [mint, {"commitment":"confirmed"}])
                    total_raw = int(((supply or {}).get("value") or {}).get("amount") or "0")
                    accounts = ((largest or {}).get("value") or [])
                    largest_raw = sum(int(a.get("amount") or "0") for a in accounts[:10])
                    if total_raw > 0:
                        result["top10_pct"] = min(100.0, 100.0 * largest_raw / total_raw)
                        pct = result["top10_pct"]
                        if pct >= 70:
                            result["whale_notes"] = ["High concentration: top 10 token accounts hold ≥70% of reported supply."]
                        elif pct >= 40:
                            result["whale_notes"] = ["Elevated concentration: top 10 token accounts hold ≥40% of reported supply."]
                        else:
                            result["whale_notes"] = ["Top-10 account concentration is below 40%; this does not prove broad ownership."]
                        if pct >= 70:
                            result["warnings"].append("Top 10 token accounts hold ≥70% of reported supply; concentration risk.")
                        elif pct >= 40:
                            result["warnings"].append("Top 10 token accounts hold ≥40% of reported supply; review concentration.")
                    else:
                        result["whale_notes"] = ["Supply unavailable; concentration not calculated."]
                except Exception as exc:
                    result["warnings"].append("Could not calculate top-10 account concentration.")
                    result["whale_notes"] = ["Whale concentration data unavailable."]
                    log.info("Largest account check failed for %s: %s", mint[:8], exc)
    except Exception as exc:
        result["status"] = "Unknown"
        result["warnings"] = [f"RPC check failed ({type(exc).__name__}); treat security as unverified."]
        result["whale_notes"] = ["Whale concentration data unavailable."]
        log.warning("Security check failed for %s: %s", mint[:8], exc)
    with _security_cache_lock:
        _security_cache[mint] = {"time":now, "data":result}
    return result

def analyze_pair(pair):
    now_ms = time.time() * 1000
    created_ms = number(pair.get("pairCreatedAt"), now_ms)
    age_min = max(0, (now_ms - created_ms) / 60000)
    liquidity = number((pair.get("liquidity") or {}).get("usd"))
    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    changes = pair.get("priceChange") or {}
    m5vol = number(volume.get("m5"))
    m5 = txns.get("m5") or {}
    buys, sells = int(number(m5.get("buys"))), int(number(m5.get("sells")))
    change5 = number(changes.get("m5"))
    base = pair.get("baseToken") or {}
    address = base.get("address") or ""
    name, symbol = base.get("name") or "Unknown", base.get("symbol") or "?"
    score, reasons = 0, []
    if liquidity >= 25000: score += 25
    elif liquidity >= 10000: score += 18
    elif liquidity >= MIN_LIQUIDITY_USD: score += 10
    else: reasons.append("low liquidity")
    if age_min <= 5: score += 15
    elif age_min <= 15: score += 12
    elif age_min <= MAX_PAIR_AGE_MINUTES: score += 7
    else: reasons.append("older pair")
    if m5vol >= 20000: score += 20
    elif m5vol >= 5000: score += 15
    elif m5vol >= 1000: score += 8
    els
