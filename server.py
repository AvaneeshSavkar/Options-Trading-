"""Run the Option Simulator on your own computer.

    pip install -r requirements.txt
    python server.py

Then open http://localhost:8000 (it opens automatically).
"""
import json
import os
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from api.stock import respond  # noqa: E402

PORT = int(os.environ.get('PORT', 8000))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path.rstrip('/') == '/api/stock':
            status, body = respond(parse_qs(url.query))
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif url.path in ('/', '/index.html'):
            super().do_GET()
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        msg = fmt % args
        if '/api/' in msg or 'code ' in msg:
            sys.stderr.write(msg + '\n')


if __name__ == '__main__':
    httpd = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    url = f'http://localhost:{PORT}'
    print(f'Option Simulator running at {url}  (press Ctrl+C to stop)')
    if not os.environ.get('NO_BROWSER'):
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
