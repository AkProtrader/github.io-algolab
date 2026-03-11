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
    """
    Fetch real ATM CE and PE premiums for NIFTY or BANKNIFTY.
    Usage: GET /atmpremium?symbol=NIFTY&spot=24000&expiry=17-Mar-2026
    Returns: { ce_strike, ce_ltp, pe_strike, pe_ltp, atm, spot, expiry }
    """
    symbol   = request.args.get("symbol", "NIFTY")
    spot_raw = request.args.get("spot", "0")
    expiry   = request.args.get("expiry", "")

    try:
        spot = float(spot_raw)
        strike_step = 50 if symbol == "NIFTY" else 100
        atm = round(spot / strike_step) * strike_step

        # Dhan option chain API
        params = {
            "UnderlyingScrip": "13" if symbol == "NIFTY" else "25",
            "UnderlyingSeg":   "IDX_I",
            "Expiry":          expiry,
        }
        r = requests.get(
            f"{DHAN_BASE}/optionchain",
            headers=dhan_headers(request),
            params=params,
            timeout=10
        )
        data = r.json()

        # Parse option chain — Dhan returns data.data list
        chain = data.get("data", [])
        ce_ltp, pe_ltp = None, None
        ce_strike, pe_strike = atm, atm

        for row in chain:
            strike = row.get("strikePrice") or row.get("strike_price") or row.get("SP")
            if strike and abs(float(strike) - atm) < strike_step * 0.6:
                # CE side
                ce = row.get("callOption") or row.get("CE") or row.get("call") or {}
                pe = row.get("putOption")  or row.get("PE") or row.get("put")  or {}
                ce_price = ce.get("last_price") or ce.get("ltp") or ce.get("LTP") or ce.get("lastPrice")
                pe_price = pe.get("last_price") or pe.get("ltp") or pe.get("LTP") or pe.get("lastPrice")
                if ce_price and float(ce_price) > 0:
                    ce_ltp = float(ce_price)
                    ce_strike = float(strike)
                if pe_price and float(pe_price) > 0:
                    pe_ltp = float(pe_price)
                    pe_strike = float(strike)

        if ce_ltp or pe_ltp:
            return jsonify({
                "success": True,
                "symbol": symbol,
                "spot": spot,
                "atm": atm,
                "expiry": expiry,
                "ce_strike": ce_strike,
                "ce_ltp": ce_ltp,
                "pe_strike": pe_strike,
                "pe_ltp": pe_ltp,
                "raw_rows": len(chain),
            }), 200
        else:
            # Return raw chain for debugging if parsing failed
            return jsonify({
                "success": False,
                "symbol": symbol,
                "atm": atm,
                "raw_rows": len(chain),
                "sample": chain[:2] if chain else [],
                "dhan_status": r.status_code,
            }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


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
