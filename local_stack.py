"""Run current local SDK/login implementations and hall protocol recorders."""
import ssl
import threading
import time

from login_server import Server as LoginServer, Handler as LoginHandler, LOGS
from sdk_auth import AuthorizationServer, HERE
from tcp_probe import Server as TapServer, Handler as TapHandler
from mercury_server import Server as MercuryServer, LoginHandler as MercuryLogin, BaseHandler


def main():
    LOGS.mkdir(exist_ok=True)
    servers = []
    serving = []
    try:
        auth = AuthorizationServer(("127.0.0.1", 443),
                                   log_path=LOGS / "authorization.jsonl", local_login=True)
        servers.append(auth)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers("DEFAULT:@SECLEVEL=0")
        context.load_cert_chain(HERE / "oauth.pem", HERE / "oauth.key")
        auth.tls_context = context
        plain = AuthorizationServer(("127.0.0.1", 18443),
                                    log_path=LOGS / "authorization.jsonl", local_login=True)
        servers.append(plain)
        # Share the dictionary and lock. Handlers update the dictionary in place.
        plain.sessions = auth.sessions
        plain.session_lock = auth.session_lock
        login = LoginServer(("127.0.0.1", 8766), LoginHandler)
        login.bootstrap_account = True
        servers.append(login)
        for port in (25000, 25100):
            servers.append(TapServer(("127.0.0.1", port), TapHandler))
        servers.append(MercuryServer(("127.0.0.1", 25000), MercuryLogin))
        servers.append(MercuryServer(("127.0.0.1", 25100), BaseHandler))
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            serving.append(server)
        print("Local SDK/login ready; hall login experiment and BaseApp recorder ready", flush=True)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for server in serving:
            server.shutdown()
        for server in servers:
            server.server_close()


if __name__ == "__main__":
    main()
