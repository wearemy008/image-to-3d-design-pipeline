#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
su_client.py — SketchUp su_mcp 直连客户端（TCP 换行分隔 JSON-RPC 2.0）

不依赖 MCP 通道，纯 socket 直连 127.0.0.1:9876，适合批量任务与调试。

用法:
    python su_client.py info
    python su_client.py tools
    python su_client.py ruby "Sketchup.active_model.entities.count"
    python su_client.py call create_geometry '{"type":"box","width":39.37,"depth":39.37,"height":39.37}'
    python su_client.py call get_model_info '{}'

作为库使用:
    from su_client import SuClient
    with SuClient() as c:
        print(c.tool("get_model_info"))
"""

import json
import socket
import sys
import time


class SuClient:
    def __init__(self, host="127.0.0.1", port=9876, timeout=60):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self.buf = b""
        self._id = 0

    # ---------- 连接管理 ----------
    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self.buf = b""
        self.call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "su_client.py", "version": "1.0"},
        })
        self.notify("notifications/initialized")
        return self

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # ---------- 协议 ----------
    def _send(self, obj):
        if not self.sock:
            raise RuntimeError("未连接，请先调用 connect()")
        self.sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    def _recv_line(self):
        deadline = time.time() + self.timeout
        while b"\n" not in self.buf:
            if time.time() > deadline:
                return None
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                return None
            if not data:
                return None
            self.buf += data
        line, _, rest = self.buf.partition(b"\n")
        self.buf = rest
        return line.decode("utf-8", "replace")

    def call(self, method, params=None):
        """发 JSON-RPC 请求，返回完整响应 dict。"""
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            req["params"] = params
        self._send(req)
        line = self._recv_line()
        return json.loads(line) if line else None

    def notify(self, method, params=None):
        req = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            req["params"] = params
        self._send(req)

    def tool(self, tool_name, **args):
        """调用 MCP 工具，自动解包 result.content[0].text 为 dict。"""
        r = self.call("tools/call", {"name": tool_name, "arguments": args})
        if r is None:
            return None
        try:
            return json.loads(r["result"]["content"][0]["text"])
        except Exception:
            return r

    # ---------- 便捷方法 ----------
    def ruby(self, code):
        """执行 Ruby，返回字符串结果（失败返回 None）。"""
        r = self.tool("execute_ruby", code=code)
        if isinstance(r, dict) and r.get("success"):
            return r.get("result")
        return None

    def model_info(self):
        return self.tool("get_model_info")

    def entity_count(self):
        info = self.model_info()
        return info.get("all_entities_count") if info else None

    def bounds_m(self):
        """返回模型真实尺寸（米）: {'width':..,'height':..,'depth':..}"""
        code = (
            "bb=Sketchup.active_model.bounds;"
            "'%.3f,%.3f,%.3f' % [bb.width.to_m, bb.height.to_m, bb.depth.to_m]"
        )
        res = self.ruby(code)
        if not res:
            return None
        w, h, d = (float(x) for x in res.split(","))
        return {"width": w, "height": h, "depth": d}


# ---------------- CLI ----------------
def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd = args[0]
    with SuClient() as c:
        if cmd == "info":
            info = c.model_info()
            print(json.dumps(info, ensure_ascii=False, indent=2))
            b = c.bounds_m()
            if b:
                print("\n真实尺寸(米): 宽 %.2f x 深 %.2f x 高 %.2f" % (b["width"], b["depth"], b["height"]))
        elif cmd == "tools":
            r = c.call("tools/list")
            for t in r["result"]["tools"]:
                print("-", t["name"], "|", (t.get("description") or "")[:70])
        elif cmd == "ruby":
            print(c.ruby(args[1]))
        elif cmd == "call":
            name = args[1]
            payload = json.loads(args[2]) if len(args) > 2 else {}
            print(json.dumps(c.tool(name, **payload), ensure_ascii=False, indent=2))
        elif cmd == "selection":
            print(json.dumps(c.tool("get_selection", detail="full"), ensure_ascii=False, indent=2))
        else:
            print("未知命令:", cmd)
            print(__doc__)


if __name__ == "__main__":
    main()
