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

app = Flask(__name__)
CORS(app, origins="*")  # Allow all origins (your tablet)

DHAN_BASE = "https://api.dhan.co"

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

if __name__ == "__main__":
    import socket
    # Get local IP so you can use it on tablet
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    print("\n" + "="*50)
    print("  AlgoLab Dhan Proxy Server")
    print("="*50)
    print(f"  Local IP:  {local_ip}")
    print(f"  Port:      8765")
    print(f"\n  In AlgoLab on your tablet, set proxy to:")
    print(f"  http://{local_ip}:8765")
    print("="*50 + "\n")
    app.run(host="0.0.0.0", port=8765, debug=False)
