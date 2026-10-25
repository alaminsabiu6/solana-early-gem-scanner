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
MAX_PROFILES_PER_SCAN = max(1, min(10, int(os.getenv("MAX_PROFILES_PER_SCAN", "8"))))
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "5000"))
MAX_PAIR_AGE_MINUTES = float(os.getenv("MAX_PAIR_AGE_MINUTES", "60"))
MIN_TXNS_5M = int(os.getenv("MIN_TXNS_5M", "10"))
SECURITY_CHECK_LIMIT = max(1, min(8, int(os.getenv("SECURITY_CHECK_LIMIT", "5"))))
REQUEST_TIMEOUT = 12
DEX_BASE = "https://api.dexscreener.com"
SOLANA_RPC = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("scanner")
app = Flask(__name__)
_lock = threading.Lock()
_security_cache = {}
_security_cache_lock = threading.Lock()
_state = {
    "last_scan": None, "status": "Starting",
    "message": "Waiting for first scan.", "tokens": [],
    "scan_count": 0, "errors": []
}

HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="45">
<title>Solana Early Gem Scanner</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--card:#151d32;--line:#2b3652;--muted:#9eabc7;--text:#edf2ff;--green:#47d7a0;--yellow:#ffd166;--red:#ff7070}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
header{padding:24px 18px;background:linear-gradient(120deg,#16244a,#123b43);border-bottom:1px solid var(--line)}
main{max-width:1250px;margin:auto;padding:18px}.tag{display:inline-block;border:1px solid #3b547c;border-radius:99px;padding:4px 10px;color:#c7d8ff;font-size:12px}
h1{font-size:clamp(25px,4vw,38px);margin:8px 0}p{color:var(--muted);margin:6px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:18px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px}.label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}.value{font-size:23px;font-weight:700;margin-top:5px}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin:15px 0}.controls input{background:#11182a;color:var(--text);border:1px solid var(--line);padding:10px;border-radius:9px;min-width:190px}
.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:14px}table{width:100%;border-collapse:collapse;min-width:1120px;background:var(--card)}th,td{text-align:left;padding:12px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--muted);text-transform:uppercase}tr:last-child td{border-bottom:0}
.symbol{font-weight:750}.sub{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.score{font-weight:800;font-size:17px}.good{color:var(--green)}.warn{color:var(--yellow)}.bad{color:var(--red)}a{color:#9bc4ff;text-decoration:none}a:hover{text-decoration:underline}.pill{display:inline-block;border-radius:7px;padding:3px 7px;font-size:12px;font-weight:700;background:#26334c}.foot{font-size:12px;color:var(--muted);margin:18px 2px}.empty{padding:25px;color:var(--muted);text-align:center}.risklist{margin:6px 0 0;padding-left:16px;font-size:12px;color:var(--muted)}
</style></head><body>
<header><main style="padding:0 18px"><span class="tag">SOLANA · SECURITY UPGRADE</span>
<h1>Solana Early Gem Scanner</h1>
<p>Market screening plus basic on-chain mint-authority and token-account concentration checks.</p></main></header>
<main>
<div class="grid">
<div class="card"><div class="label">Scanner status</div><div class="value">{{ state.status }}</div><p>{{ state.message }}</p></div>
<div class="card"><div class="label">Pairs passing filters</div><div class="value">{{ state.tokens|length }}</div><p>Current scan results</p></div>
<div class="card"><div class="label">Last scan (UTC)</div><div class="value" style="font-size:17px">{{ state.last_scan or "Not yet" }}</div><p>Periodic refresh</p></div>
<div class="card"><div class="label">Scan count</div><div class="value">{{ state.scan_count }}</div><p>Since this process started</p></div>
</div>
<div class="controls"><input id="filter" placeholder="Filter by token / symbol…" oninput="filterRows()">
<span class="tag">Min liquidity: ${{ "{:,.0f}".format(min_liquidity) }}</span>
<span class="tag">Max age: {{ max_age }} min</span><span class="tag">Security checks per scan: {{ security_limit }}</span></div>
<div class="tablewrap"><table id="tokens"><thead><tr>
<th>Token</th><th>Age</th><th>Liquidity</th><th>5m volume</th><th>Buys / sells</th><th>5m change</th><th>Score</th><th>Market screen</th><th>On-chain checks</th><th>Links</th>
</tr></thead><tbody>
{% for t in state.tokens %}<tr>
<td><div class="symbol">{{ t.symbol }}</div><div>{{ t.name }}</div><div class="sub">{{ t.address }}</div></td>
<td>{{ "%.1f"|format(t.age_min) }} min</td><td>${{ "{:,.0f}".format(t.liquidity) }}</td>
<td>${{ "{:,.0f}".format(t.vol5) }}</td><td>{{ t.buys5 }} / {{ t.sells5 }}</td>
<td class="{{ 'good' if t.change5 > 0 else 'bad' if t.change5 < 0 else '' }}">{{ "%.2f"|format(t.change5) }}%</td>
<td class="score">{{ t.score }}/100</td>
<td><span class="pill {{ 'good' if t.label == 'WATCH' else 'warn' if t.label == 'CAUTION' else 'bad' }}">{{ t.label }}</span><ul class="risklist">{% for r in t.reasons %}<li>{{ r }}</li>{% endfor %}</ul></td>
<td><span class="pill {{ 'good' if t.security.status == 'No obvious mint-authority warning' else 'warn' if t.security.status in ['Partial check','Unknown'] else 'bad' }}">{{ t.security.status }}</span>
<ul class="risklist">{% for r in t.security.warnings %}<li>{{ r }}</li>{% endfor %}</ul>
{% if t.security.top10_pct is not none %}<div class="sub">Top 10 token accounts: {{ "%.1f"|format(t.security.top10_pct) }}% of supply</div>{% endif %}
</td>
<td><a href="{{ t.dex_url }}" target="_blank" rel="noopener">DEX chart</a><br><a href="https://solscan.io/token/{{ t.address }}" target="_blank" rel="noopener">Solscan</a><br><a href="https://jup.ag/swap/SOL-{{ t.address }}" target="_blank" rel="noopener">Open swap</a></td>
</tr>{% else %}<tr><td colspan="10" class="empty">No pairs currently meet your filters. Discovery data is limited; this does not mean no new coins exist.</td></tr>{% endfor %}
</tbody></table></div>
<div class="card" style="margin-top:16px"><b>Read this before trading</b>
<p><b>These checks are warnings, not a guarantee that a token is safe.</b> The scanner checks whether mint/freeze authorities appear active and estimates the share held by the 10 largest token accounts. Token accounts are not the same as unique owners, so concentration can be overstated or understated.</p>
<p>This MVP does not prove a token is sellable and does not verify LP lock/burn status, malicious Token-2022 extensions, creator-wallet history, or all honeypot/rug-pull patterns. An unavailable RPC result is marked Unknown/Partial, never treated as safe. WATCH is not a buy signal.</p>
<p>No wallet private key is requested or stored. Automatic buying/selling is disabled.</p></div>
<div class="foot">Data: DEX Screener public API and Solana mainnet RPC. Data may be delayed, rate-limited, or incomplete.</div>
</main><script>
function filterRows(){let q=document.getElementById('filter').value.toLowerCase();document.querySelectorAll('#tokens tbody tr').forEach(r=>r.style.display=r.innerText.toLowerCase().includes(q)?'':'none')}
</script></body></html>"""

def get_json(url):
    response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "SolanaEarlyGemScanner/1.1"})
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

def onchain_security(mint):
    now = time.time()
    with _security_cache_lock:
        cached = _security_cache.get(mint)
        if cached and now - cached["time"] < 600:
            return cached["data"]
    result = {"status":"Unknown", "warnings":["Security data unavailable; do not assume safe."],
              "mint_authority":None, "freeze_authority":None, "top10_pct":None}
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
                result["mint_authority"] = (base64.b64encode(raw[4:36]).decode("ascii") if mint_opt else None)
                result["freeze_authority"] = (base64.b64encode(raw[50:82]).decode("ascii") if freeze_opt else None)
                # Display active vs disabled only; raw pubkeys are not base58, so do not present them as addresses.
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
                        if result["top10_pct"] >= 70:
                            result["warnings"].append("Top 10 token accounts hold ≥70% of reported supply; concentration risk.")
                        elif result["top10_pct"] >= 40:
                            result["warnings"].append("Top 10 token accounts hold ≥40% of reported supply; review concentration.")
                        else:
                            result["warnings"].append("Top-10 token-account concentration is below 40%; this alone does not prove broad ownership.")
                except Exception as exc:
                    result["warnings"].append("Could not calculate top-10 account concentration.")
                    log.info("Largest account check failed for %s: %s", mint[:8], exc)
    except Exception as exc:
        result["status"] = "Unknown"
        result["warnings"] = [f"RPC check failed ({type(exc).__name__}); treat security as unverified."]
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
    elif change5 > 25: score += 3; reasons.append("sharp price spike")
    elif change5 < -10: score -= 8; reasons.append("price falling")
    if not address: score -= 20; reasons.append("missing token address")
    if liquidity < MIN_LIQUIDITY_USD: score = min(score, 39)
    score = max(0, min(100, score))
    if score >= 70 and liquidity >= MIN_LIQUIDITY_USD and total_txns >= MIN_TXNS_5M:
        label = "WATCH"
    elif score >= 40: label = "CAUTION"
    else: label = "AVOID"
    if not reasons: reasons = ["market screen passed"]
    return {
        "name":name,"symbol":symbol,"address":address,"age_min":age_min,
        "liquidity":liquidity,"vol5":m5vol,"buys5":buys,"sells5":sells,
        "change5":change5,"score":score,"label":label,"reasons":reasons,
        "dex_url":pair.get("url") or f"https://dexscreener.com/solana/{pair.get('pairAddress','')}",
        "pair_address":pair.get("pairAddress",""), "security":{"status":"Not checked","warnings":["Not selected for on-chain checks this scan."],"top10_pct":None}
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
        # Run RPC checks only for the top few candidates to limit rate usage.
        for item in ranked[:SECURITY_CHECK_LIMIT]:
            item["security"] = onchain_security(item["address"])
            # Security findings lower the market score and can prevent WATCH classification.
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
            item["label"] = "WATCH" if item["score"] >= 70 else "CAUTION" if item["score"] >= 40 else "AVOID"
            time.sleep(0.15)
        ranked.sort(key=lambda x:(x["score"],x["vol5"],x["liquidity"]), reverse=True)
        with _lock:
            _state["tokens"] = ranked[:100]
            _state["last_scan"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            _state["status"] = "Running"
            _state["message"] = f"Checked {min(len(sol_profiles), MAX_PROFILES_PER_SCAN)} Solana profiles; {len(ranked)} pairs passed market filters. On-chain checks run for up to {SECURITY_CHECK_LIMIT} candidates."
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
                                  max_age=MAX_PAIR_AGE_MINUTES, security_limit=SECURITY_CHECK_LIMIT)

@app.route("/api/status")
def api_status():
    with _lock: return jsonify(_state)

@app.route("/health")
def health():
    return jsonify({"ok":True,"app":APP_NAME,"last_scan":_state["last_scan"]})

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    port = int(os.getenv("PORT","8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
