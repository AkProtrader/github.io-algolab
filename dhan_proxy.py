"""
AlgoLab — Dhan API Proxy  v3.6
================================
Uses Tradehull(client_code, access_token) — the correct pattern from docs.

HOW TO GET YOUR ACCESS TOKEN (takes 30 seconds):
  1. Go to https://web.dhan.co
  2. Login → click your name top-right → "Apps" (or go to My Account → Dhan API)
  3. Click "Generate Token" → copy the long JWT access token
  4. Paste it in AlgoLab → Dhan Connect

OR use TOTP auto-generate (if TOTP is enabled on your account):
  - Set DHAN_PIN and DHAN_TOTP_SECRET below for fully automatic login

Token renews every 24hrs. Proxy can auto-renew via Dhan's RenewToken API.

SETUP:
  pip install flask flask-cors requests Dhan-Tradehull pyotp
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, json, os, time, socket, threading
from datetime import datetime, timezone, timedelta

app  = Flask(__name__)
CORS(app, origins="*")

DHAN_BASE    = "https://api.dhan.co"
DHAN_AUTH    = "https://auth.dhan.co"
CREDS_FILE   = ".dhan_creds.json"
GROQ_KEY     = os.environ.get("GROQ_API_KEY",
               "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")

# ── Optional: fill these for auto-login without copy-pasting token ──
DHAN_PIN         = os.environ.get("DHAN_PIN", "")          # your Dhan login PIN
DHAN_TOTP_SECRET = os.environ.get("DHAN_TOTP_SECRET", "")  # TOTP secret (not OTP)
# ────────────────────────────────────────────────────────────────────

# Global state
tsl_client  = None
dhan_token  = ""
dhan_client = ""
token_expiry = None   # datetime when token expires

# ─────────────────────────────────────────────────────────────────
# CREDENTIAL HELPERS
# ─────────────────────────────────────────────────────────────────
def load_saved_creds():
    if os.path.exists(CREDS_FILE):
        try:
            with open(CREDS_FILE) as f: return json.load(f)
        except: pass
    return {}

def save_creds(data):
    with open(CREDS_FILE, "w") as f: json.dump(data, f, indent=2)
    print(f"  Credentials saved to {CREDS_FILE}")

def get_headers():
    return {
        "Content-Type": "application/json",
        "access-token": dhan_token,
        "client-id":    dhan_client,
    }

# ─────────────────────────────────────────────────────────────────
# AUTO TOKEN GENERATION (TOTP method)
# ─────────────────────────────────────────────────────────────────
def generate_token_totp(client_id, pin, totp_secret):
    """Auto-generate Dhan access token using PIN + TOTP. No browser needed."""
    try:
        import pyotp
        totp = pyotp.TOTP(totp_secret).now()
        url  = f"{DHAN_AUTH}/app/generateAccessToken"
        r    = requests.post(url, params={"dhanClientId": client_id, "pin": pin, "totp": totp},
                             timeout=15)
        print(f"  TOTP token gen: {r.status_code} — {r.text[:150]}")
        if r.status_code == 200:
            data = r.json()
            return data.get("accessToken"), data.get("expiryTime")
    except ImportError:
        print("  pip install pyotp  to use TOTP auto-login")
    except Exception as e:
        print(f"  TOTP auth error: {e}")
    return None, None

# ─────────────────────────────────────────────────────────────────
# TOKEN RENEWAL
# ─────────────────────────────────────────────────────────────────
def renew_token():
    """Call Dhan's RenewToken API to extend current token by 24hrs."""
    global dhan_token, token_expiry
    if not dhan_token or not dhan_client:
        return False
    try:
        r = requests.get(f"{DHAN_BASE}/v2/RenewToken",
                         headers={"access-token": dhan_token, "dhanClientId": dhan_client},
                         timeout=10)
        print(f"  RenewToken: {r.status_code} — {r.text[:100]}")
        if r.status_code == 200:
            d = r.json()
            new_tok = d.get("accessToken") or d.get("access_token")
            if new_tok:
                dhan_token   = new_tok
                token_expiry = datetime.now() + timedelta(hours=24)
                saved = load_saved_creds()
                saved["access_token"] = new_tok
                save_creds(saved)
                print("  ✔ Token renewed for another 24hrs")
                return True
    except Exception as e:
        print(f"  Token renewal failed: {e}")
    return False

