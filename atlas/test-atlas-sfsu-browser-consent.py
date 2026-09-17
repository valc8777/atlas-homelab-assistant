#!/usr/bin/env python3

"""One-time browser PKCE SFSU probe. Tokens are never persisted."""

import argparse
import base64
import datetime
import hashlib
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import atlas_sfsu_graph as graph
import atlas_sfsu_oauth as oauth


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    result = {}
    completed = threading.Event()

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, fmt, *values):
            return

        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({key: values[0] for key, values in params.items() if values})
            message = b"ATLAS received the Microsoft response. You may close this tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)
            completed.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
    port = server.server_address[1]
    redirect_uri = f"http://localhost:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    query = urllib.parse.urlencode({
        "client_id": oauth.validate_client_id(args.client_id),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": " ".join(oauth.validate_scopes()),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "consent",
    })
    url = f"{oauth.AUTHORITY}/authorize?{query}"
    print("Opening Microsoft browser sign-in:", url, flush=True)
    webbrowser.open(url)
    if not completed.wait(args.timeout_seconds):
        server.shutdown()
        raise SystemExit("Browser consent timed out; no token was stored.")
    server.shutdown()
    if result.get("state") != state:
        raise SystemExit("Rejected callback with invalid state; no token was stored.")
    if result.get("error"):
        raise SystemExit(f"Consent failed: {result['error']}: {result.get('error_description', '')}")
    token = oauth.exchange_authorization_code(args.client_id, result.get("code", ""), redirect_uri, verifier)
    if token.get("error"):
        raise SystemExit(f"Token exchange failed: {token['error']}: {token.get('error_description', '')}")
    access_token = token.get("access_token")
    if not access_token:
        raise SystemExit("Microsoft returned no access token.")
    now = datetime.datetime.now(datetime.timezone.utc)
    events = graph.paged_values(graph.calendar_view_url(now.isoformat(), (now + datetime.timedelta(days=14)).isoformat()), access_token, max_pages=3)
    messages = graph.paged_values(graph.recent_mail_url(limit=5), access_token, max_pages=1)
    print("Consent succeeded.")
    print("Granted scopes:", ", ".join(sorted(set((token.get("scope") or "").split()))))
    print("Calendar read succeeded; event count in next 14 days:", len(events))
    print("Mail metadata read succeeded; recent header count:", len(messages))
    print("Access and refresh tokens were kept only in memory and are now discarded.")


if __name__ == "__main__":
    main()
