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

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "AlgoLab proxy running ✔"}), 200

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

    print("\n" + "="*52)
    print("  AlgoLab Dhan + AI Proxy Server")
    print("="*52)
    print(f"  Local IP : {local_ip}")
    print(f"  Port     : 8765")
    print(f"  Dhan API : ✔ Ready")
    print(f"  AI (Groq): {'✔ API key set — AI Advisor ready!' if has_key else '✘ Add your FREE Groq key from console.groq.com'}")
    print(f"\n  Set proxy in AlgoLab to:")
    print(f"  http://127.0.0.1:8765  (same device)")
    print("="*52 + "\n")
    app.run(host="0.0.0.0", port=8765, debug=False)
