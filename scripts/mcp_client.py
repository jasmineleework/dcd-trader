#!/usr/bin/env python3
"""Simple synchronous MCP client for okx-trade-mcp-live."""
import subprocess, json, sys, threading, queue, time, os

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

    def _recv(self, timeout=30):
        return self._q.get(timeout=timeout)

    def initialize(self):
        self._id += 1
        self._send({"jsonrpc":"2.0","id":self._id,"method":"initialize",
                    "params":{"protocolVersion":"2024-11-05",
                              "capabilities":{},"clientInfo":{"name":"dcd-cron","version":"1.0"}}})
        resp = self._recv(30)
        # send initialized notification
        self._send({"jsonrpc":"2.0","method":"notifications/initialized","params":{}})
        return resp

    def call(self, tool_name, arguments, timeout=30):
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


def run_tool(client, name, args, timeout=30):
    resp = client.call(name, args, timeout)
    if "error" in resp:
        return {"error": resp["error"]}
    content = resp.get("result", {}).get("content", [])
    texts = [c["text"] for c in content if c.get("type") == "text"]
    try:
        return json.loads(texts[0]) if texts else {}
    except Exception:
        return {"raw": texts[0] if texts else str(content)}


if __name__ == "__main__":
    tool = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    cmd = ["npx", "-y", "@okx_ai/okx-trade-mcp", "--profile", "live", "--modules", "all"]
    client = MCPClient(cmd)
    time.sleep(3)  # wait for server startup
    client.initialize()
    time.sleep(1)

    result = run_tool(client, tool, args, timeout=30)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    client.close()
