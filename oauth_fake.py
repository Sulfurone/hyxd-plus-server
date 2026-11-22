#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地 OAuth 授权链伪造
---------------------
客户端登录页是 CEF 加载的外部 OAuth2 授权页：
  https://hyp.swjoy.com/oauth2/authorize.do?client_id=3525
      &redirect_uri=https://mgbcb.matrix.netease.com/pgx/cb/authcallback/swjoydy/pc/defaults/<TOKEN>
      &response_type=code&state=

实测 hyp.swjoy.com 在公网已无 A 记录（下架），所以登录页永远是白板。
本脚本用 SNI 路由，在同一个 443 端口上同时冒充两个域：
  - hyp.swjoy.com            -> 返回一个会自动跳转的授权页
  - mgbcb.matrix.netease.com -> 返回授权码回调结果
目的：把客户端推过登录页，看它下一步请求什么、发什么协议。

用法：python oauth_fake.py
证书：自动用 openssl 生成（含两个域的 SAN）
"""
import os
import socket
import ssl
import subprocess
import threading
import urllib.parse
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = os.path.join(HERE, "logs")
LOG = os.path.join(LOGDIR, "oauth.log")
CERT = os.path.join(HERE, "oauth.pem")
KEY = os.path.join(HERE, "oauth.key")
os.makedirs(LOGDIR, exist_ok=True)
_lock = threading.Lock()

AUTH_HOST = "hyp.swjoy.com"
CB_HOST = "mgbcb.matrix.netease.com"

OPENSSL = [r"C:\Program Files\Git\usr\bin\openssl.exe",
           r"C:\Program Files\Git\mingw64\bin\openssl.exe", "openssl"]


def log(msg):
    line = "[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    with _lock:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    print(line, end="", flush=True)


def openssl():
    for exe in OPENSSL:
        try:
            if subprocess.run([exe, "version"], capture_output=True, timeout=15).returncode == 0:
                return exe
        except Exception:
            continue
    return None


def ensure_cert():
    if os.path.exists(CERT) and os.path.exists(KEY):
        return True
    exe = openssl()
    if not exe:
        log("openssl not found")
        return False
    cfg = os.path.join(HERE, "oauth.cnf")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write("""
[req]
distinguished_name=dn
x509_extensions=v3
prompt=no
[dn]
CN=%s
[v3]
subjectAltName=DNS:%s,DNS:%s
basicConstraints=CA:TRUE
""" % (AUTH_HOST, AUTH_HOST, CB_HOST))
    r = subprocess.run([exe, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", KEY, "-out", CERT, "-days", "3650",
                        "-config", cfg],
                       capture_output=True, timeout=120)
    if r.returncode != 0:
        log("openssl fail: %s" % r.stderr.decode("utf8", "replace")[:400])
        return False
    log("cert generated with SAN for %s, %s" % (AUTH_HOST, CB_HOST))
    return True


AUTH_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>登录</title></head><body>
<p>授权中…</p>
<script>
// 直接把 code 塞回 redirect_uri
var u = new URL(location.href);
var ru = u.searchParams.get('redirect_uri');
if (ru) { location.replace(ru + '?code=LOCALFAKE' + '&state=' + (u.searchParams.get('state')||'')); }
</script>
</body></html>"""


def handle(conn, addr, ctx):
    tls = None
    try:
        conn.settimeout(20)
        tls = ctx.wrap_socket(conn, server_side=True)
        sni = getattr(tls, "server_hostname", None)
        log("TLS OK from %s:%d sni=%s" % (addr[0], addr[1], sni))

        tls.settimeout(10)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = tls.recv(8192)
            if not chunk:
                break
            buf += chunk
        head = buf.split(b"\r\n\r\n")[0].decode("latin1", "replace")
        first = head.split("\r\n")[0] if head else ""
        log("  REQ %s" % first)

        hdrs = {}
        for line in head.split("\r\n")[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                hdrs[k.strip().lower()] = v.strip()
        host = hdrs.get("host", "")

        path = first.split(" ")[1] if len(first.split(" ")) > 1 else "/"
        qs = urllib.parse.urlparse(path).query
        params = urllib.parse.parse_qs(qs)
        if params:
            log("  params: %s" % {k: v[:2] for k, v in params.items()})

        if AUTH_HOST in host or AUTH_HOST in (sni or ""):
            body = AUTH_PAGE.encode("utf-8")
            ctype = "text/html; charset=utf-8"
            log("  -> serving fake authorize page (redirects to redirect_uri?code=...)")
        else:
            body = b'{"code":0,"msg":"ok"}'
            ctype = "application/json"
            log("  -> serving fake callback json")

        resp = ("HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
                "Connection: close\r\n\r\n" % (ctype, len(body))).encode("latin1") + body
        tls.sendall(resp)
        log("  <- %dB sent" % len(resp))
        try:
            tls.close()
        except Exception:
            pass
    except ssl.SSLError as e:
        log("TLS FAIL %s: %s" % (addr[1], e))
    except Exception as e:
        log("ERR %s: %s" % (type(e).__name__, e))


def serve(ctx):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 443))
    s.listen(32)
    log("listening 443 (SNI routing)")
    while True:
        c, a = s.accept()
        threading.Thread(target=handle, args=(c, a, ctx), daemon=True).start()


if __name__ == "__main__":
    if not ensure_cert():
        raise SystemExit("need cert")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    # 2018 年的 CEF 不支持 TLS 1.3，必须把上限压到 1.2
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers("ALL:@SECLEVEL=0")
        log("TLS pinned to 1.0-1.2")
    except Exception as e:
        log("tls setup: %s" % e)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    serve(ctx)
