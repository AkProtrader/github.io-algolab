"""
AlgoLab — Dhan API Proxy Server
Run this on your PC, keep it running while using AlgoLab on your tablet.
Both PC and tablet must be on the SAME WiFi network.

Install: pip install flask flask-cors requests
Run:     python dhan_proxy.py
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import json
import os

app = Flask(__name__)
CORS(app, origins="*")  # Allow all origins (your tablet)

DHAN_BASE = "https://api.dhan.co"

# ── Put your FREE Groq API key here ──────────────────────────────
# Get it FREE at: console.groq.com → API Keys (no credit card!)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")
# ─────────────────────────────────────────────────────────────────

def dhan_headers(req):
    return {
        "Content-Type":  "application/json",
        "access-token":  req.headers.get("access-token", ""),
        "client-id":     req.headers.get("client-id", ""),
    }

@app.route("/fundlimit", methods=["GET"])
def fund_limit():
    r = requests.get(f"{DHAN_BASE}/fundlimit", headers=dhan_headers(request))
    return jsonify(r.json()), r.status_code

@app.route("/positions", methods=["GET"])
def positions():
    r = requests.get(f"{DHAN_BASE}/positions", headers=dhan_headers(request))
    return jsonify(r.json()), r.status_code

@app.route("/orders", methods=["GET"])
def get_orders():
    r = requests.get(f"{DHAN_BASE}/orders", headers=dhan_headers(request))
    return jsonify(r.json()), r.status_code

@app.route("/orders", methods=["POST"])
def place_order():
    r = requests.post(
        f"{DHAN_BASE}/orders",
        headers=dhan_headers(request),
        json=request.json
    )
    return jsonify(r.json()), r.status_code

@app.route("/orders/<order_id>", methods=["DELETE"])
def cancel_order(order_id):
    r = requests.delete(
        f"{DHAN_BASE}/orders/{order_id}",
        headers=dhan_headers(request)
    )
    return jsonify(r.json()), r.status_code

@app.route("/holdings", methods=["GET"])
def holdings():
    r = requests.get(f"{DHAN_BASE}/holdings", headers=dhan_headers(request))
    return jsonify(r.json()), r.status_code

@app.route("/optionchain", methods=["GET"])
def option_chain():
    params = dict(request.args)
    r = requests.get(f"{DHAN_BASE}/optionchain", headers=dhan_headers(request), params=params)
    return jsonify(r.json()), r.status_code

@app.route("/atmpremium", methods=["GET"])
def atm_premium():
    symbol   = request.args.get("symbol", "NIFTY")
    spot_raw = request.args.get("spot", "0")
    nse_error = ""

    try:
        spot = float(spot_raw) if float(spot_raw) > 0 else None
        strike_step = 50 if symbol == "NIFTY" else 100
        nse_symbol  = "NIFTY" if symbol == "NIFTY" else "BANKNIFTY"

        # PRIMARY: NSE option chain with proper cookie handshake
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })
        # Step 1: Hit homepage to get cookies
        s.get("https://www.nseindia.com", timeout=10)
        import time; time.sleep(0.5)
        # Step 2: Hit option-chain page to get additional cookies
        s.get("https://www.nseindia.com/option-chain", timeout=10)
        time.sleep(0.5)
        # Step 3: Fetch option chain data
        s.headers.update({"Referer": "https://www.nseindia.com/option-chain"})
        oc_r = s.get(
            f"https://www.nseindia.com/api/option-chain-indices?symbol={nse_symbol}",
            timeout=15
        )
        oc_data = oc_r.json()
        records   = oc_data.get("records", {})
        oc_list   = records.get("data", [])
        und_value = records.get("underlyingValue", spot or 0)
        if not spot: spot = und_value
        atm = round(spot / strike_step) * strike_step

        ce_ltp = pe_ltp = ce_strike = pe_strike = None
        best_diff = float("inf")
        for row in oc_list:
            s_price = row.get("strikePrice", 0)
            diff = abs(s_price - atm)
            if diff < best_diff:
                best_diff = diff
                ce_data = row.get("CE", {})
                pe_data = row.get("PE", {})
                if ce_data.get("lastPrice", 0) > 0:
                    ce_ltp = ce_data["lastPrice"]; ce_strike = s_price
                if pe_data.get("lastPrice", 0) > 0:
                    pe_ltp = pe_data["lastPrice"]; pe_strike = s_price

        if ce_ltp and pe_ltp:
            return jsonify({
                "success": True, "source": "NSE",
                "symbol": symbol, "spot": round(und_value, 2), "atm": atm,
                "ce_strike": ce_strike, "ce_ltp": round(ce_ltp, 2),
                "pe_strike": pe_strike, "pe_ltp": round(pe_ltp, 2),
            }), 200
        else:
            nse_error = f"NSE returned {len(oc_list)} rows, no ATM match. HTTP {oc_r.status_code}"

    except Exception as e:
        nse_error = str(e)

    # FALLBACK: Yahoo Finance options (no auth)
    try:
        spot = float(spot_raw) if float(spot_raw) > 0 else 24000
        strike_step = 50 if symbol == "NIFTY" else 100
        atm = round(spot / strike_step) * strike_step

        yf_sym = "%5ENSEI" if symbol == "NIFTY" else "%5ENSEBANK"
        yf_r = requests.get(
            f"https://query1.finance.yahoo.com/v7/finance/options/{yf_sym}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        yf_data = yf_r.json()
        option_chain = yf_data.get("optionChain", {}).get("result", [{}])[0]
        calls = option_chain.get("options", [{}])[0].get("calls", [])
        puts  = option_chain.get("options", [{}])[0].get("puts",  [])
        und   = option_chain.get("quote", {}).get("regularMarketPrice", spot)

        ce_ltp = pe_ltp = ce_strike = pe_strike = None
        best_ce = best_pe = float("inf")
        for c in calls:
            diff = abs(c.get("strike", 0) - atm)
            if diff < best_ce and c.get("lastPrice", 0) > 0:
                best_ce = diff; ce_ltp = c["lastPrice"]; ce_strike = c["strike"]
        for p in puts:
            diff = abs(p.get("strike", 0) - atm)
            if diff < best_pe and p.get("lastPrice", 0) > 0:
                best_pe = diff; pe_ltp = p["lastPrice"]; pe_strike = p["strike"]

        if ce_ltp and pe_ltp:
            return jsonify({
                "success": True, "source": "Yahoo",
                "symbol": symbol, "spot": round(und, 2), "atm": atm,
                "ce_strike": ce_strike, "ce_ltp": round(ce_ltp, 2),
                "pe_strike": pe_strike, "pe_ltp": round(pe_ltp, 2),
            }), 200
    except Exception as e3:
        pass

    # LAST RESORT: return debug info
    return jsonify({
        "success": False, "source": "all_failed",
        "nse_error": nse_error,
        "atm": atm if "atm" in dir() else 0,
    }), 200

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "AlgoLab proxy running ✔"}), 200

# ── Yahoo Finance Market Data Relay ─────────────────────────────
@app.route("/quotes", methods=["GET"])
def yf_quotes():
    """
    Fetch market quotes. Tries multiple sources:
    1. Yahoo Finance v8 API (with crumb cookie)
    2. Yahoo Finance v7 with session cookie
    3. Returns last known / simulated data as fallback
    """
    symbols_raw = request.args.get("symbols", "")
    symbols = [s.strip() for s in symbols_raw.split(",") if s.strip()]

    # ── Try Yahoo Finance with proper session/crumb ──────────────
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    })

    try:
        # Step 1: Get cookie by visiting Yahoo Finance
        session.get("https://finance.yahoo.com", timeout=10)

        # Step 2: Get crumb
        crumb_r = session.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            timeout=10
        )
        crumb = crumb_r.text.strip()

        # Step 3: Fetch quotes with crumb
        quote_r = session.get(
            "https://query1.finance.yahoo.com/v7/finance/quote",
            params={
                "symbols": ",".join(symbols),
                "crumb": crumb,
                "fields": "regularMarketPrice,regularMarketChangePercent,regularMarketChange,regularMarketPreviousClose,regularMarketDayHigh,regularMarketDayLow,regularMarketVolume,fiftyTwoWeekHigh,fiftyTwoWeekLow"
            },
            timeout=15
        )
        data = quote_r.json()
        results = data.get("quoteResponse", {}).get("result", [])
        if results:
            return jsonify({"quoteResponse": {"result": results}}), 200
    except Exception as e:
        pass

    # ── Fallback: Try v8 API ─────────────────────────────────────
    try:
        r = requests.get(
            "https://query2.finance.yahoo.com/v8/finance/spark",
            params={"symbols": ",".join(symbols), "range": "1d", "interval": "5m"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        # v8 has different structure, convert to v7 format
        spark = r.json().get("spark", {}).get("result", [])
        if spark:
            results = []
            for item in spark:
                sym = item.get("symbol", "")
                resp = item.get("response", [{}])[0]
                meta = resp.get("meta", {})
                closes = resp.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                closes = [c for c in closes if c is not None]
                price = closes[-1] if closes else meta.get("regularMarketPrice", 0)
                prev = meta.get("chartPreviousClose", price)
                chg = price - prev
                chgPct = (chg / prev * 100) if prev else 0
                results.append({
                    "symbol": sym,
                    "regularMarketPrice": price,
                    "regularMarketChange": chg,
                    "regularMarketChangePercent": chgPct,
                    "regularMarketPreviousClose": prev,
                    "regularMarketDayHigh": max(closes) if closes else price,
                    "regularMarketDayLow": min(closes) if closes else price,
                })
            if results:
                return jsonify({"quoteResponse": {"result": results}}), 200
    except Exception as e:
        pass

    return jsonify({"quoteResponse": {"result": []}, "error": "All data sources failed"}), 200
# ────────────────────────────────────────────────────────────────


# ── Claude AI Relay via Groq (FREE) ─────────────────────────────
# Get free API key at: console.groq.com → API Keys (no credit card needed)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")

@app.route("/ai", methods=["POST"])
def ai_relay():
    """
    Relay to Groq API (free) — Llama 3.3 70B model.
    Get free key at console.groq.com
    """
    body = request.json or {}
    messages = body.get("messages", [])

    # Groq uses OpenAI-compatible format
    # Prepend system message into messages list
    system_text = body.get("system", "")
    if system_text:
        messages = [{"role": "system", "content": system_text}] + messages

    payload = {
        "model": "llama-3.3-70b-versatile",
        "max_tokens": body.get("max_tokens", 1000),
        "messages": messages,
        "temperature": 0.7,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}",
    }

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        data = r.json()

        # Convert Groq response format → Anthropic-style so AlgoLab JS works unchanged
        if "choices" in data:
            text = data["choices"][0]["message"]["content"]
            return jsonify({"content": [{"type": "text", "text": text}]}), 200
        else:
            return jsonify(data), r.status_code

    except Exception as e:
        return jsonify({"error": str(e)}), 500
# ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import socket
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except:
        local_ip = "127.0.0.1"

    has_key = "YOUR_GROQ_API_KEY_HERE" not in GROQ_API_KEY and len(GROQ_API_KEY) > 20

    # Test Yahoo Finance connectivity
    try:
        test_session = requests.Session()
        test_session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        test_session.get("https://finance.yahoo.com", timeout=8)
        crumb_test = test_session.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=8)
        crumb_ok = len(crumb_test.text.strip()) > 3
        mkt_status = "✔ Yahoo Finance connected" if crumb_ok else "⚠ Yahoo Finance — crumb failed, will use v8 fallback"
    except:
        mkt_status = "⚠ Yahoo Finance unreachable — check internet"

    print("\n" + "="*52)
    print("  AlgoLab Dhan + AI Proxy Server")
    print("="*52)
    print(f"  Local IP      : {local_ip}")
    print(f"  Port          : 8765")
    print(f"  Dhan API      : ✔ Ready")
    print(f"  Market Data   : {mkt_status}")
    print(f"  AI (Groq)     : {'✔ API key set — AI Advisor ready!' if has_key else '✘ Add FREE Groq key from console.groq.com'}")
    print(f"\n  Set proxy in AlgoLab to:")
    print(f"  http://127.0.0.1:8765  (same device)")
    print("="*52 + "\n")
    app.run(host="0.0.0.0", port=8765, debug=False)
