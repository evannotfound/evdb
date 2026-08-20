from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

CHALLENGE_URL = os.environ["CHALLENGE_URL"]


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(length))
        path = "/set-txt" if self.path == "/present" else "/clear-txt"
        body = json.dumps({"host": value["fqdn"], "value": value["value"]}).encode()
        request = Request(
            CHALLENGE_URL + path,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=10) as response:
            response.read()
        self.send_response(204)
        self.end_headers()

    def log_message(self, _format, *args):
        del args


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
