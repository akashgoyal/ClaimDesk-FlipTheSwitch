#!/usr/bin/env python3
"""The live claims board, for finance and for the demo video.

Serves a board on this machine only. It reads tracker.json, which the rules engine
rewrites on every change, and changes nothing. Standard library only.

  python3 tracker/serve.py [--home ~/.te-desk] [--port 8765]
"""
import argparse
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def handler(home):
    class Board(BaseHTTPRequestHandler):
        def send(self, status, kind, body):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = self.path.split("?")[0]
            if route == "/":
                with open(os.path.join(HERE, "board.html"), "rb") as handle:
                    self.send(200, "text/html; charset=utf-8", handle.read())
            elif route == "/tracker.json":
                try:
                    with open(os.path.join(home, "tracker.json"), "rb") as handle:
                        self.send(200, "application/json", handle.read())
                except FileNotFoundError:
                    self.send(200, "application/json", b'{"updated": null, "stages": [], "cases": []}')
            else:
                self.send(404, "text/plain", b"Not found")

        def log_message(self, *_args):
            pass

    return Board


def main():
    parser = argparse.ArgumentParser(description="Live claims board")
    parser.add_argument("--home", default=os.environ.get("TE_HOME") or os.path.expanduser("~/.te-desk"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"Claims board: http://localhost:{args.port} (reading {args.home})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), handler(args.home)).serve_forever()


if __name__ == "__main__":
    main()