def start_renewal_watchdog():
    """Background thread — renews token 30min before expiry."""
    def watchdog():
        while True:
            time.sleep(1800)  # check every 30 min
            if token_expiry and dhan_token:
                remaining = (token_expiry - datetime.now()).total_seconds()
                if remaining < 1800:  # less than 30 min left
                    print(f"\n  ⚠ Token expires in {int(remaining/60)}min — renewing...")
                    renew_token()
    t = threading.Thread(target=watchdog, daemon=True)
    t.start()

# ─────────────────────────────────────────────────────────────────
# TRADEHULL INIT
# ─────────────────────────────────────────────────────────────────
def init_tradehull(client_code, access_token):
    """
    Tradehull(client_code, token_id) — token_id IS the access token (JWT).
    Source: https://github.com/TradeHull/Dhan-Tradehull
    """
    global tsl_client
    try:
        from Dhan_Tradehull import Tradehull
        tsl_client = Tradehull(client_code, access_token)
        print(f"  ✔ Tradehull initialized")
        return True
    except Exception as e:
        print(f"  ⚠ Tradehull init: {e}")
        return False

# ─────────────────────────────────────────────────────────────────
# DHAN REST HELPER
# ─────────────────────────────────────────────────────────────────
def dhan(method, path, body=None, params=None):
    """Make authenticated Dhan API call."""
    hdrs = get_headers()
    # Also accept token from browser for backwards compat
    if not hdrs["access-token"] and request:
        hdrs["access-token"] = request.headers.get("access-token", "")
        hdrs["client-id"]    = request.headers.get("client-id", dhan_client)

    url = f"{DHAN_BASE}{path}"
    try:
        if method == 'GET':
            r = requests.get(url, headers=hdrs, params=params, timeout=15)
        elif method == 'POST':
            r = requests.post(url, headers=hdrs, json=body, timeout=15)
        elif method == 'DELETE':
            r = requests.delete(url, headers=hdrs, timeout=15)

        # Auto-renew on 401
        if r.status_code == 401:
            print(f"  401 on {path} — attempting token renewal...")
            if renew_token():
                hdrs = get_headers()
                if method == 'GET':
                    r = requests.get(url, headers=hdrs, params=params, timeout=15)
        return r
    except Exception as e:
        print(f"  Dhan API error ({path}): {e}")
        raise

# ─────────────────────────────────────────────────────────────────
# AUTH ENDPOINT
# ─────────────────────────────────────────────────────────────────
@app.route("/connect", methods=["POST"])
def connect():
    global dhan_token, dhan_client, token_expiry

    body = request.json or {}
    tok  = (body.get("access_token") or body.get("token") or "").strip()
    cid  = (body.get("client_id") or body.get("client_code") or "").strip()

    print(f"\n  /connect: client={repr(cid)}, token_len={len(tok)}")

    if not tok or not cid:
        return jsonify({"success": False,
                        "error": "Provide client_id and access_token. "
                                 "Get token from: web.dhan.co → Apps → Generate Token"}), 400

    # Validate against Dhan API
    test_hdrs = {"Content-Type": "application/json",
                 "access-token": tok, "client-id": cid}
    try:
        r = requests.get(f"{DHAN_BASE}/fundlimit", headers=test_hdrs, timeout=10)
        print(f"  Dhan /fundlimit: {r.status_code} — {r.text[:150]}")
    except Exception as e:
        return jsonify({"success": False, "error": f"Cannot reach Dhan API: {e}"}), 500

    if r.status_code == 401:
        try:    err = r.json()
        except: err = {"raw": r.text[:200]}
        return jsonify({
            "success": False,
            "error":   "Token rejected by Dhan (401). Token must be the Access Token "
                       "from web.dhan.co → Apps → Generate Token. "
                       "It's a long JWT (eyJ...), NOT the short API key.",
            "dhan_error": err,
        }), 401

    # Store valid token
    dhan_token   = tok
    dhan_client  = cid
    token_expiry = datetime.now() + timedelta(hours=24)
    save_creds({"access_token": tok, "client_id": cid})
    init_tradehull(cid, tok)
    print(f"  ✔ Token valid! Client: {cid}, Funds response: {r.status_code}")

    try:    fund_data = r.json()
    except: fund_data = {}
    return jsonify({"success": True, "access_token": tok,
                    "client_id": cid, "fund_data": fund_data}), 200

