"""
AlgoLab — Dhan API Proxy Server  v3.5
======================================
SETUP:
  pip install flask flask-cors requests Dhan-Tradehull

HOW TO GET YOUR ACCESS TOKEN (one time):
  1. Go to: https://dhanhq.co/  → Login → My Profile → Data APIs
  2. Create or copy your Access Token (long JWT string)
  3. Enter it in AlgoLab → Dhan Connect → Token (24hr) tab
  OR paste it below as DHAN_ACCESS_TOKEN for auto-connect on startup

  Note: "API Key" (08fe15c2) is NOT the access token.
        The real token is a long JWT like: eyJhbGciOi...
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, json, os, time, socket
from datetime import datetime, timezone, timedelta

app  = Flask(__name__)
CORS(app, origins="*")

DHAN_BASE    = "https://api.dhan.co"
DHAN_BASE_V2 = "https://api.dhan.co/v2"
CREDS_FILE   = ".dhan_creds.json"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY",
               "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")

# ── Paste your Dhan Access Token here for auto-connect on startup ─
# Get it from: https://dhanhq.co → My Profile → Data APIs → Access Token
DHAN_ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "")
DHAN_CLIENT_ID    = os.environ.get("DHAN_CLIENT_ID", "")
# ─────────────────────────────────────────────────────────────────

# Global state
tsl_client  = None
dhan_token  = DHAN_ACCESS_TOKEN or ""
dhan_client = DHAN_CLIENT_ID   or ""

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

def get_headers(req=None):
    """Build Dhan API headers, always using the best available token."""
    tok = dhan_token or ""
    cid = dhan_client or ""
    if req and not tok:
        tok = req.headers.get("access-token", "")
    if req and not cid:
        cid = req.headers.get("client-id", "")
    return {
        "Content-Type": "application/json",
        "access-token": tok,
        "client-id":    cid,
    }

# ─────────────────────────────────────────────────────────────────
# TRADEHULL INIT  (for Tradehull API methods — historical data etc)
# ─────────────────────────────────────────────────────────────────
def init_tradehull_with_token(client_code, access_token):
    """Initialize Tradehull using the real JWT access token."""
    global tsl_client
    try:
        from Dhan_Tradehull import Tradehull
        # Tradehull v3: Tradehull(client_code, api_key)
        # api_key here = the actual access token (JWT)
        tsl_client = Tradehull(client_code, access_token)
        print(f"  ✔ Tradehull initialized with real token")
        return True
    except Exception as e:
        print(f"  ⚠ Tradehull init: {e} (continuing with REST API only)")
        return False

# ─────────────────────────────────────────────────────────────────
# DHAN REST HELPER
# ─────────────────────────────────────────────────────────────────
def dhan_rest(method, path, body=None, params=None, req=None):
    hdrs = get_headers(req or request)
    url  = f"{DHAN_BASE}{path}"
    if method == 'GET':
        return requests.get(url, headers=hdrs, params=params, timeout=15)
    elif method == 'POST':
        return requests.post(url, headers=hdrs, json=body, timeout=15)
    elif method == 'DELETE':
        return requests.delete(url, headers=hdrs, timeout=15)

# ─────────────────────────────────────────────────────────────────
# AUTH ENDPOINT
# ─────────────────────────────────────────────────────────────────
@app.route("/connect", methods=["POST"])
def connect():
    global dhan_token, dhan_client, tsl_client
    body = request.json or {}

    print(f"
  /connect received keys: {list(body.keys())}")
    print(f"  client_id={repr(body.get('client_id',''))[:30]}")
    print(f"  access_token len={len(body.get('access_token',''))}")

    # Extract credentials from request
    tok = (body.get("access_token") or body.get("token") or "").strip()
    cid = (body.get("client_id") or body.get("client_code") or "").strip()

    if not tok or not cid:
        saved = load_saved_creds()
        tok = tok or saved.get("access_token", "")
        cid = cid or saved.get("client_id", "")

    if not tok or not cid:
        print(f"  400: tok={bool(tok)} cid={bool(cid)}")
        return jsonify({"success": False,
                        "error": "Provide access_token and client_id"}), 400

    print(f"  Validating: client={cid}, token_len={len(tok)}, token_start={tok[:20]}...")

    # Try both v1 and v2 Dhan API endpoints
    auth_headers = {
        "access-token": tok,
        "client-id":    cid,
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }
    test = None
    for base in [DHAN_BASE, f"{DHAN_BASE}/v2"]:
        try:
            r = requests.get(f"{base}/fundlimit", headers=auth_headers, timeout=10)
            print(f"  [{base}] → {r.status_code}: {r.text[:120]}")
            if r.status_code == 200:
                test = r; break
            elif r.status_code == 401:
                test = r  # keep last 401 to report
        except Exception as e:
            print(f"  [{base}] error: {e}")

    if not test:
        return jsonify({"success": False, "error": "Could not reach Dhan API"}), 500

    if test.status_code == 401:
        err_body = {}
        try: err_body = test.json()
        except: err_body = {"raw": test.text[:200]}
        return jsonify({"success": False,
                        "error": "Token rejected by Dhan — get a fresh token from: dhanhq.co → My Profile → Data APIs → Access Token",
                        "hint": "The token must be the long JWT from Dhan console, not the API key",
                        "dhan_error": err_body}), 401

    # Token valid — store globally
    dhan_token  = tok
    dhan_client = cid
    save_creds({"access_token": tok, "client_id": cid, "mode": "token"})
    print(f"  ✔ Token accepted! Client: {cid}")

    # Init Tradehull with real JWT for historical data / option chain
    init_tradehull_with_token(cid, tok)

    try:    fund_data = test.json()
    except: fund_data = {}
    return jsonify({
        "success":    True,
        "access_token": tok,
        "client_id":  cid,
        "fund_data":  fund_data,
    }), 200

# ─────────────────────────────────────────────────────────────────
# DHAN DATA ROUTES
# ─────────────────────────────────────────────────────────────────
@app.route("/fundlimit", methods=["GET"])
def fund_limit():
    r = dhan_rest('GET', '/fundlimit')
    return jsonify(r.json()), r.status_code

@app.route("/positions", methods=["GET"])
def positions():
    r = dhan_rest('GET', '/positions')
    d = r.json()
    return jsonify(d if isinstance(d, list) else d.get('data', d)), r.status_code

@app.route("/orders", methods=["GET"])
def get_orders():
    r = dhan_rest('GET', '/orders')
    d = r.json()
    return jsonify(d if isinstance(d, list) else d.get('data', d)), r.status_code

@app.route("/orders", methods=["POST"])
def place_order():
    r = dhan_rest('POST', '/orders', body=request.json)
    return jsonify(r.json()), r.status_code

@app.route("/orders/<order_id>", methods=["DELETE"])
def cancel_order(order_id):
    r = dhan_rest('DELETE', f'/orders/{order_id}')
    return jsonify(r.json()), r.status_code

@app.route("/holdings", methods=["GET"])
def holdings():
    r = dhan_rest('GET', '/holdings')
    return jsonify(r.json()), r.status_code

@app.route("/optionchain", methods=["GET"])
def option_chain():
    r = dhan_rest('GET', '/optionchain', params=dict(request.args))
    return jsonify(r.json()), r.status_code

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({
        "status":        "AlgoLab proxy running ✔",
        "token_set":     bool(dhan_token),
        "client_id":     dhan_client or "—",
        "token_preview": (dhan_token[:16]+"...") if dhan_token else "none",
    }), 200

# ─────────────────────────────────────────────────────────────────
# NSE OPTION CHAIN
# ─────────────────────────────────────────────────────────────────
nse_session = None

def get_nse_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "DNT": "1",
    })
    try:
        s.get("https://www.nseindia.com", timeout=10); time.sleep(1)
        s.headers.update({"Referer": "https://www.nseindia.com/"})
        s.get("https://www.nseindia.com/option-chain", timeout=10); time.sleep(0.5)
        s.headers.update({"Referer": "https://www.nseindia.com/option-chain"})
        s.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=10)
        time.sleep(0.3)
    except Exception as e:
        print(f"  NSE session: {e}")
    return s

def is_market_open():
    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST)
    t   = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9*60+15 <= t <= 15*60+30

@app.route("/atmpremium", methods=["GET"])
def atm_premium():
    global nse_session
    symbol = request.args.get("symbol", "NIFTY")
    spot_raw = request.args.get("spot", "0")
    nse_sym = "NIFTY" if symbol == "NIFTY" else "BANKNIFTY"
    step = 50 if symbol == "NIFTY" else 100
    nse_error = ""
    IST = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(IST)

    if not is_market_open():
        spot = float(spot_raw) if float(spot_raw) > 0 else 24000
        atm  = round(spot / step) * step
        day  = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][now_ist.weekday()]
        return jsonify({
            "success": False, "source": "market_closed", "atm": atm,
            "tip": f"Market closed ({day} {now_ist.strftime('%H:%M')} IST). App uses B-S estimate.",
        }), 200

    try:
        spot = float(spot_raw) if float(spot_raw) > 0 else None
        for attempt in range(2):
            try:
                if nse_session is None or attempt == 1:
                    nse_session = get_nse_session()
                nse_session.headers.update({
                    "Referer": "https://www.nseindia.com/option-chain",
                    "X-Requested-With": "XMLHttpRequest",
                })
                oc_r = nse_session.get(
                    f"https://www.nseindia.com/api/option-chain-indices?symbol={nse_sym}",
                    timeout=12)
                if oc_r.status_code != 200:
                    nse_error = f"NSE HTTP {oc_r.status_code}"; nse_session = None; continue
                rec = oc_r.json().get("records", {})
                oc_list = rec.get("data", [])
                und_val = rec.get("underlyingValue", 0)
                if not oc_list:
                    nse_error = "NSE 0 rows"; nse_session = None; continue
                if not spot: spot = und_val
                atm = round(spot / step) * step
                ce_ltp = pe_ltp = ce_strike = pe_strike = None
                best = float("inf")
                for row in oc_list:
                    sp = row.get("strikePrice", 0); diff = abs(sp - atm)
                    if diff < best:
                        best = diff
                        ce = row.get("CE", {}); pe = row.get("PE", {})
                        if ce.get("lastPrice", 0) > 0: ce_ltp = ce["lastPrice"]; ce_strike = sp
                        if pe.get("lastPrice", 0) > 0: pe_ltp = pe["lastPrice"]; pe_strike = sp
                if ce_ltp and pe_ltp:
                    return jsonify({
                        "success": True, "source": "NSE", "symbol": symbol,
                        "spot": round(und_val, 2), "atm": atm,
                        "ce_strike": ce_strike, "ce_ltp": round(ce_ltp, 2),
                        "pe_strike": pe_strike, "pe_ltp": round(pe_ltp, 2),
                        "rows": len(oc_list),
                    }), 200
                nse_error = f"No ATM match ({len(oc_list)} rows)"; break
            except Exception as ex:
                nse_error = str(ex); nse_session = None
    except Exception as e:
        nse_error = str(e)

    try:
        spot = float(spot_raw) if float(spot_raw) > 0 else 24000
        atm  = round(spot / step) * step
        yf_s = "%5ENSEI" if symbol == "NIFTY" else "%5ENSEBANK"
        yf_r = requests.get(f"https://query2.finance.yahoo.com/v7/finance/options/{yf_s}",
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        res = yf_r.json().get("optionChain", {}).get("result", [])
        if res:
            opts = res[0].get("options", [{}])[0]
            und  = res[0].get("quote", {}).get("regularMarketPrice", spot)
            calls, puts = opts.get("calls", []), opts.get("puts", [])
            ce_ltp = pe_ltp = ce_strike = pe_strike = None
            bc = bp = float("inf")
            for call in calls:
                d = abs(call.get("strike", 0) - atm)
                if d < bc and call.get("lastPrice", 0) > 0:
                    bc = d; ce_ltp = call["lastPrice"]; ce_strike = call["strike"]
            for put in puts:
                d = abs(put.get("strike", 0) - atm)
                if d < bp and put.get("lastPrice", 0) > 0:
                    bp = d; pe_ltp = put["lastPrice"]; pe_strike = put["strike"]
            if ce_ltp and pe_ltp:
                return jsonify({
                    "success": True, "source": "Yahoo", "symbol": symbol,
                    "spot": round(und, 2), "atm": atm,
                    "ce_strike": ce_strike, "ce_ltp": round(ce_ltp, 2),
                    "pe_strike": pe_strike, "pe_ltp": round(pe_ltp, 2),
                }), 200
    except: pass

    return jsonify({
        "success": False, "source": "fetch_failed",
        "nse_error": nse_error, "atm": atm if "atm" in locals() else 0,
    }), 200

# ─────────────────────────────────────────────────────────────────
# YAHOO FINANCE QUOTES RELAY
# ─────────────────────────────────────────────────────────────────
@app.route("/quotes", methods=["GET"])
def yf_quotes():
    symbols = [s.strip() for s in request.args.get("symbols","").split(",") if s.strip()]
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                          "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"})
    try:
        session.get("https://finance.yahoo.com", timeout=10)
        crumb = session.get("https://query1.finance.yahoo.com/v1/test/getcrumb",
                            timeout=10).text.strip()
        r = session.get("https://query1.finance.yahoo.com/v7/finance/quote",
                        params={"symbols": ",".join(symbols), "crumb": crumb,
                                "fields": "regularMarketPrice,regularMarketChangePercent,"
                                          "regularMarketChange,regularMarketPreviousClose"},
                        timeout=15)
        results = r.json().get("quoteResponse", {}).get("result", [])
        if results: return jsonify({"quoteResponse": {"result": results}}), 200
    except: pass
    try:
        r = requests.get("https://query2.finance.yahoo.com/v8/finance/spark",
                         params={"symbols": ",".join(symbols), "range": "1d", "interval": "5m"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        spark = r.json().get("spark", {}).get("result", [])
        results = []
        for item in spark:
            sym  = item.get("symbol","")
            resp = item.get("response",[{}])[0]
            meta = resp.get("meta",{})
            closes = [c for c in resp.get("indicators",{}).get("quote",[{}])[0].get("close",[]) if c]
            price = closes[-1] if closes else meta.get("regularMarketPrice", 0)
            prev  = meta.get("chartPreviousClose", price)
            chg   = price - prev
            results.append({"symbol": sym, "regularMarketPrice": price,
                            "regularMarketChange": chg,
                            "regularMarketChangePercent": (chg/prev*100) if prev else 0,
                            "regularMarketPreviousClose": prev})
        if results: return jsonify({"quoteResponse": {"result": results}}), 200
    except: pass
    return jsonify({"quoteResponse": {"result": []}}), 200

# ─────────────────────────────────────────────────────────────────
# GROQ AI RELAY
# ─────────────────────────────────────────────────────────────────
@app.route("/ai", methods=["POST"])
def ai_relay():
    body     = request.json or {}
    messages = body.get("messages", [])
    sys_text = body.get("system", "")
    if sys_text:
        messages = [{"role": "system", "content": sys_text}] + messages
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                          headers={"Content-Type": "application/json",
                                   "Authorization": f"Bearer {GROQ_API_KEY}"},
                          json={"model": "llama-3.3-70b-versatile",
                                "max_tokens": body.get("max_tokens", 1000),
                                "messages": messages, "temperature": 0.7},
                          timeout=30)
        data = r.json()
        if "choices" in data:
            return jsonify({"content": [{"type": "text",
                "text": data["choices"][0]["message"]["content"]}]}), 200
        return jsonify(data), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─────────────────────────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    hostname = socket.gethostname()
    try:    local_ip = socket.gethostbyname(hostname)
    except: local_ip = "127.0.0.1"

    has_key = len(GROQ_API_KEY) > 20 and "YOUR_GROQ" not in GROQ_API_KEY
    saved   = load_saved_creds()

    print("\n" + "="*56)
    print("  AlgoLab Dhan Proxy  v3.5")
    print("="*56)

    # Auto-load saved token
    if not dhan_token and saved.get("access_token"):
        dhan_token  = saved["access_token"]
        dhan_client = saved.get("client_id", "")
        print(f"\n  ✔ Token loaded from {CREDS_FILE}")
        print(f"    Client: {dhan_client}")

        # Validate saved token
        try:
            test = requests.get(f"{DHAN_BASE}/fundlimit",
                               headers={"access-token": dhan_token,
                                        "client-id": dhan_client,
                                        "Content-Type": "application/json"},
                               timeout=8)
            if test.status_code == 200:
                d = test.json()
                av = d.get('availableBalance', d.get('availabelBalance', d.get('sodLimit', '?')))
                print(f"    ✔ Token VALID — Available: ₹{av}")
                init_tradehull_with_token(dhan_client, dhan_token)
            else:
                print(f"    ✘ Token EXPIRED (HTTP {test.status_code})")
                print(f"    → Get fresh token: dhanhq.co → My Profile → Data APIs")
                dhan_token = ""   # clear expired token
        except Exception as e:
            print(f"    ⚠ Token validation failed: {e}")
    else:
        print("\n  No saved token.")
        print("  → In AlgoLab: Dhan Connect → 🔐 Token (24hr) tab")
        print("  → Paste Access Token from: dhanhq.co → My Profile → Data APIs")

    # Yahoo Finance test
    try:
        ts = requests.Session()
        ts.headers.update({"User-Agent": "Mozilla/5.0"})
        ts.get("https://finance.yahoo.com", timeout=8)
        crumb_ok = len(ts.get("https://query1.finance.yahoo.com/v1/test/getcrumb",
                              timeout=8).text.strip()) > 3
        mkt_status = "✔ Yahoo Finance connected" if crumb_ok else "⚠ crumb failed (v8 active)"
    except:
        mkt_status = "⚠ Yahoo Finance unreachable"

    tok_status = f"✔ {dhan_token[:16]}..." if dhan_token else "✘ Not set — connect via AlgoLab"
    print(f"\n  Token       : {tok_status}")
    print(f"  Market Data : {mkt_status}")
    print(f"  AI (Groq)   : {'✔ Ready — Llama 3.3 70B' if has_key else '✘ Add key'}")
    print(f"\n  Proxy URL   : http://127.0.0.1:8765")
    print(f"  Tablet URL  : http://{local_ip}:8765")
    print("="*56 + "\n")

    app.run(host="0.0.0.0", port=8765, debug=False)
