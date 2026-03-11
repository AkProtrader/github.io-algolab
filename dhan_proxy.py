"""
AlgoLab — Dhan API Proxy Server  v3.3
======================================
SETUP (one time):
  pip install flask flask-cors requests Dhan-Tradehull

RUN:
  python dhan_proxy.py

On first run:
  - Enter your Client Code, API Key, API Secret when prompted
  - A browser window will open — login with Dhan credentials
  - Copy the full redirect URL and paste it in the terminal
  - Token is saved to .dhan_creds.json — no more 24hr expiry!

Both PC and tablet must be on the SAME WiFi network.
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, json, os, time, socket
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
CORS(app, origins="*")

# ── FREE Groq AI key (console.groq.com — no credit card) ─────────
GROQ_API_KEY = os.environ.get(
    "GROQ_API_KEY",
    "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"
)
CREDS_FILE = ".dhan_creds.json"

# ── Tradehull client (initialized at startup or on /connect) ─────
tsl_client  = None   # Tradehull instance
dhan_token  = None   # access token string
dhan_client = None   # client id string

# ─────────────────────────────────────────────────────────────────
# CREDENTIAL HELPERS
# ─────────────────────────────────────────────────────────────────

def load_saved_creds():
    """Load client_code, api_key, api_secret from .dhan_creds.json"""
    if os.path.exists(CREDS_FILE):
        try:
            with open(CREDS_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}

def save_creds(data):
    with open(CREDS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Credentials saved to {CREDS_FILE}")

# ─────────────────────────────────────────────────────────────────
# TRADEHULL INIT  (api_key mode — no 24hr expiry)
# ─────────────────────────────────────────────────────────────────

def init_tradehull(client_code, api_key, api_secret):
    """
    Initialize Tradehull v3 with browser-based OAuth (api_key mode).
    Tradehull v3 API: Tradehull(client_code, api_key)  — only 2 args!
    The constructor itself opens a browser, user logs in and pastes the redirect URL.
    Token auto-renews — no 24hr expiry!
    """
    global tsl_client, dhan_token, dhan_client
    try:
        from Dhan_Tradehull import Tradehull
        print("\n  Opening browser for Dhan login...")
        print("  1. A browser window will open automatically")
        print("  2. Login with your Dhan credentials")
        print("  3. After successful login you will be redirected")
        print("  4. Copy the FULL URL from the browser address bar")
        print("  5. Paste it here in this terminal and press Enter\n")

        # Tradehull v3: __init__(self, client_code, api_key) — only 2 args
        # api_secret is NOT passed to constructor in v3
        tsl_client = Tradehull(client_code, api_key)
        dhan_client = str(client_code)

        # (attribute dump removed — token location confirmed: tsl_client.Dhan.access_token)

        # Extract access token from Tradehull v3
        # AlgoLab browser sends credentials in request headers as fallback,
        # so Dhan REST calls work even if we can't extract it here.
        dhan_token = None

        # Try .Dhan (dhanhq object) __dict__ — token is stored there
        dhan_obj = getattr(tsl_client, 'Dhan', None)
        if dhan_obj and hasattr(dhan_obj, '__dict__'):
            d = dhan_obj.__dict__
            # Known dhanhq v3 attribute names for the access token
            for k in ['access_token', 'accessToken', 'token', 'auth_token',
                      '_access_token', 'dhan_access_token']:
                if k in d and isinstance(d[k], str) and len(d[k]) > 10:
                    dhan_token = d[k]
                    break
            # Fallback: any long string value that isn't a URL or client ID
            if not dhan_token:
                for k, v in d.items():
                    if (isinstance(v, str) and len(v) > 30
                            and 'http' not in v and k != 'client_id'):
                        dhan_token = v
                        break

        # Also try top-level tsl_client attrs
        if not dhan_token:
            for attr in ['access_token', 'token', 'token_id']:
                val = getattr(tsl_client, attr, None)
                if val and isinstance(val, str) and len(val) > 10:
                    dhan_token = val
                    break

        if dhan_token:
            print(f"  ✔ Token extracted: {dhan_token[:16]}...")
        else:
            print(f"  ✔ Tradehull connected — AlgoLab will provide token via connect flow")

        return True

    except ImportError:
        print("  ✘ Dhan-Tradehull not installed. Run: pip install Dhan-Tradehull")
        return False
    except Exception as e:
        print(f"  ✘ Tradehull init failed: {e}")
        import traceback; traceback.print_exc()
        return False

def get_live_token():
    """
    Get the current access token.
    Tradehull v3 stores it at tsl_client.Dhan.access_token.
    Falls back to manually-set dhan_token.
    """
    if tsl_client:
        try:
            dhan_obj = getattr(tsl_client, 'Dhan', None)
            if dhan_obj:
                for attr in ['access_token', 'token', 'accessToken']:
                    val = getattr(dhan_obj, attr, None)
                    if val and isinstance(val, str) and len(val) > 10:
                        return val
        except:
            pass
    return dhan_token or ""

def get_dhan_headers():
    """Headers for raw Dhan REST calls — always uses live token."""
    return {
        "Content-Type": "application/json",
        "access-token": get_live_token(),
        "client-id":    dhan_client or "",
    }

def dhan_headers_from_request(req):
    """
    Build headers — proxy token takes priority over browser-sent token.
    This means once logged in via Tradehull, all calls use the live token.
    """
    t = get_live_token() or req.headers.get("access-token", "") or ""
    cli = dhan_client or req.headers.get("client-id", "") or ""
    return {"Content-Type": "application/json", "access-token": t, "client-id": cli}

DHAN_BASE = "https://api.dhan.co"

# ─────────────────────────────────────────────────────────────────
# AUTH ENDPOINT  — called by AlgoLab Connect button
# ─────────────────────────────────────────────────────────────────

@app.route("/connect", methods=["POST"])
def connect():
    """
    AlgoLab sends { client_code, api_key, api_secret } here.
    We init Tradehull (browser OAuth) and return the access_token.
    If creds match saved ones and token already live → skip re-login.
    """
    global tsl_client, dhan_token, dhan_client
    body = request.json or {}

    cc  = body.get("client_code", "").strip()
    ak  = body.get("api_key",     "").strip()
    aks = body.get("api_secret",  "").strip()

    # Legacy mode: plain access_token + client_id (old flow still works)
    tok = body.get("access_token", "").strip()
    cid = body.get("client_id",    "").strip()

    if tok and cid:
        dhan_token  = tok
        dhan_client = cid
        save_creds({"mode": "token", "access_token": tok, "client_id": cid})
        return jsonify({"success": True, "mode": "token",
                        "access_token": tok, "client_id": cid}), 200

    if not (cc and ak and aks):
        # Try saved creds
        saved = load_saved_creds()
        cc  = saved.get("client_code", cc)
        ak  = saved.get("api_key",     ak)
        aks = saved.get("api_secret",  aks)
        if not (cc and ak and aks):
            return jsonify({"success": False,
                "error": "Provide client_code, api_key, api_secret"}), 400

    # Save for next time
    save_creds({"mode": "api_key", "client_code": cc,
                "api_key": ak, "api_secret": aks})

    ok = init_tradehull(cc, ak, aks)
    if ok:
        # Return whatever token we have (may be empty if v3 stores it differently)
        # The proxy will use tsl_client directly for API calls
        tok_to_send = dhan_token or "tradehull_connected"
        return jsonify({
            "success":      True,
            "mode":         "api_key",
            "access_token": tok_to_send,
            "client_id":    dhan_client,
        }), 200
    else:
        return jsonify({"success": False,
            "error": "Tradehull login failed. Check terminal for details."}), 401

# ─────────────────────────────────────────────────────────────────
# DHAN REST PASSTHROUGH ROUTES
# ─────────────────────────────────────────────────────────────────

def dhan_call(method, path, body=None, params=None):
    """
    Make a Dhan API call.
    Priority:
      1. tsl_client.Dhan (dhanhq object) — uses its own authenticated session
      2. get_live_token() — token extracted from dhanhq object
      3. Headers from browser request (fallback for legacy token mode)
    """
    # Try using dhanhq object's internal session (most reliable)
    dhan_obj = getattr(tsl_client, 'Dhan', None) if tsl_client else None
    if dhan_obj:
        try:
            sess = getattr(dhan_obj, 'session', None) or getattr(dhan_obj, '_session', None)
            if sess:
                url = f"{DHAN_BASE}{path}"
                if method == 'GET':
                    r = sess.get(url, params=params)
                elif method == 'POST':
                    r = sess.post(url, json=body)
                elif method == 'DELETE':
                    r = sess.delete(url)
                return r
        except Exception as e:
            pass  # fall through to token-based call

    # Use token from dhanhq or browser headers
    hdrs = dhan_headers_from_request(request)
    url  = f"{DHAN_BASE}{path}"
    if method == 'GET':
        return requests.get(url, headers=hdrs, params=params)
    elif method == 'POST':
        return requests.post(url, headers=hdrs, json=body)
    elif method == 'DELETE':
        return requests.delete(url, headers=hdrs)


@app.route("/fundlimit", methods=["GET"])
def fund_limit():
    # Try dhanhq SDK method first (no token needed)
    dhan_obj = getattr(tsl_client, 'Dhan', None) if tsl_client else None
    if dhan_obj and hasattr(dhan_obj, 'get_fund_limits'):
        try:
            result = dhan_obj.get_fund_limits()
            if result and result.get('status') == 'success':
                return jsonify(result.get('data', result)), 200
        except Exception as e:
            pass
    r = dhan_call('GET', '/fundlimit')
    return jsonify(r.json()), r.status_code

@app.route("/positions", methods=["GET"])
def positions():
    dhan_obj = getattr(tsl_client, 'Dhan', None) if tsl_client else None
    if dhan_obj and hasattr(dhan_obj, 'get_positions'):
        try:
            result = dhan_obj.get_positions()
            if result and result.get('status') == 'success':
                return jsonify(result.get('data', result)), 200
        except Exception as e:
            pass
    r = dhan_call('GET', '/positions')
    return jsonify(r.json()), r.status_code

@app.route("/orders", methods=["GET"])
def get_orders():
    dhan_obj = getattr(tsl_client, 'Dhan', None) if tsl_client else None
    if dhan_obj and hasattr(dhan_obj, 'get_order_list'):
        try:
            result = dhan_obj.get_order_list()
            if result and result.get('status') == 'success':
                return jsonify(result.get('data', result)), 200
        except Exception as e:
            pass
    r = dhan_call('GET', '/orders')
    return jsonify(r.json()), r.status_code

@app.route("/orders", methods=["POST"])
def place_order():
    dhan_obj = getattr(tsl_client, 'Dhan', None) if tsl_client else None
    body = request.json or {}
    if dhan_obj and hasattr(dhan_obj, 'place_order'):
        try:
            result = dhan_obj.place_order(
                security_id   = body.get('securityId',''),
                exchange_segment = body.get('exchangeSegment','NSE_EQ'),
                transaction_type = body.get('transactionType','BUY'),
                quantity      = body.get('quantity', 1),
                order_type    = body.get('orderType','MARKET'),
                product_type  = body.get('productType','INTRADAY'),
                price         = body.get('price', 0),
            )
            return jsonify(result), 200
        except Exception as e:
            pass
    r = dhan_call('POST', '/orders', body=body)
    return jsonify(r.json()), r.status_code

@app.route("/orders/<order_id>", methods=["DELETE"])
def cancel_order(order_id):
    dhan_obj = getattr(tsl_client, 'Dhan', None) if tsl_client else None
    if dhan_obj and hasattr(dhan_obj, 'cancel_order'):
        try:
            result = dhan_obj.cancel_order(order_id=order_id)
            return jsonify(result), 200
        except Exception as e:
            pass
    r = dhan_call('DELETE', f'/orders/{order_id}')
    return jsonify(r.json()), r.status_code

@app.route("/holdings", methods=["GET"])
def holdings():
    dhan_obj = getattr(tsl_client, 'Dhan', None) if tsl_client else None
    if dhan_obj and hasattr(dhan_obj, 'get_holdings'):
        try:
            result = dhan_obj.get_holdings()
            if result and result.get('status') == 'success':
                return jsonify(result.get('data', result)), 200
        except Exception as e:
            pass
    r = dhan_call('GET', '/holdings')
    return jsonify(r.json()), r.status_code

@app.route("/optionchain", methods=["GET"])
def option_chain():
    r = dhan_call('GET', '/optionchain', params=dict(request.args))
    return jsonify(r.json()), r.status_code

@app.route("/debug", methods=["GET"])
def debug_dhan():
    """Test all Dhan API calls and report what they return."""
    report = {}
    dhan_obj = getattr(tsl_client, 'Dhan', None) if tsl_client else None

    # Test fund limits
    if dhan_obj:
        for method_name in ['get_fund_limits','get_funds']:
            if hasattr(dhan_obj, method_name):
                try:
                    r = getattr(dhan_obj, method_name)()
                    report['fundlimit_sdk'] = {'method': method_name, 'result': str(r)[:300]}
                    break
                except Exception as e:
                    report['fundlimit_sdk_err'] = str(e)

        # Test positions
        for method_name in ['get_positions','positions']:
            if hasattr(dhan_obj, method_name):
                try:
                    r = getattr(dhan_obj, method_name)()
                    report['positions_sdk'] = {'method': method_name, 'result': str(r)[:300]}
                    break
                except Exception as e:
                    report['positions_sdk_err'] = str(e)

        # List all methods
        report['dhan_methods'] = [m for m in dir(dhan_obj)
                                   if not m.startswith('_') and callable(getattr(dhan_obj,m,None))]

    # Test via REST with current token
    tok = get_live_token()
    report['live_token'] = tok[:20]+'...' if tok else 'NONE'
    report['client_id']  = dhan_client or 'NONE'
    report['tradehull_connected'] = tsl_client is not None

    try:
        r = requests.get(f"{DHAN_BASE}/fundlimit",
                        headers={"access-token": tok, "client-id": dhan_client or "",
                                 "Content-Type": "application/json"},
                        timeout=10)
        report['fundlimit_rest'] = {'status': r.status_code, 'body': r.text[:300]}
    except Exception as e:
        report['fundlimit_rest_err'] = str(e)

    return jsonify(report), 200

@app.route("/ping", methods=["GET"])
def ping():
    live_tok = get_live_token()
    return jsonify({
        "status":      "AlgoLab proxy running ✔",
        "tradehull":   tsl_client is not None,
        "token_set":   bool(live_tok),
        "client_id":   dhan_client or "—",
        "token_preview": (live_tok[:12] + "...") if live_tok else "none",
    }), 200

# ─────────────────────────────────────────────────────────────────
# NSE OPTION CHAIN  (persistent session)
# ─────────────────────────────────────────────────────────────────

nse_session = None

def get_nse_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "Accept":          "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "DNT":             "1",
    })
    try:
        s.get("https://www.nseindia.com", timeout=10)
        time.sleep(1)
        s.headers.update({"Referer": "https://www.nseindia.com/"})
        s.get("https://www.nseindia.com/option-chain", timeout=10)
        time.sleep(0.5)
        s.headers.update({"Referer": "https://www.nseindia.com/option-chain"})
        s.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
              timeout=10)
        time.sleep(0.3)
    except Exception as e:
        print(f"  NSE session init error: {e}")
    return s

def is_market_open():
    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST)
    t   = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9*60+15 <= t <= 15*60+30

@app.route("/atmpremium", methods=["GET"])
def atm_premium():
    global nse_session
    symbol      = request.args.get("symbol", "NIFTY")
    spot_raw    = request.args.get("spot", "0")
    nse_sym     = "NIFTY" if symbol == "NIFTY" else "BANKNIFTY"
    step        = 50 if symbol == "NIFTY" else 100
    nse_error   = ""

    IST = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(IST)

    if not is_market_open():
        spot = float(spot_raw) if float(spot_raw) > 0 else 24000
        atm  = round(spot / step) * step
        day  = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][now_ist.weekday()]
        return jsonify({
            "success": False, "source": "market_closed", "atm": atm,
            "tip": f"Market closed ({day} {now_ist.strftime('%H:%M')} IST). "
                   "App uses B-S estimate automatically.",
        }), 200

    # ── Try NSE (2 attempts with session refresh) ─────────────────
    try:
        spot = float(spot_raw) if float(spot_raw) > 0 else None
        for attempt in range(2):
            try:
                if nse_session is None or attempt == 1:
                    nse_session = get_nse_session()
                nse_session.headers.update({
                    "Referer":         "https://www.nseindia.com/option-chain",
                    "X-Requested-With":"XMLHttpRequest",
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
                    sp   = row.get("strikePrice", 0)
                    diff = abs(sp - atm)
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

    # ── Fallback: Yahoo Finance options ──────────────────────────
    try:
        spot = float(spot_raw) if float(spot_raw) > 0 else 24000
        atm  = round(spot / step) * step
        yf_s = "NIFTY" if symbol == "NIFTY" else "BANKNIFTY"
        yf_r = requests.get(
            f"https://query2.finance.yahoo.com/v7/finance/options/%5E{'NSEI' if yf_s=='NIFTY' else 'NSEBANK'}",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        res = yf_r.json().get("optionChain", {}).get("result", [])
        if res:
            opts  = res[0].get("options", [{}])[0]
            calls = opts.get("calls", []); puts = opts.get("puts", [])
            und   = res[0].get("quote", {}).get("regularMarketPrice", spot)
            ce_ltp = pe_ltp = ce_strike = pe_strike = None
            bc = bp = float("inf")
            for c in calls:
                d = abs(c.get("strike",0)-atm)
                if d < bc and c.get("lastPrice",0) > 0: bc=d; ce_ltp=c["lastPrice"]; ce_strike=c["strike"]
            for p in puts:
                d = abs(p.get("strike",0)-atm)
                if d < bp and p.get("lastPrice",0) > 0: bp=d; pe_ltp=p["lastPrice"]; pe_strike=p["strike"]
            if ce_ltp and pe_ltp:
                return jsonify({
                    "success": True, "source": "Yahoo", "symbol": symbol,
                    "spot": round(und, 2), "atm": atm,
                    "ce_strike": ce_strike, "ce_ltp": round(ce_ltp, 2),
                    "pe_strike": pe_strike, "pe_ltp": round(pe_ltp, 2),
                }), 200
    except Exception:
        pass

    return jsonify({
        "success": False, "source": "fetch_failed", "nse_error": nse_error,
        "atm": atm if "atm" in locals() else 0,
        "tip": "NSE + Yahoo both failed during market hours. Check internet connection.",
    }), 200

# ─────────────────────────────────────────────────────────────────
# YAHOO FINANCE QUOTES RELAY
# ─────────────────────────────────────────────────────────────────

@app.route("/quotes", methods=["GET"])
def yf_quotes():
    symbols = [s.strip() for s in request.args.get("symbols","").split(",") if s.strip()]
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    })
    try:
        session.get("https://finance.yahoo.com", timeout=10)
        crumb = session.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10).text.strip()
        r = session.get(
            "https://query1.finance.yahoo.com/v7/finance/quote",
            params={"symbols": ",".join(symbols), "crumb": crumb,
                    "fields": "regularMarketPrice,regularMarketChangePercent,"
                              "regularMarketChange,regularMarketPreviousClose"},
            timeout=15)
        results = r.json().get("quoteResponse", {}).get("result", [])
        if results:
            return jsonify({"quoteResponse": {"result": results}}), 200
    except:
        pass
    try:
        r = requests.get(
            "https://query2.finance.yahoo.com/v8/finance/spark",
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
            results.append({
                "symbol": sym,
                "regularMarketPrice": price,
                "regularMarketChange": chg,
                "regularMarketChangePercent": (chg/prev*100) if prev else 0,
                "regularMarketPreviousClose": prev,
            })
        if results:
            return jsonify({"quoteResponse": {"result": results}}), 200
    except:
        pass
    return jsonify({"quoteResponse": {"result": []}, "error": "All sources failed"}), 200

# ─────────────────────────────────────────────────────────────────
# GROQ AI RELAY  (FREE — Llama 3.3 70B)
# ─────────────────────────────────────────────────────────────────

@app.route("/ai", methods=["POST"])
def ai_relay():
    body     = request.json or {}
    messages = body.get("messages", [])
    sys_text = body.get("system", "")
    if sys_text:
        messages = [{"role": "system", "content": sys_text}] + messages
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
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
    print("  AlgoLab Dhan Proxy  v3.3")
    print("="*54)

    # ── Auto-init Tradehull if saved creds exist ──────────────────
    if saved.get("mode") == "api_key":
        cc  = saved.get("client_code","")
        ak  = saved.get("api_key","")
        aks = saved.get("api_secret","")   # kept for storage, v3 doesn't need it in constructor
        if cc and ak:
            print(f"\n  Saved creds found for client: {cc}")
            print("  Initializing Tradehull (api_key mode)...")
            init_tradehull(cc, ak, aks)
        else:
            print("\n  ⚠ Saved creds incomplete — need Client Code + API Key.")
            print("    Enter credentials in AlgoLab → Dhan Connect tab.")
    elif saved.get("mode") == "token":
        dhan_token  = saved.get("access_token","")
        dhan_client = saved.get("client_id","")
        print(f"\n  Token mode: client {dhan_client} loaded from .dhan_creds.json")
    else:
        print("\n  No saved credentials.")
        print("  Option A: Enter Client Code + API Key + API Secret")
        print("            in AlgoLab → Dhan Connect tab → Connect")
        print("            (Browser opens for login — token never expires!)")
        print("  Option B: Enter Access Token + Client ID directly")
        print("            (24hr expiry — manual refresh needed)")

    # ── Yahoo Finance test ────────────────────────────────────────
    try:
        ts = requests.Session()
        ts.headers.update({"User-Agent": "Mozilla/5.0"})
        ts.get("https://finance.yahoo.com", timeout=8)
        crumb_ok = len(ts.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            timeout=8).text.strip()) > 3
        mkt_status = "✔ Yahoo Finance connected" if crumb_ok \
                     else "⚠ Yahoo crumb failed (v8 fallback active)"
    except:
        mkt_status = "⚠ Yahoo Finance unreachable"

    print(f"\n  Port          : 8765")
    print(f"  Tradehull     : {'✔ Connected — auto-renewing token' if tsl_client else '⚠ Not connected (login via AlgoLab)'}")
    print(f"  Market Data   : {mkt_status}")
    print(f"  AI (Groq)     : {'✔ Ready — Llama 3.3 70B' if has_key else '✘ Add FREE key from console.groq.com'}")
    print(f"\n  Proxy URL     : http://127.0.0.1:8765")
    print(f"  Tablet URL    : http://{local_ip}:8765")
    print("="*54 + "\n")

    app.run(host="0.0.0.0", port=8765, debug=False)
