"""Loopback-only synthetic browser fixture. Not used by the application."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from queue import Queue
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phone_capture import PhoneCapture

events = Queue()
capture = PhoneCapture(tunnel=False, ice_servers=[], on_event=lambda *event: events.put(event))
kind, url = events.get(timeout=10)
assert kind == 'phone_url'
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        value = url.encode('ascii')
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(value)))
        self.end_headers()
        self.wfile.write(value)
    def log_message(self, *args): pass
server = HTTPServer(('127.0.0.1', 38765), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
print('Browser fixture ready', flush=True)
try:
    deadline = time.monotonic() + 90
    received = False
    while time.monotonic() < deadline:
        frame = capture.read(timeout=1)
        if frame is not None and frame.width > 2 and not received:
            print('Synthetic browser frame received:', frame.width, 'x', frame.height, flush=True)
            received = True
        if frame is not None and frame.width == 2 and received:
            print('Browser stopped; fallback frame received', flush=True)
            break
    else:
        raise RuntimeError('Timed out waiting for browser frame')
finally:
    server.shutdown()
    capture.close()
