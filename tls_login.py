#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
登录端点探针 v3 —— 抓包 + 逐字节落盘
--------------------------------------
已实测结论（2026-11-22）：
  客户端的登录入口是  game.hyp.163.com:443
  握手：TLS 1.0 / ECDHE-RSA-AES256-SHA
  应用层：明文 HTTP/1.1
  首个请求：POST /s/chatft.s  Content-Type: application/x-www-form-urlencoded
           Content-Length: 96，正文是加密二进制（两次抓包完全一致 → 固定载荷）

换句话说：**传输层完全可以本地伪造**，卡点只剩"这 96 字节怎么解密/怎么回"。

本脚本把每次请求的原始字节存成单独文件，方便离线分析。
用法：python tls_login.py
产物：_local_server\logs\req_<时间戳>_<序号>.bin
"""
import os
import socket
import ssl
import subprocess
import threading
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = os.path.join(HERE, "logs")
LOG = os.path.join(LOGDIR, "tls443.log")
CERT = os.path.join(HERE, "server.pem")
KEY = os.path.join(HERE, "server.key")
os.makedirs(LOGDIR, exist_ok=True)
_lock = threading.Lock()
_counter = [0]

OPENSSL_CANDIDATES = [
    r"C:\Program Files\Git\usr\bin\openssl.exe",
    r"C:\Program Files\Git\mingw64\bin\openssl.exe",
    "openssl",
]


def log(msg):
    line = "[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    with _lock:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    print(line, end="", flush=True)


def find_openssl():
    for exe in OPENSSL_CANDIDATES:
        try:
            if subprocess.run([exe, "version"], capture_output=True, timeout=15).returncode == 0:
                return exe
        except Exception:
            continue
    return None


def ensure_cert():
    if os.path.exists(CERT) and os.path.exists(KEY):
        return True
    exe = find_openssl()
    if not exe:
        log("openssl not found")
        return False
    r = subprocess.run([exe, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", KEY, "-out", CERT, "-days", "3650",
                        "-subj", "/CN=game.hyp.163.com"],
                       capture_output=True, timeout=120)
    if r.returncode != 0:
        log("openssl failed: %s" % r.stderr.decode("utf8", "replace")[:300])
        return False
    log("self-signed cert generated")
    return True


def make_ctx():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1
        ctx.maximum_version = ssl.TLSVersion.TLSv1
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    except Exception as e:
        log("tls1.0 setup: %s" % e)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def dump(payload):
    with _lock:
        _counter[0] += 1
        n = _counter[0]
    path = os.path.join(LOGDIR, "req_%s_%03d.bin"
                        % (datetime.now().strftime("%Y%m%d_%H%M%S"), n))
    with open(path, "wb") as f:
        f.write(payload)
    return path


def summarize(head, body):
    """只打印头部 + 正文长度/哈希，正文另存。"""
    import hashlib
    first = head.split(b"\r\n")[0].decode("latin1", "replace")
    return "%s | body=%dB sha1=%s" % (first, len(body),
                                      hashlib.sha1(body).hexdigest()[:16])


def handle(conn, addr, ctx):
    try:
        conn.settimeout(20)
        tls = ctx.wrap_socket(conn, server_side=True)
        log("TLS OK %s:%d cipher=%s version=%s"
            % (addr[0], addr[1], tls.cipher()[0], tls.version()))
        tls.settimeout(12)
        buf = b""
        while True:
            try:
                chunk = tls.recv(8192)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            if b"\r\n\r\n" in buf:
                head, _, body = buf.partition(b"\r\n\r\n")
                # 按 Content-Length 补全
                clen = 0
                for line in head.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        try:
                            clen = int(line.split(b":")[1])
                        except Exception:
                            clen = 0
                if len(body) < clen:
                    try:
                        while len(body) < clen:
                            more = tls.recv(clen - len(body))
                            if not more:
                                break
                            body += more
                    except Exception:
                        pass
                path = dump(head + b"\r\n\r\n" + body)
                log("  REQ %s -> %s" % (summarize(head, body), os.path.basename(path)))
                # 回最小 200，让客户端别卡死重试
                resp = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Content-Length: 9\r\nConnection: close\r\n\r\n{\"ret\":0}")
                tls.sendall(resp)
                buf = b""
                break
        try:
            tls.close()
        except Exception:
            pass
    except ssl.SSLError as e:
        log("TLS FAIL %s:%d %s" % (addr[0], addr[1], e))
    except Exception as e:
        log("ERR %s: %s" % (type(e).__name__, e))


def serve(port, ctx):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(32)
    log("listening 0.0.0.0:%d (TLS 1.0)" % port)
    while True:
        c, a = s.accept()
        threading.Thread(target=handle, args=(c, a, ctx), daemon=True).start()


if __name__ == "__main__":
    if not ensure_cert():
        raise SystemExit("need cert")
    serve(443, make_ctx())
