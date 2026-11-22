#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
荒野行动Plus (Knives Out Plus / PGX) 本地服务端脚手架
-------------------------------------------------------
用途：
  1. 替代已停服的网易更新服 / 服务器列表服，让启动器和客户端不再卡在
     "无法安全地连接到此页面" / "正在更新" 上；
  2. 把所有发到本机的请求原样记录下来，用于分析客户端协议。

运行：
  python localsrv.py            # HTTP :80 + TCP :25000/:25100
  python localsrv.py --http 8080

日志：
  _local_server\logs\http.log    所有 HTTP 请求（含 host / path / body）
  _local_server\logs\tcp_25000.log  游戏服 socket 的原始字节
"""
import argparse
import json
import os
import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_lock = threading.Lock()


def log(name, text):
    line = "[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text)
    with _lock:
        with open(os.path.join(LOG_DIR, name), "a", encoding="utf-8") as f:
            f.write(line)
    print(line, end="", flush=True)


# --------------------------------------------------------------------------
# 服务器列表：client 会从这里拿到大厅 / 战斗服地址
# 格式（官方 server_list.txt，空格分隔）：
#   <渠道> <区服名> <类型:1=大厅 2=战斗> <区服ID> <名字> <外网地址:端口>
#              <内网地址:端口> <备用地址:端口>
# 我们把它指向本机。
# --------------------------------------------------------------------------
SERVER_LIST = """\
201 荒野行动 0 1 hall1 10001 127.0.0.1:25000 127.0.0.1:25100 127.0.0.1:0
201 荒野行动 0 2 combat1 12001 127.0.0.1:25000 127.0.0.1:25100 127.0.0.1:25000
#updateTimestamp=1638763291
#network=cnc
"""

PATCH_LIST = """\
3.0.43 http://127.0.0.1/patch3.0.43.pg
#$1 0 localserver
#bturl http://127.0.0.1/
#httpurl http://127.0.0.1/
#p2purl http://127.0.0.1/
#timeout_to_http 0
#snail_timeout 300000
"""

INDEX_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>荒野行动Plus 本地服务端</title></head>
<body style="font-family:Segoe UI,Microsoft YaHei;background:#111;color:#ddd;padding:24px">
<h2>荒野行动Plus — 本地服务端已接管</h2>
<p>更新服 / 服务器列表服已被本机接管。这条请求说明启动器仍然在工作。</p>
<p>详细请求记录见 <code>_local_server\logs\http.log</code></p>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalPGX/1.0"
    protocol_version = "HTTP/1.1"

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            try:
                return self.rfile.read(n)
            except Exception:
                return b""
        return b""

    def _route(self, method):
        host = self.headers.get("Host", "")
        body = self._read_body()
        path = self.path
        log("http.log", "%s %s host=%s ua=%s len=%d body=%r"
            % (method, path, host, (self.headers.get("User-Agent") or "")[:60],
               len(body), body[:400]))

        name = path.split("?")[0].rstrip("/").lower()

        if name.endswith("server_list.txt"):
            payload, ctype = SERVER_LIST.encode("utf-8"), "text/plain; charset=utf-8"
        elif name.endswith("note.txt"):
            payload, ctype = b"local\n", "text/plain; charset=utf-8"
        elif name.endswith("patch_list3.txt") or name.endswith("patch_list.txt"):
            payload, ctype = PATCH_LIST.encode("utf-8"), "text/plain; charset=utf-8"
        elif name.endswith("planb3.txt"):
            payload, ctype = b"100,100,0,0,0,0,50,100\n", "text/plain; charset=utf-8"
        elif "latest_default.json" in name or name.endswith(".json"):
            payload = json.dumps({"code": 0, "version": "3.0.43"}).encode("utf-8")
            ctype = "application/json"
        elif name.endswith(".pg"):
            payload, ctype = b"", "application/octet-stream"
        else:
            payload, ctype = INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(payload)

    def do_GET(self):
        self._route("GET")

    def do_HEAD(self):
        self._route("HEAD")

    def do_POST(self):
        self._route("POST")

    def log_message(self, *a):
        pass


class TCPTap(socketserver.BaseRequestHandler):
    """把游戏服的原始字节流抓下来，先不实现任何协议。"""

    def handle(self):
        port = self.server.server_address[1]
        peer = "%s:%s" % self.client_address[:2]
        log("tcp_%d.log" % port, "CONNECT from %s" % peer)
        try:
            self.request.settimeout(30)
            while True:
                data = self.request.recv(65536)
                if not data:
                    break
                log("tcp_%d.log" % port,
                    "RECV %d bytes: %s" % (len(data), data[:512].hex()))
        except Exception as e:
            log("tcp_%d.log" % port, "ERR %s" % e)
        finally:
            # 不主动断开，让客户端自己超时，便于观察它的重试行为
            time.sleep(2)
            log("tcp_%d.log" % port, "CLOSE %s" % peer)


class TCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", type=int, default=80)
    ap.add_argument("--ports", type=str, default="25000,25100")
    args = ap.parse_args()

    httpd = ThreadingHTTPServer(("0.0.0.0", args.http), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log("http.log", "HTTP listening on 0.0.0.0:%d" % args.http)

    for p in [int(x) for x in args.ports.split(",") if x.strip()]:
        try:
            srv = TCPServer(("0.0.0.0", p), TCPTap)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            log("http.log", "TCP tap listening on 0.0.0.0:%d" % p)
        except OSError as e:
            log("http.log", "TCP %d bind failed: %s" % (p, e))

    print("\n本地服务端已启动。Ctrl+C 结束。\n"
          "  HTTP :%d   日志 _local_server\\logs\\http.log\n"
          "  TCP  :%s\n" % (args.http, args.ports))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("bye")


if __name__ == "__main__":
    main()
