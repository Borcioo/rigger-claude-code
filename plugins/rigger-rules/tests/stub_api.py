"""A local stand-in for the Anthropic Messages API, so `claude -p` runs without login or cost.

Every POST body is kept in `StubApi.requests`, which is what the model would have received.
Used through ANTHROPIC_BASE_URL with a temp CLAUDE_CONFIG_DIR.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USAGE = {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def sse_events(model, text):
    message = {
        "id": "msg_stub",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": USAGE,
    }
    return [
        ("message_start", {"type": "message_start", "message": message}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 5}}),
        ("message_stop", {"type": "message_stop"}),
    ]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_body(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_body(404, "application/json", b"{}")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
            self.send_body(404, "application/json", b"{}")
            return
        body = json.loads(raw.decode("utf-8"))
        self.server.stub.requests.append(body)
        text = "STUB-REPLY"
        model = body.get("model", "stub")
        if body.get("stream"):
            chunks = []
            for name, data in sse_events(model, text):
                chunks.append(("event: %s\ndata: %s\n\n" % (name, json.dumps(data))).encode("utf-8"))
            self.send_body(200, "text/event-stream", b"".join(chunks))
        else:
            reply = {
                "id": "msg_stub",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": USAGE,
            }
            self.send_body(200, "application/json", json.dumps(reply).encode("utf-8"))


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # the CLI drops its keep-alive connections on exit


class StubApi:
    def __init__(self):
        self.requests = []
        self.server = QuietServer(("127.0.0.1", 0), Handler)
        self.server.stub = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)
