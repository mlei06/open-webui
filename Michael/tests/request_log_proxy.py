#!/usr/bin/env python3
"""Logging pass-through proxy for model requests (test tool, standard library only).

Point an Open WebUI connection at http://host.docker.internal:<port>/v1 and this
forwards every request unchanged to the real provider while appending one JSON
line per POST to the log file: time, path, model, the request body's keys, and its
system content. Headers (and so API keys) are never logged or stored.

  UPSTREAM_BASE_URL=<provider /v1 URL> CA_BUNDLE=<pem> \
      python3 tests/request_log_proxy.py <port> <log.jsonl>
"""

import http.server
import json
import os
import socketserver
import ssl
import sys
import time
import urllib.error
import urllib.request

UPSTREAM = os.environ['UPSTREAM_BASE_URL'].rstrip('/')
CTX = ssl.create_default_context(cafile=os.environ.get('CA_BUNDLE') or None)
LOG = sys.argv[2]
SKIP = {'host', 'content-length', 'connection', 'accept-encoding', 'transfer-encoding'}


def system_text(body):
    """System content as the provider will see it, for chat completions and responses payloads."""
    out = []
    for m in body.get('messages') or []:
        if m.get('role') in ('system', 'developer'):
            c = m.get('content')
            out.append(c if isinstance(c, str) else json.dumps(c))
    if body.get('instructions'):
        out.append(body['instructions'])
    return out


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def forward(self):
        n = int(self.headers.get('Content-Length') or 0)
        data = self.rfile.read(n) if n else None
        if self.command == 'POST' and data:
            try:
                body = json.loads(data)
                entry = {'t': time.time(), 'path': self.path, 'model': body.get('model'), 'keys': sorted(body), 'system': system_text(body)}
                with open(LOG, 'a') as f:
                    f.write(json.dumps(entry) + '\n')
            except ValueError:
                pass
        path = self.path[len('/v1'):] if self.path.startswith('/v1') else self.path
        headers = {k: v for k, v in self.headers.items() if k.lower() not in SKIP}
        req = urllib.request.Request(UPSTREAM + path, data=data, headers=headers, method=self.command)
        try:
            resp = urllib.request.urlopen(req, context=CTX, timeout=300)
        except urllib.error.HTTPError as e:
            resp = e
        self.send_response(resp.status if hasattr(resp, 'status') else resp.code)
        for k, v in resp.headers.items():
            if k.lower() not in SKIP:
                self.send_header(k, v)
        self.send_header('Connection', 'close')
        self.end_headers()
        while chunk := resp.read(4096):
            self.wfile.write(chunk)
            self.wfile.flush()

    do_GET = do_POST = forward


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == '__main__':
    Server(('0.0.0.0', int(sys.argv[1])), Handler).serve_forever()
