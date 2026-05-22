#!/usr/bin/env python3
"""Batch MCP client - runs multiple tool calls in one server session."""
import subprocess, json, sys, threading, queue, time

class MCPClient:
    def __init__(self, cmd):
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1
        )
        self._id = 0
        self._q = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                try:
                    self._q.put(json.loads(line))
                except Exception:
                    pass

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _recv(self, timeout=45):
        return self._q.get(timeout=timeout)

    def initialize(self):
        self._id += 1
        self._send({"jsonrpc":"2.0","id":self._id,"method":"initialize",
                    "params":{"protocolVersion":"2024-11-05",
                              "capabilities":{},"clientInfo":{"name":"dcd-batch","version":"1.0"}}})
        resp = self._recv(30)
        self._send({"jsonrpc":"2.0","method":"notifications/initialized","params":{}})
        return resp

    def call(self, tool_name, arguments, timeout=45):
        self._id += 1
        self._send({"jsonrpc":"2.0","id":self._id,"method":"tools/call",
                    "params":{"name":tool_name,"arguments":arguments}})
        return self._recv(timeout)

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(5)
        except Exception:
            self.proc.kill()


def parse_result(resp):
    if "error" in resp:
        return {"error": resp["error"]}
    content = resp.get("result", {}).get("content", [])
    texts = [c["text"] for c in content if c.get("type") == "text"]
    try:
        return json.loads(texts[0]) if texts else {}
    except Exception:
        return {"raw": texts[0] if texts else str(content)}


cmd = ["npx", "-y", "@okx_ai/okx-trade-mcp", "--profile", "live", "--modules", "all"]
client = MCPClient(cmd)
time.sleep(3)
client.initialize()
time.sleep(1)

results = {}

calls = [
    ("ticker",    "market_get_index_ticker",  {"instId": "BTC-USD"}),
    ("candles",   "market_get_index_candles",  {"instId": "BTC-USD", "bar": "1D", "limit": "30"}),
    ("atr_fast",  "market_get_indicator",      {"instId": "BTC-USD-SWAP", "indicator": "ATR", "period": "7", "bar": "1H"}),
    ("atr_slow",  "market_get_indicator",      {"instId": "BTC-USD-SWAP", "indicator": "ATR", "period": "14", "bar": "1H"}),
    ("boll",      "market_get_indicator",      {"instId": "BTC-USD-SWAP", "indicator": "BOLLINGER", "period": "20", "bar": "1H"}),
    ("iv",        "option_get_greeks",         {"instFamily": "BTC-USD"}),
    ("funding",   "market_get_funding_rate",   {"instId": "BTC-USD-SWAP"}),
    ("put_usdg",  "dcd_get_products",          {"baseCcy": "BTC", "quoteCcy": "USDG", "optType": "P"}),
    ("put_usdt",  "dcd_get_products",          {"baseCcy": "BTC", "quoteCcy": "USDT", "optType": "P"}),
    ("call_usdg", "dcd_get_products",          {"baseCcy": "BTC", "quoteCcy": "USDG", "optType": "C"}),
    ("call_usdt", "dcd_get_products",          {"baseCcy": "BTC", "quoteCcy": "USDT", "optType": "C"}),
    ("balance",   "account_get_asset_balance", {"ccy": "USDG,USDT,BTC"}),
]

for key, tool, args in calls:
    print(f"[+] {tool}...", file=sys.stderr)
    try:
        resp = client.call(tool, args, timeout=45)
        results[key] = parse_result(resp)
    except Exception as e:
        results[key] = {"error": str(e)}
    time.sleep(0.5)

client.close()
print(json.dumps(results, ensure_ascii=False, indent=2))
