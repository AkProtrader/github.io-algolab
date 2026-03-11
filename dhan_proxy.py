"""
AlgoLab — Dhan API Proxy Server  v3.4
======================================
SETUP (one time):
  pip install flask flask-cors requests Dhan-Tradehull

RUN:
  python dhan_proxy.py

On first run:
  - Enter credentials in AlgoLab → Dhan Connect tab → Connect
  - Browser opens → login with Dhan → copy redirect URL → paste in terminal
  - Credentials saved to .dhan_creds.json for next time
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, json, os, time, socket
from datetime import datetime, timezone, timedelta

app  = Flask(__name__)
CORS(app, origins="*")

DHAN_BASE    = "https://api.dhan.co"
CREDS_FILE   = ".dhan_creds.json"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY",
               "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")

# ── Global state ─────────────────────────────────────────────────
tsl_client  = None   # Tradehull instance
dhan_obj    = None   # tsl_client.Dhan  (dhanhq object)
dhan_token  = None   # access token string (JWT)
dhan_client = None   # client id string

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

# ─────────────────────────────────────────────────────────────────
# TOKEN EXTRACTION
# ─────────────────────────────────────────────────────────────────
def extract_token(tsl):
    """
    Extract the Dhan JWT access token from a Tradehull v3 instance.

    From /debug output we know dhanhq.__dict__ has:
      ["client_id","access_token","base_url","timeout","header","disable_ssl","session"]

    The "header" dict contains the actual API headers including the real JWT.
    "access_token" alone is just the api_key (short string like "08fe15c2").
    """
    obj = getattr(tsl, 'Dhan', None)
    if obj is None:
        return None

    d = getattr(obj, '__dict__', {})

    # PRIMARY: read from header dict — this is what dhanhq actually sends to Dhan API
    hdr = d.get('header', {})
    if isinstance(hdr, dict):
        for k in ['access-token', 'Access-Token', 'accessToken']:
            v = hdr.get(k, '')
            if v and isinstance(v, str) and len(v) > 8:
                return v

    # SECONDARY: access_token field (may be JWT or short api_key)
    tok = d.get('access_token', '')
    if tok and isinstance(tok, str) and len(tok) > 8:
        return tok

    # TERTIARY: session headers
    sess = d.get('session') or getattr(obj, 'session', None)
    if sess:
        hdrs = dict(getattr(sess, 'headers', {}))
        for k in ['access-token', 'Access-Token', 'authorization']:
            v = hdrs.get(k, '')
            if v and len(v) > 8:
                return v.replace('Bearer ', '')

    return None

# ─────────────────────────────────────────────────────────────────
# TRADEHULL INIT
# ─────────────────────────────────────────────────────────────────
def init_tradehull(client_code, api_key, api_secret=None):
    global tsl_client, dhan_obj, dhan_token, dhan_client
    try:
        from Dhan_Tradehull import Tradehull
        print("\n  Opening browser for Dhan login...")
        print("  1. Login with Dhan credentials in the browser")
        print("  2. After redirect, copy the FULL URL")
        print("  3. Paste it in this terminal and press Enter\n")

        # Tradehull v3: only 2 positional args
        tsl_client  = Tradehull(client_code, api_key)
        dhan_client = str(client_code)
        dhan_obj    = getattr(tsl_client, 'Dhan', None)

        # Extract token
        dhan_token = extract_token(tsl_client)

        if dhan_token:
            print(f"  ✔ Token extracted: {dhan_token[:20]}...")
        else:
            print(f"  ✔ Tradehull connected — using SDK session for API calls")
            # Print what's in dhanhq for debugging
            if dhan_obj:
                d = getattr(dhan_obj, '__dict__', {})
                print(f"  dhanhq attrs: {list(d.keys())}")
                sess = getattr(dhan_obj, 'session', None)
                if sess:
                    print(f"  Session headers: {dict(sess.headers)}")

        print(f"  ✔ Client: {dhan_client}")
        return True

    except ImportError:
        print("  ✘ Dhan-Tradehull not installed: pip install Dhan-Tradehull")
        return False
    except Exception as e:
        print(f"  ✘ Tradehull init failed: {e}")
        import traceback; traceback.print_exc()
        return False

# ─────────────────────────────────────────────────────────────────
# DHAN API CALL HELPER
# ─────────────────────────────────────────────────────────────────
def get_headers(req=None):
    """
    Build Dhan API headers. Priority:
    1. Token extracted from tsl_client.Dhan (JWT from OAuth)
    2. Token sent from browser in request headers (legacy token mode)
    """
    tok = dhan_token
    cid = dhan_client or ""

    # Also try extracting fresh token each time (in case it was refreshed)
    if tsl_client and not tok:
        tok = extract_token(tsl_client)

    # Fallback: use whatever browser sent
    if req and not tok:
        tok = req.headers.get("access-token", "")
    if req and not cid:
        cid = req.headers.get("client-id", "") or dhan_client or ""

    return {
        "Content-Type": "application/json",
        "access-token": tok or "",
        "client-id":    cid,
    }

def dhan_rest(method, path, body=None, params=None):
    """
    Make a Dhan REST call.
    First tries dhanhq's own session (which has correct auth headers baked in).
    Falls back to manual token headers.
    """
    # Try using dhanhq session directly — it has the correct auth headers
    obj  = getattr(tsl_client, 'Dhan', None) if tsl_client else None
    sess = getattr(obj, '__dict__', {}).get('session') if obj else None
    if sess:
        try:
            # Update session with correct Dhan headers from dhanhq.header dict
            hdr = getattr(obj, '__dict__', {}).get('header', {})
            if hdr:
                sess.headers.update(hdr)
            url = f"{DHAN_BASE}{path}"
            if method == 'GET':
                return sess.get(url, params=params, timeout=15)
            elif method == 'POST':
                return sess.post(url, json=body, timeout=15)
            elif method == 'DELETE':
                return sess.delete(url, timeout=15)
        except Exception as e:
            print(f"  dhanhq session call failed: {e}")

    # Fallback: manual headers
    hdrs = get_headers(request)
    url  = f"{DHAN_BASE}{path}"
    if method == 'GET':
        return requests.get(url, headers=hdrs, params=params, timeout=15)
    elif method == 'POST':
        return requests.post(url, headers=hdrs, json=body, timeout=15)
    elif method == 'DELETE':
        return requests.delete(url, headers=hdrs, timeout=15)

def sdk_call(method_name, **kwargs):
    """
    Call a dhanhq SDK method directly.
    Returns (data, success) tuple.
    """
    if not dhan_obj: return None, False
    method = getattr(dhan_obj, method_name, None)
    if not method: return None, False
    try:
        result = method(**kwargs)
        # dhanhq returns {"status": "success", "data": [...]}
        if isinstance(result, dict):
            if result.get('status') == 'success':
                return result.get('data', result), True
            elif 'data' in result:
                return result['data'], True
            else:
                return result, True  # return as-is for caller to inspect
        return result, True
    except Exception as e:
        print(f"  SDK {method_name} error: {e}")
        return None, False

# ─────────────────────────────────────────────────────────────────
# AUTH ENDPOINT
# ─────────────────────────────────────────────────────────────────
@app.route("/connect", methods=["POST"])
def connect():
    global tsl_client, dhan_obj, dhan_token, dhan_client
    body = request.json or {}

    # Legacy token mode
    tok = body.get("access_token", "").strip()
    cid = body.get("client_id",    "").strip()
    if tok and cid:
        dhan_token  = tok
        dhan_client = cid
        save_creds({"mode": "token", "access_token": tok, "client_id": cid})
        return jsonify({"success": True, "mode": "token",
                        "access_token": tok, "client_id": cid}), 200

    # API key mode
    cc  = body.get("client_code", "").strip()
    ak  = body.get("api_key",     "").strip()
    aks = body.get("api_secret",  "").strip()

    if not (cc and ak):
        saved = load_saved_creds()
        cc  = cc  or saved.get("client_code", "")
        ak  = ak  or saved.get("api_key", "")
        aks = aks or saved.get("api_secret", "")

    if not (cc and ak):
        return jsonify({"success": False,
                        "error": "Provide client_code and api_key"}), 400

    save_creds({"mode": "api_key", "client_code": cc,
                "api_key": ak, "api_secret": aks})

    # Don't re-init if already connected with same client
    if tsl_client and dhan_client == str(cc):
        print(f"  Already connected as {cc} — skipping re-login")
        live_tok = dhan_token or extract_token(tsl_client) or ""
        return jsonify({
            "success":      True,
            "mode":         "api_key",
            "access_token": live_tok,
            "client_id":    dhan_client,
            "proxy_auth":   True,
            "note":         "Already connected — skipped re-login",
        }), 200

    ok = init_tradehull(cc, ak, aks)
    if not ok:
        return jsonify({"success": False,
                        "error": "Tradehull login failed — check terminal"}), 401

    # Return the real token if we got it, otherwise signal proxy-mode
    live_tok = dhan_token or extract_token(tsl_client) or ""
    return jsonify({
        "success":      True,
        "mode":         "api_key",
        "access_token": live_tok,   # may be "" — proxy uses SDK internally
        "client_id":    dhan_client,
        "proxy_auth":   True,       # tells browser: proxy handles auth
    }), 200

# ─────────────────────────────────────────────────────────────────
# DHAN DATA ROUTES  (SDK first, REST fallback)
# ─────────────────────────────────────────────────────────────────
@app.route("/fundlimit", methods=["GET"])
def fund_limit():
    data, ok = sdk_call('get_fund_limits')
    if ok and data:
        # Normalize field names dhanhq returns
        if isinstance(data, dict):
            return jsonify(data), 200
    r = dhan_rest('GET', '/fundlimit')
    return jsonify(r.json()), r.status_code

@app.route("/positions", methods=["GET"])
def positions():
    data, ok = sdk_call('get_positions')
    if ok and data is not None:
        return jsonify(data if isinstance(data, list) else []), 200
    r = dhan_rest('GET', '/positions')
    try:
        d = r.json()
        return jsonify(d if isinstance(d, list) else d.get('data', d)), r.status_code
    except:
        return jsonify([]), 200

@app.route("/orders", methods=["GET"])
def get_orders():
    data, ok = sdk_call('get_order_list')
    if ok and data is not None:
        return jsonify(data if isinstance(data, list) else []), 200
    r = dhan_rest('GET', '/orders')
    try:
        d = r.json()
        return jsonify(d if isinstance(d, list) else d.get('data', d)), r.status_code
    except:
        return jsonify([]), 200

@app.route("/orders", methods=["POST"])
def place_order():
    body = request.json or {}
    # Try SDK
    if dhan_obj and hasattr(dhan_obj, 'place_order'):
        try:
            result = dhan_obj.place_order(
                security_id      = body.get('securityId', ''),
                exchange_segment = body.get('exchangeSegment', 'NSE_EQ'),
                transaction_type = body.get('transactionType', 'BUY'),
                quantity         = int(body.get('quantity', 1)),
                order_type       = body.get('orderType', 'MARKET'),
                product_type     = body.get('productType', 'INTRADAY'),
                price            = float(body.get('price', 0)),
            )
            return jsonify(result), 200
        except Exception as e:
            print(f"  place_order SDK error: {e}")
    r = dhan_rest('POST', '/orders', body=body)
    return jsonify(r.json()), r.status_code

@app.route("/orders/<order_id>", methods=["DELETE"])
def cancel_order(order_id):
    data, ok = sdk_call('cancel_order', order_id=order_id)
    if ok: return jsonify(data or {"status": "cancelled"}), 200
    r = dhan_rest('DELETE', f'/orders/{order_id}')
    return jsonify(r.json()), r.status_code

@app.route("/holdings", methods=["GET"])
def holdings():
    data, ok = sdk_call('get_holdings')
    if ok and data is not None:
        return jsonify(data if isinstance(data, list) else []), 200
    r = dhan_rest('GET', '/holdings')
    return jsonify(r.json()), r.status_code

@app.route("/optionchain", methods=["GET"])
def option_chain():
    r = dhan_rest('GET', '/optionchain', params=dict(request.args))
    return jsonify(r.json()), r.status_code

# ─────────────────────────────────────────────────────────────────
# DEBUG ENDPOINT
# ─────────────────────────────────────────────────────────────────
@app.route("/debug2", methods=["GET"])
def debug2():
    """Show raw dhanhq header dict — reveals the actual access token."""
    obj = getattr(tsl_client, 'Dhan', None) if tsl_client else None
    if not obj:
        return jsonify({"error": "No dhanhq object"}), 200
    d = getattr(obj, '__dict__', {})
    return jsonify({
        "header_dict":   d.get('header', {}),
        "access_token":  d.get('access_token', ''),
        "client_id":     d.get('client_id', ''),
        "all_keys":      list(d.keys()),
        "extracted_tok": extract_token(tsl_client) or "NONE",
    }), 200

@app.route("/debug", methods=["GET"])
def debug_dhan():
    report = {
        "tradehull_connected": tsl_client is not None,
        "dhan_obj_present":    dhan_obj is not None,
        "dhan_client":         dhan_client or "NONE",
        "token_extracted":     bool(dhan_token),
        "token_preview":       (dhan_token[:20]+"...") if dhan_token else "NONE",
    }
    if dhan_obj:
        # List SDK methods available
        report["sdk_methods"] = sorted([
            m for m in dir(dhan_obj)
            if not m.startswith('_') and callable(getattr(dhan_obj, m, None))
        ])
        # Show dhanhq __dict__ keys
        report["dhan_dict_keys"] = list(getattr(dhan_obj, '__dict__', {}).keys())
        # Show session headers
        sess = getattr(dhan_obj, 'session', None)
        if sess:
            report["session_headers"] = dict(sess.headers)

        # Try fund_limits
        data, ok = sdk_call('get_fund_limits')
        report["fund_limits_sdk"] = {"ok": ok, "data": str(data)[:200]}

        # Try REST
        try:
            tok = dhan_token or extract_token(tsl_client) or ""
            r   = requests.get(f"{DHAN_BASE}/fundlimit",
                               headers={"access-token": tok,
                                        "client-id":    dhan_client or "",
                                        "Content-Type": "application/json"},
                               timeout=8)
            report["fund_limits_rest"] = {"status": r.status_code, "body": r.text[:300]}
        except Exception as e:
            report["fund_limits_rest_err"] = str(e)

    return jsonify(report), 200

# ─────────────────────────────────────────────────────────────────
# PING
# ─────────────────────────────────────────────────────────────────
@app.route("/ping", methods=["GET"])
def ping():
    tok = dhan_token or (extract_token(tsl_client) if tsl_client else "")
    return jsonify({
        "status":        "AlgoLab proxy running ✔",
        "tradehull":     tsl_client is not None,
        "token_set":     bool(tok),
        "client_id":     dhan_client or "—",
        "token_preview": (tok[:12]+"...") if tok else "none",
    }), 200

# ─────────────────────────────────────────────────────────────────
# NSE OPTION CHAIN
# ─────────────────────────────────────────────────────────────────
nse_session = None

def get_nse_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "Accept":          "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "DNT":             "1",
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
    symbol   = request.args.get("symbol", "NIFTY")
    spot_raw = request.args.get("spot", "0")
    nse_sym  = "NIFTY" if symbol == "NIFTY" else "BANKNIFTY"
    step     = 50 if symbol == "NIFTY" else 100
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
                rec     = oc_r.json().get("records", {})
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
                d = abs(call.get("strike",0)-atm)
                if d < bc and call.get("lastPrice",0) > 0: bc=d; ce_ltp=call["lastPrice"]; ce_strike=call["strike"]
            for put in puts:
                d = abs(put.get("strike",0)-atm)
                if d < bp and put.get("lastPrice",0) > 0: bp=d; pe_ltp=put["lastPrice"]; pe_strike=put["strike"]
            if ce_ltp and pe_ltp:
                return jsonify({
                    "success": True, "source": "Yahoo", "symbol": symbol,
                    "spot": round(und, 2), "atm": atm,
                    "ce_strike": ce_strike, "ce_ltp": round(ce_ltp, 2),
                    "pe_strike": pe_strike, "pe_ltp": round(pe_ltp, 2),
                }), 200
    except: pass

    return jsonify({
        "success": False, "source": "fetch_failed", "nse_error": nse_error,
        "atm": atm if "atm" in locals() else 0,
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
        crumb = session.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10).text.strip()
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
            price = closes[-1] if closes else meta.get("regularMarketPrice",0)
            prev  = meta.get("chartPreviousClose", price)
            chg   = price - prev
            results.append({"symbol": sym, "regularMarketPrice": price,
                            "regularMarketChange": chg,
                            "regularMarketChangePercent": (chg/prev*100) if prev else 0,
                            "regularMarketPreviousClose": prev})
        if results: return jsonify({"quoteResponse": {"result": results}}), 200
    except: pass
    return jsonify({"quoteResponse": {"result": []}, "error": "All sources failed"}), 200

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
            return jsonify({"content": [{"type":"text",
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

    print("\n" + "="*54)
    print("  AlgoLab Dhan Proxy  v3.4")
    print("="*54)

    # Auto-init if saved creds exist
    if saved.get("mode") == "api_key":
        cc  = saved.get("client_code","")
        ak  = saved.get("api_key","")
        aks = saved.get("api_secret","")
        if cc and ak:
            print(f"\n  Saved creds → client: {cc}")
            init_tradehull(cc, ak, aks)
        else:
            print("\n  ⚠ Incomplete saved creds — connect via AlgoLab")
    elif saved.get("mode") == "token":
        dhan_token  = saved.get("access_token","")
        dhan_client = saved.get("client_id","")
        print(f"\n  Token mode loaded: client {dhan_client}")
    else:
        print("\n  No saved creds — connect via AlgoLab → Dhan Connect tab")

    # Yahoo test
    try:
        ts = requests.Session()
        ts.headers.update({"User-Agent": "Mozilla/5.0"})
        ts.get("https://finance.yahoo.com", timeout=8)
        crumb_ok = len(ts.get("https://query1.finance.yahoo.com/v1/test/getcrumb",
                              timeout=8).text.strip()) > 3
        mkt_status = "✔ Yahoo Finance connected" if crumb_ok else "⚠ Yahoo crumb failed (v8 active)"
    except:
        mkt_status = "⚠ Yahoo Finance unreachable"

    print(f"\n  Port        : 8765")
    print(f"  Tradehull   : {'✔ Connected' if tsl_client else '⚠ Not connected'}")
    print(f"  Token       : {'✔ ' + dhan_token[:16] + '...' if dhan_token else '⚠ Not extracted (using SDK session)'}")
    print(f"  Market Data : {mkt_status}")
    print(f"  AI (Groq)   : {'✔ Ready' if has_key else '✘ Add key from console.groq.com'}")
    print(f"\n  Proxy URL   : http://127.0.0.1:8765")
    print(f"  Tablet URL  : http://{local_ip}:8765")
    print(f"\n  Debug URL   : http://127.0.0.1:8765/debug")
    print("="*54 + "\n")

    app.run(host="0.0.0.0", port=8765, debug=False)