@app.route("/renew", methods=["POST"])
def renew_endpoint():
    ok = renew_token()
    if ok:
        return jsonify({"success": True, "token_preview": dhan_token[:20]+"..."}), 200
    return jsonify({"success": False, "error": "Renewal failed"}), 400

# ─────────────────────────────────────────────────────────────────
# DHAN DATA ROUTES
# ─────────────────────────────────────────────────────────────────
@app.route("/fundlimit")
def fund_limit():
    r = dhan('GET', '/fundlimit')
    return jsonify(r.json()), r.status_code

@app.route("/positions")
def positions():
    r = dhan('GET', '/positions')
    d = r.json()
    return jsonify(d if isinstance(d, list) else d.get('data', d)), r.status_code

@app.route("/orders", methods=["GET"])
def get_orders():
    r = dhan('GET', '/orders')
    d = r.json()
    return jsonify(d if isinstance(d, list) else d.get('data', d)), r.status_code

@app.route("/orders", methods=["POST"])
def place_order():
    r = dhan('POST', '/orders', body=request.json)
    return jsonify(r.json()), r.status_code

@app.route("/orders/<oid>", methods=["DELETE"])
def cancel_order(oid):
    r = dhan('DELETE', f'/orders/{oid}')
    return jsonify(r.json()), r.status_code

@app.route("/holdings")
def holdings():
    r = dhan('GET', '/holdings')
    return jsonify(r.json()), r.status_code

@app.route("/optionchain")
def option_chain():
    r = dhan('GET', '/optionchain', params=dict(request.args))
    return jsonify(r.json()), r.status_code

@app.route("/ping")
def ping():
    return jsonify({
        "status":    "AlgoLab proxy v3.6 ✔",
        "connected": bool(dhan_token),
        "client_id": dhan_client or "—",
        "token":     (dhan_token[:16]+"...") if dhan_token else "none",
    }), 200

# ─────────────────────────────────────────────────────────────────
# NSE OPTION CHAIN (live ATM premiums)
# ─────────────────────────────────────────────────────────────────
nse_sess = None

def nse_session_new():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"})
    try:
        s.get("https://www.nseindia.com", timeout=10); time.sleep(0.8)
        s.headers["Referer"] = "https://www.nseindia.com/"
        s.get("https://www.nseindia.com/option-chain", timeout=10); time.sleep(0.4)
        s.headers["Referer"] = "https://www.nseindia.com/option-chain"
    except: pass
    return s

def is_market_open():
    IST = timezone(timedelta(hours=5, minutes=30))
    n = datetime.now(IST); t = n.hour*60+n.minute
    return n.weekday() < 5 and 555 <= t <= 930

