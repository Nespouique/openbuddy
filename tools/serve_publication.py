"""Serve the reviewed public-source payload once on loopback for the MCP push."""

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

from prepare_publication import prepare


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=19848)
    args = parser.parse_args()
    body = json.dumps({"files": prepare()}, ensure_ascii=False).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/payload":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.timeout = 120
        print("Public-source payload ready on loopback port", server.server_port, flush=True)
        server.handle_request()


if __name__ == "__main__":
    main()
