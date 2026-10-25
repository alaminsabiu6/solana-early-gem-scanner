import os
import time
import base64
import struct
import threading
import logging
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template_string, request

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
<h1>Solana Early Gem Scanner</h1><p><a class="tag" href="/technical">📈 Technical Analysis</a></p>
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
    else: reasons.append("low 5m volume")
    total_txns = buys + sells
    if total_txns >= 100: score += 15
    elif total_txns >= 40: score += 12
    elif total_txns >= MIN_TXNS_5M: score += 7
    else: reasons.append("low transaction count")
    if buys > sells * 1.5 and buys >= 10: score += 10
    elif buys > sells: score += 5
    elif sells > buys: score -= 8; reasons.append("sell pressure")
    if 0 < change5 <= 25: score += 10
    elif change5 > 25: score += 3; reasons.append("sharp price spike / possible volatility")
    elif change5 < -10: score -= 8; reasons.append("price falling")
    # Simple heuristic risk flags; these are not contract audits.
    if liquidity < MIN_LIQUIDITY_USD:
        score = min(score, 39)
    if m5vol > 0 and liquidity > 0 and m5vol > liquidity * 5:
        score -= 10
        reasons.append("very high 5m volume relative to liquidity; verify for wash trading/volatility")
    if total_txns > 0 and buys > 0 and sells == 0:
        score -= 3
        reasons.append("no reported sells in 5m; data may be sparse or trading one-sided")
    if not address:
        score -= 20
        reasons.append("missing token address")
    score = max(0, min(100, score))
    label = "WATCH" if score >= 70 and liquidity >= MIN_LIQUIDITY_USD and total_txns >= MIN_TXNS_5M else "CAUTION" if score >= 40 else "AVOID"
    if not reasons: reasons = ["no heuristic market flags; this is not proof of safety"]
    return {
        "name":name,"symbol":symbol,"address":address,"age_min":age_min,
        "liquidity":liquidity,"vol5":m5vol,"buys5":buys,"sells5":sells,
        "change5":change5,"score":score,"label":label,"reasons":reasons,
        "dex_url":pair.get("url") or f"https://dexscreener.com/solana/{pair.get('pairAddress','')}",
        "pair_address":pair.get("pairAddress",""),
        "security":{"status":"Not checked","warnings":["Not selected for on-chain checks this scan."],"top10_pct":None,"whale_notes":[]}
    }