@app.route("/atmpremium")
def atm_premium():
    global nse_sess
    sym  = request.args.get("symbol", "NIFTY")
    spot = float(request.args.get("spot", "0") or 0)
    step = 50 if sym == "NIFTY" else 100
    IST  = timezone(timedelta(hours=5, minutes=30))
    now  = datetime.now(IST)

    if not is_market_open():
        atm = round((spot or 24000) / step) * step
        return jsonify({"success": False, "source": "market_closed", "atm": atm,
            "tip": f"Market closed ({now.strftime('%a %H:%M')} IST)"}), 200

    for attempt in range(2):
        try:
            if nse_sess is None or attempt == 1: nse_sess = nse_session_new()
            nse_sym = "NIFTY" if sym == "NIFTY" else "BANKNIFTY"
            nse_sess.headers["X-Requested-With"] = "XMLHttpRequest"
            r = nse_sess.get(
                f"https://www.nseindia.com/api/option-chain-indices?symbol={nse_sym}",
                timeout=12)
            if r.status_code != 200: nse_sess = None; continue
            rec = r.json().get("records", {})
            rows = rec.get("data", []); und = rec.get("underlyingValue", spot or 24000)
            if not rows: nse_sess = None; continue
            atm = round((spot or und) / step) * step
            ce = pe = None; best = float("inf")
            for row in rows:
                d = abs(row.get("strikePrice", 0) - atm)
                if d < best:
                    best = d
                    cc = row.get("CE", {}); pp = row.get("PE", {})
                    if cc.get("lastPrice", 0) > 0: ce = (cc["lastPrice"], row["strikePrice"])
                    if pp.get("lastPrice", 0) > 0: pe = (pp["lastPrice"], row["strikePrice"])
            if ce and pe:
                return jsonify({"success": True, "source": "NSE", "symbol": sym,
                    "spot": round(und, 2), "atm": atm,
                    "ce_strike": ce[1], "ce_ltp": round(ce[0], 2),
                    "pe_strike": pe[1], "pe_ltp": round(pe[0], 2)}), 200
        except Exception as e:
            print(f"  NSE option chain: {e}"); nse_sess = None

    # Yahoo fallback
    try:
        yf = "%5ENSEI" if sym == "NIFTY" else "%5ENSEBANK"
        r  = requests.get(f"https://query2.finance.yahoo.com/v7/finance/options/{yf}",
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        res = r.json().get("optionChain", {}).get("result", [])
        if res:
            opts = res[0].get("options", [{}])[0]
            und  = res[0].get("quote", {}).get("regularMarketPrice", spot or 24000)
            atm  = round(und / step) * step
            calls, puts = opts.get("calls", []), opts.get("puts", [])
            ce = pe = None; bc = bp = float("inf")
            for c2 in calls:
                d = abs(c2.get("strike", 0)-atm)
                if d < bc and c2.get("lastPrice", 0) > 0: bc=d; ce=(c2["lastPrice"], c2["strike"])
            for p2 in puts:
                d = abs(p2.get("strike", 0)-atm)
                if d < bp and p2.get("lastPrice", 0) > 0: bp=d; pe=(p2["lastPrice"], p2["strike"])
            if ce and pe:
                return jsonify({"success": True, "source": "Yahoo", "symbol": sym,
                    "spot": round(und, 2), "atm": atm,
                    "ce_strike": ce[1], "ce_ltp": round(ce[0], 2),
                    "pe_strike": pe[1], "pe_ltp": round(pe[0], 2)}), 200
    except: pass

    return jsonify({"success": False, "source": "failed"}), 200

# ─────────────────────────────────────────────────────────────────
# YAHOO FINANCE QUOTES
# ─────────────────────────────────────────────────────────────────
@app.route("/quotes")
def quotes():
    syms = [s.strip() for s in request.args.get("symbols","").split(",") if s.strip()]
    sess = requests.Session()
    sess.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    try:
        sess.get("https://finance.yahoo.com", timeout=10)
        crumb = sess.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10).text.strip()
        r = sess.get("https://query1.finance.yahoo.com/v7/finance/quote",
            params={"symbols": ",".join(syms), "crumb": crumb,
                    "fields": "regularMarketPrice,regularMarketChangePercent,regularMarketChange"},
            timeout=15)
        res = r.json().get("quoteResponse", {}).get("result", [])
        if res: return jsonify({"quoteResponse": {"result": res}}), 200
    except: pass
    try:
        r = requests.get("https://query2.finance.yahoo.com/v8/finance/spark",
            params={"symbols": ",".join(syms), "range": "1d", "interval": "5m"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        spark = r.json().get("spark", {}).get("result", [])
        out = []
        for item in spark:
            s = item.get("symbol",""); resp = item.get("response",[{}])[0]
            meta = resp.get("meta",{}); closes = [x for x in
                resp.get("indicators",{}).get("quote",[{}])[0].get("close",[]) if x]
            price = closes[-1] if closes else meta.get("regularMarketPrice",0)
            prev  = meta.get("chartPreviousClose", price); chg = price - prev
            out.append({"symbol":s,"regularMarketPrice":price,"regularMarketChange":chg,
                "regularMarketChangePercent":(chg/prev*100) if prev else 0})
        if out: return jsonify({"quoteResponse": {"result": out}}), 200
    except: pass
    return jsonify({"quoteResponse": {"result": []}}), 200

# ─────────────────────────────────────────────────────────────────
# GROQ AI
# ─────────────────────────────────────────────────────────────────
@app.route("/ai", methods=["POST"])
def ai():
    body = request.json or {}
    msgs = body.get("messages", [])
    sys  = body.get("system", "")
    if sys: msgs = [{"role":"system","content":sys}] + msgs
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Content-Type":"application/json","Authorization":f"Bearer {GROQ_KEY}"},
            json={"model":"llama-3.3-70b-versatile","max_tokens":body.get("max_tokens",1000),
                  "messages":msgs,"temperature":0.7}, timeout=30)
        d = r.json()
        if "choices" in d:
            return jsonify({"content":[{"type":"text","text":d["choices"][0]["message"]["content"]}]}), 200
        return jsonify(d), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─────────────────────────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:    local_ip = socket.gethostbyname(socket.gethostname())
    except: local_ip = "127.0.0.1"

    saved = load_saved_creds()

    print("\n" + "="*58)
    print("  AlgoLab Dhan Proxy  v3.6")
    print("="*58)

    # Try TOTP auto-generate first
    if DHAN_PIN and DHAN_TOTP_SECRET and saved.get("client_id"):
        print(f"\n  TOTP auto-login for client {saved['client_id']}...")
        tok, expiry = generate_token_totp(saved["client_id"], DHAN_PIN, DHAN_TOTP_SECRET)
        if tok:
            dhan_token  = tok; dhan_client = saved["client_id"]
            token_expiry = datetime.now() + timedelta(hours=24)
            save_creds({"access_token": tok, "client_id": dhan_client})
            init_tradehull(dhan_client, dhan_token)

    # Load saved token
    if not dhan_token and saved.get("access_token"):
        dhan_token  = saved["access_token"]
        dhan_client = saved.get("client_id", "")
        print(f"\n  Saved token found → client: {dhan_client}")
        try:
            r = requests.get(f"{DHAN_BASE}/fundlimit", headers=get_headers(), timeout=8)
            if r.status_code == 200:
                d  = r.json()
                av = d.get('availableBalance', d.get('availabelBalance', d.get('sodLimit','?')))
                print(f"  ✔ Token VALID — Available Balance: ₹{av}")
                token_expiry = datetime.now() + timedelta(hours=24)
                init_tradehull(dhan_client, dhan_token)
            elif r.status_code == 401:
                print(f"  ✘ Token EXPIRED — trying renewal...")
                if not renew_token():
                    print(f"  ✘ Renewal failed. Please reconnect via AlgoLab.")
                    print(f"     web.dhan.co → Apps → Generate Token → paste in AlgoLab")
                    dhan_token = ""
        except Exception as e:
            print(f"  ⚠ Validation error: {e}")
    else:
        print("\n  No saved credentials.")
        print("  ┌─ HOW TO CONNECT ───────────────────────────────────┐")
        print("  │  1. Go to https://web.dhan.co                      │")
        print("  │  2. Login → top-right menu → Apps (or Dhan API)    │")
        print("  │  3. Click 'Generate Token' → copy the JWT          │")
        print("  │  4. Open AlgoLab → Dhan Connect → paste token      │")
        print("  └────────────────────────────────────────────────────┘")

    # Yahoo test
    try:
        ts = requests.Session(); ts.headers["User-Agent"] = "Mozilla/5.0"
        ts.get("https://finance.yahoo.com", timeout=8)
        crumb_ok = len(ts.get("https://query1.finance.yahoo.com/v1/test/getcrumb",timeout=8).text.strip()) > 3
        mkt = "✔ Yahoo Finance connected" if crumb_ok else "⚠ Yahoo (v8 fallback active)"
    except: mkt = "⚠ Yahoo Finance unreachable"

    tok_str = f"✔ {dhan_token[:20]}..." if dhan_token else "✘ Not connected — paste token in AlgoLab"
    print(f"\n  Token       : {tok_str}")
    print(f"  Market Data : {mkt}")
    print(f"  AI (Groq)   : {'✔ Llama 3.3 70B' if len(GROQ_KEY)>20 else '✘ Add key'}")
    print(f"\n  Proxy URL   : http://127.0.0.1:8765")
    print(f"  Tablet URL  : http://{local_ip}:8765")
    print("="*58 + "\n")

    start_renewal_watchdog()
    app.run(host="0.0.0.0", port=8765, debug=False)
