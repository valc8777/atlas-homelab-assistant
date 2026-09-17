#!/usr/bin/env python3

import json
import socket
import tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import atlas_scheduler


class Handler(BaseHTTPRequestHandler):
    paths = []

    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        self.__class__.paths.append(self.path)
        payload = json.dumps({"ok": True, "path": self.path}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> None:
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = atlas_scheduler.run_once(f"http://127.0.0.1:{port}", "token")
        with tempfile.TemporaryDirectory() as temp_dir:
            heartbeat = Path(temp_dir) / "heartbeat.json"
            atlas_scheduler.write_heartbeat(str(heartbeat), result)
            heartbeat_payload = json.loads(heartbeat.read_text())
        assert result["evaluation"]["path"] == "/api/v1/evaluate"
        assert result["briefing"]["path"] == "/api/v1/briefings/evaluate"
        assert result["delivery"]["path"] == "/api/v1/notifications/deliver"
        assert result["memory_sync"]["path"] == "/api/v1/memory/sync"
        assert result["confirmation_execution"]["path"] == "/api/v1/confirmations/execute-approved"
        assert Handler.paths == [
            "/api/v1/evaluate",
            "/api/v1/briefings/evaluate",
            "/api/v1/notifications/deliver",
            "/api/v1/memory/sync",
            "/api/v1/confirmations/execute-approved",
        ]
        assert heartbeat_payload["ok"] is True
    finally:
        server.shutdown()
    print("ATLAS scheduler smoke test passed")


if __name__ == "__main__":
    main()