def scan_once():
    errors, candidates = [], {}
    try:
        profiles = get_json(f"{DEX_BASE}/token-profiles/latest/v1")
        if not isinstance(profiles, list): profiles = []
        sol_profiles = [p for p in profiles if (p.get("chainId") or "").lower()=="solana" and p.get("tokenAddress")]
        for profile in sol_profiles[:MAX_PROFILES_PER_SCAN]:
            address = profile.get("tokenAddress")
            try:
                pairs = get_json(f"{DEX_BASE}/token-pairs/v1/solana/{address}")
                if not isinstance(pairs, list): continue
                for pair in pairs:
                    if (pair.get("chainId") or "").lower() != "solana" or not pair.get("pairCreatedAt"): continue
                    token = ((pair.get("baseToken") or {}).get("address") or "")
                    if not token or token.lower() in {
                        "so11111111111111111111111111111111111111112",
                        "epjfwdd5aufqssqem2qn1xzybapc8g4wegkzwytdt1v"}: continue
                    item = analyze_pair(pair)
                    if item["age_min"] <= MAX_PAIR_AGE_MINUTES and item["liquidity"] >= MIN_LIQUIDITY_USD:
                        key = item["pair_address"] or item["address"]
                        if key not in candidates or item["liquidity"] > candidates[key]["liquidity"]:
                            candidates[key] = item
                time.sleep(0.15)
            except Exception as exc:
                errors.append(f"{address[:8]}…: {type(exc).__name__}")
                log.warning("Token lookup failed for %s: %s", address, exc)
        ranked = sorted(candidates.values(), key=lambda x:(x["score"],x["vol5"],x["liquidity"]), reverse=True)
        for item in ranked[:SECURITY_CHECK_LIMIT]:
            item["security"] = onchain_security(item["address"])
            security_text = " ".join(item["security"].get("warnings", [])).lower()
            if "mint authority appears active" in security_text:
                item["score"] = max(0, item["score"] - 12)
                item["reasons"].append("active mint authority risk (-12)")
            if "freeze authority appears active" in security_text:
                item["score"] = max(0, item["score"] - 12)
                item["reasons"].append("active freeze authority risk (-12)")
            if item["security"].get("top10_pct") is not None:
                pct = item["security"]["top10_pct"]
                if pct >= 70:
                    item["score"] = max(0, item["score"] - 20)
                    item["reasons"].append("high top-10 account concentration (-20)")
                elif pct >= 40:
                    item["score"] = max(0, item["score"] - 10)
                    item["reasons"].append("elevated top-10 account concentration (-10)")
            if item["security"]["status"] == "Unknown":
                item["score"] = max(0, item["score"] - 5)
                item["reasons"].append("security not verified (-5)")
            item["score"] = max(0, min(100, item["score"]))
            item["label"] = "WATCH" if item["score"] >= 70 and item["liquidity"] >= MIN_LIQUIDITY_USD and (item["buys5"]+item["sells5"]) >= MIN_TXNS_5M else "CAUTION" if item["score"] >= 40 else "AVOID"
            maybe_alert(item)
            time.sleep(0.15)
        ranked.sort(key=lambda x:(x["score"],x["vol5"],x["liquidity"]), reverse=True)
        with _lock:
            _state["tokens"] = ranked[:100]
            _state["last_scan"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            _state["status"] = "Running"
            _state["message"] = f"Checked {min(len(sol_profiles), MAX_PROFILES_PER_SCAN)} Solana profiles; {len(ranked)} pairs passed filters. Security/concentration checks run for up to {SECURITY_CHECK_LIMIT} candidates."
            _state["scan_count"] += 1
            _state["errors"] = errors[-10:]
    except Exception as exc:
        log.exception("Scan failed")
        with _lock:
            _state["status"] = "API error"
            _state["message"] = f"Could not refresh market data ({type(exc).__name__}). Will retry automatically."
            _state["errors"] = [str(exc)[:200]]

def scanner_loop():
    while True:
        scan_once()
        time.sleep(POLL_SECONDS)

@app.route("/")
def index():
    with _lock:
        snapshot = dict(_state)
        snapshot["tokens"] = list(_state["tokens"])
    return render_template_string(HTML, state=snapshot, min_liquidity=MIN_LIQUIDITY_USD,
                                  max_age=MAX_PAIR_AGE_MINUTES, security_limit=SECURITY_CHECK_LIMIT,
                                  alert_min_score=ALERT_MIN_SCORE)


# --- Technical analysis: historical OHLCV from GeckoTerminal public API ---
def _ema(values, period):
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(float(value) * alpha + result[-1] * (1.0 - alpha))
    return result

def _rsi(closes, period=14):
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = [max(closes[i] - closes[i-1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i-1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0/(1.0+avg_gain/avg_loss)
    for i in range(period+1, len(closes)):
        avg_gain = (avg_gain*(period-1)+gains[i-1])/period
        avg_loss = (avg_loss*(period-1)+losses[i-1])/period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0/(1.0+avg_gain/avg_loss)
    return out

def _indicators(candles):
    closes=[c['close'] for c in candles]
    e20=_ema(closes,20); e50=_ema(closes,50)
    e12=_ema(closes,12); e26=_ema(closes,26)
    macd=[a-b for a,b in zip(e12,e26)]
    signal=_ema(macd,9)
    rsi=_rsi(closes,14)
    for i,c in enumerate(candles):
        c['ema20']=e20[i]; c['ema50']=e50[i]
        c['macd']=macd[i]; c['macd_signal']=signal[i]; c['macd_hist']=macd[i]-signal[i]
        c['rsi']=rsi[i]
    lows=[c['low'] for c in candles[-50:]]; highs=[c['high'] for c in candles[-50:]]
    return {'rsi':rsi[-1] if rsi else None,'macd':macd[-1] if macd else None,
            'macd_signal':signal[-1] if signal else None,'ema20':e20[-1] if e20 else None,
            'ema50':e50[-1] if e50 else None,'support':min(lows) if lows else None,
            'resistance':max(highs) if highs else None}

TECHNICAL_HTML = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Technical Analysis · Solana Gem Scanner</title><script src="https://unpkg.com/lightweight-charts@4.2.2/dist/lightweight-charts.standalone.production.js"></script><style>
:root{color-scheme:dark;--bg:#0b1020;--card:#151d32;--line:#2b3652;--muted:#9eabc7;--text:#edf2ff;--green:#47d7a0;--yellow:#ffd166;--red:#ff7070}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}header{padding:22px;background:linear-gradient(120deg,#16244a,#123b43)}main{max-width:1200px;margin:auto;padding:16px}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px;margin:12px 0}.row{display:flex;flex-wrap:wrap;gap:9px;align-items:center}.tag,button{border:1px solid #3b547c;border-radius:9px;padding:9px 12px;background:#202b45;color:var(--text);text-decoration:none}input,select{padding:11px;background:#11182a;color:var(--text);border:1px solid var(--line);border-radius:9px;min-width:180px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.label{color:var(--muted);font-size:12px}.value{font-size:20px;font-weight:700;overflow-wrap:anywhere}.muted{color:var(--muted)}#priceChart{height:360px}#indicatorChart{height:240px}.good{color:var(--green)}.warn{color:var(--yellow)}.bad{color:var(--red)}a{color:#9bc4ff}.hidden{display:none}.small{font-size:12px}</style></head><body><header><main style="padding:0"><span class="tag">SOLANA · TECHNICAL ANALYSIS</span><h1>Technical Analysis</h1><p class="muted">Historical candles, RSI, MACD, moving averages and approximate support/resistance.</p><a class="tag" href="/">← Back to Scanner</a></main></header><main>
<div class="card"><form id="searchForm" class="row"><input id="token" placeholder="Solana token mint address" value="{{ token }}" required><select id="tf"><option value="minute:15">15 minutes</option><option value="hour:1" selected>1 hour</option><option value="hour:4">4 hours</option><option value="day:1">1 day</option></select><button type="submit">Analyze token</button><button type="button" id="simpleBtn">Simple view</button><button type="button" id="advancedBtn">Advanced view</button></form><p class="muted small">Paste a token's Solana contract address. Data availability depends on the pool and GeckoTerminal's public API. Symbol/name search is not supported in this first version to avoid selecting the wrong token.</p><p id="status" class="muted">Enter a token mint address and press Analyze.</p></div>
<div id="metrics" class="grid"></div><div class="card"><h2>Price chart</h2><div id="priceChart"></div></div><div class="card" id="advanced"><h2>RSI / MACD</h2><div id="indicatorChart"></div></div><div class="card"><h2>Interpretation guide</h2><p class="muted">RSI above 70 is often called overbought and below 30 oversold; neither predicts a reversal. MACD compares exponential moving averages. EMA20/EMA50 show smoothed trend. Support/resistance here are simple lowest-low/highest-high levels across the recent available candles, not guaranteed barriers.</p><p class="muted">This is informational analysis, not financial advice. Thin liquidity, new tokens, manipulated trading and incomplete data can make indicators unreliable. Never treat a signal as a guarantee.</p></div>
</main><script>
let priceChart=null, candleSeries=null, ema20Series=null, ema50Series=null, volSeries=null, indChart=null, rsiSeries=null, macdSeries=null, signalSeries=null, mode='advanced';
function setupCharts(){const L=window.LightweightCharts;if(!L){document.getElementById('status').textContent='Chart library did not load. Check your connection and reload.';return false;} const common={layout:{background:{color:'#151d32'},textColor:'#edf2ff'},grid:{vertLines:{color:'#26324a'},horzLines:{color:'#26324a'}},timeScale:{timeVisible:true,secondsVisible:false},rightPriceScale:{borderColor:'#2b3652'}};priceChart=L.createChart(document.getElementById('priceChart'),{...common,width:document.getElementById('priceChart').clientWidth,height:360});candleSeries=priceChart.addCandlestickSeries();ema20Series=priceChart.addLineSeries({lineWidth:2});ema50Series=priceChart.addLineSeries({lineWidth:2});volSeries=priceChart.addHistogramSeries({priceScaleId:'volume',priceFormat:{type:'volume'},scaleMargins:{top:0.8,bottom:0}});indChart=L.createChart(document.getElementById('indicatorChart'),{...common,width:document.getElementById('indicatorChart').clientWidth,height:240});rsiSeries=indChart.addLineSeries({priceScaleId:'rsi',lineWidth:2});macdSeries=indChart.addLineSeries({priceScaleId:'macd',lineWidth:2});signalSeries=indChart.addLineSeries({priceScaleId:'macd',lineWidth:2});return true;}
function fmt(v){return v===null||v===undefined?'Not enough data':Number(v).toPrecision(6)}
function renderMetrics(m){const items=[['RSI (14)',m.rsi===null?'Not enough candles':Number(m.rsi).toFixed(2)],['MACD',fmt(m.macd)],['MACD signal',fmt(m.macd_signal)],['EMA 20',fmt(m.ema20)],['EMA 50',fmt(m.ema50)],['Approx. support',fmt(m.support)],['Approx. resistance',fmt(m.resistance)]];document.getElementById('metrics').innerHTML=items.map(x=>`<div class="card"><div class="label">${x[0]}</div><div class="value">${x[1]}</div></div>`).join('');}
async function analyze(){const token=document.getElementById('token').value.trim();const tf=document.getElementById('tf').value.split(':');if(!token)return;document.getElementById('status').textContent='Loading historical candles…';try{const url=`/api/technical?token=${encodeURIComponent(token)}&timeframe=${tf[0]}&aggregate=${tf[1]}`;const r=await fetch(url);const data=await r.json();if(!r.ok)throw new Error(data.error||'Could not load data');renderMetrics(data.indicators);if(!priceChart&&!setupCharts())return;const candles=data.candles.map(c=>({time:c.time,open:c.open,high:c.high,low:c.low,close:c.close}));candleSeries.setData(candles);ema20Series.setData(data.candles.map(c=>({time:c.time,value:c.ema20})));ema50Series.setData(data.candles.map(c=>({time:c.time,value:c.ema50})));volSeries.setData(data.candles.map(c=>({time:c.time,value:c.volume,color:c.close>=c.open?'#47d7a088':'#ff707088'})));rsiSeries.setData(data.candles.filter(c=>c.rsi!==null).map(c=>({time:c.time,value:c.rsi})));macdSeries.setData(data.candles.map(c=>({time:c.time,value:c.macd})));signalSeries.setData(data.candles.map(c=>({time:c.time,value:c.macd_signal})));priceChart.timeScale().fitContent();indChart.timeScale().fitContent();document.getElementById('status').textContent=`Loaded ${data.candles.length} candles · Pool: ${data.pool} · Source: GeckoTerminal`;document.getElementById('advanced').classList.toggle('hidden',mode==='simple');}catch(e){document.getElementById('status').textContent='Analysis unavailable: '+e.message+'. Try another token or timeframe; no indicator values were invented.'}}
document.getElementById('searchForm').addEventListener('submit',e=>{e.preventDefault();analyze()});document.getElementById('simpleBtn').onclick=()=>{mode='simple';document.getElementById('advanced').classList.add('hidden');if(priceChart)priceChart.applyOptions({height:360});};document.getElementById('advancedBtn').onclick=()=>{mode='advanced';document.getElementById('advanced').classList.remove('hidden');if(indChart)indChart.applyOptions({width:document.getElementById('indicatorChart').clientWidth});};window.addEventListener('resize',()=>{if(priceChart)priceChart.applyOptions({width:document.getElementById('priceChart').clientWidth});if(indChart)indChart.applyOptions({width:document.getElementById('indicatorChart').clientWidth});});if(document.getElementById('token').value)analyze();
</script></body></html>'''

@app.route("/technical")
def technical_page():
    token = request.args.get("token", "").strip()
    return render_template_string(TECHNICAL_HTML, token=token)

@app.route("/api/technical")
def api_technical():
    mint = request.args.get("token", "").strip()
    timeframe = request.args.get("timeframe", "hour")
    aggregate = request.args.get("aggregate", "1")
    if not mint or len(mint) < 32 or len(mint) > 50:
        return jsonify({"error": "Enter a valid-looking Solana mint address (32–50 characters)."}), 400
    if timeframe not in ("minute", "hour", "day") or aggregate not in ("1", "4", "15"):
        return jsonify({"error": "Unsupported timeframe."}), 400
    if timeframe == "minute" and aggregate not in ("1", "15"):
        return jsonify({"error": "Unsupported minute interval."}), 400
    headers = {"accept": "application/json", "user-agent": "SolanaGemScanner/1.0"}
    try:
        pool_resp = requests.get(f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools?page=1", headers=headers, timeout=REQUEST_TIMEOUT)
        pool_resp.raise_for_status()
        pools = pool_resp.json().get("data", [])
        if not pools:
            return jsonify({"error": "No indexed Solana pool was found for this token."}), 404
        def liquidity(item):
            try: return float(item.get("attributes", {}).get("reserve_in_usd") or 0)
            except (TypeError, ValueError): return 0.0
        pool = max(pools, key=liquidity)
        pool_address = pool.get("attributes", {}).get("address")
        if not pool_address:
            return jsonify({"error": "The data provider did not return a pool address."}), 502
        endpoint = f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool_address}/ohlcv/{timeframe}"
        candle_resp = requests.get(endpoint, params={"aggregate": aggregate, "limit": 200, "currency": "usd", "token": "base"}, headers=headers, timeout=REQUEST_TIMEOUT)
        candle_resp.raise_for_status()
        raw = candle_resp.json().get("data", {}).get("attributes", {}).get("ohlcv_list", [])
        candles=[]
        for row in sorted(raw, key=lambda x: x[0]):
            if not isinstance(row, list) or len(row) < 6: continue
            candles.append({"time": int(row[0]), "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])})
        if len(candles) < 2:
            return jsonify({"error": "The provider returned too few historical candles for analysis."}), 422
        metrics = _indicators(candles)
        return jsonify({"token": mint, "pool": pool_address, "candles": candles, "indicators": metrics, "source": "GeckoTerminal", "count": len(candles)})
    except requests.RequestException as exc:
        log.warning("Technical analysis data request failed: %s", exc)
        return jsonify({"error": "Historical market data provider is unavailable or rate-limited. Please try again later."}), 502
    except (ValueError, TypeError, KeyError) as exc:
        log.warning("Technical analysis response parsing failed: %s", exc)
        return jsonify({"error": "Historical data was incomplete or could not be parsed."}), 502


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify(_state)

@app.route("/health")
def health():
    return jsonify({"ok":True,"app":APP_NAME,"last_scan":_state["last_scan"]})

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    port = int(os.getenv("PORT","8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
