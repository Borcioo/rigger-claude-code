"""`/rigger-core:coordinate` runs its `host` call and sees the host it runs in.

A real `claude -p` session loads only rigger-core, against a local stand-in for the API: the handler finds the
expanded `host` call line in the command text the model receives (so `${CLAUDE_PLUGIN_ROOT}` expanded), answers
with a Bash tool_use running it, and answers the request that carries the tool_result with text. The inherited
`CLAUDE_CODE_ENTRYPOINT=claude-desktop` (as the app sets it) must read `desktop`; a plain `claude -p` (which sets
`sdk-cli`) must read `none`. `cli` cannot come from `claude -p`.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
PLUGINS = HERE.parent.parent.parent.parent
sys.path.insert(0, str(PLUGINS / "rigger-rules" / "tests"))

from stub_api import USAGE, QuietServer  # noqa: E402

# Anchored on the --plugin-dir path the CLI expands ${CLAUDE_PLUGIN_ROOT} to: the command body is its own text
# block and holds no other marker.
ROOT = re.escape((PLUGINS / "rigger-core").as_posix())
HOST_LINE = re.compile(r'sh "%s/bin/py\.sh" "%s/tools/coord-state/coord_state\.py" host(?![\w-])' % (ROOT, ROOT),
                       re.IGNORECASE)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def sse(model, block, stop):
    message = {"id": "msg_stub", "type": "message", "role": "assistant", "model": model, "content": [],
               "stop_reason": None, "stop_sequence": None, "usage": USAGE}
    start = dict(block)
    if block["type"] == "text":
        start["text"] = ""
        delta = {"type": "text_delta", "text": block["text"]}
    else:
        start["input"] = {}
        delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
    events = [("message_start", {"type": "message_start", "message": message}),
              ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": start}),
              ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
              ("content_block_stop", {"type": "content_block_stop", "index": 0}),
              ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                 "usage": {"output_tokens": 5}}),
              ("message_stop", {"type": "message_stop"})]
    return b"".join(("event: %s\ndata: %s\n\n" % (name, json.dumps(data))).encode() for name, data in events)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, status, ctype, body):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.reply(404, "application/json", b"{}")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
            self.reply(404, "application/json", b"{}")
            return
        body = json.loads(raw)
        self.server.requests.append(body)
        last = body["messages"][-1]
        has_result = isinstance(last.get("content"), list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in last["content"])
        command = None
        for text in strings(body.get("messages")):
            found = HOST_LINE.search(text)
            if found:
                command = found.group(0)
                break
        if has_result or command is None:
            block, stop = {"type": "text", "text": "STUB-DONE"}, "end_turn"
        else:
            block, stop = {"type": "tool_use", "id": "toolu_host1", "name": "Bash",
                           "input": {"command": command, "description": "host"}}, "tool_use"
        self.reply(200, "text/event-stream", sse(body.get("model", "stub"), block, stop))


class CoordinateHostTests(unittest.TestCase):
    def setUp(self):
        self.claude = shutil.which("claude")
        self.assertIsNotNone(self.claude, "claude CLI not on PATH; HARNESS_NO_CLAUDE=1 skips this test")
        self.tmp = Path(tempfile.mkdtemp(prefix="coord-host-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cwd = self.tmp / "cwd"
        self.cwd.mkdir()
        (self.cwd / "plan.md").write_text("# Plan\n\n### Task 1: one thing\n", encoding="utf-8")
        self.server = QuietServer(("127.0.0.1", 0), Handler)
        self.server.requests = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def run_coordinate(self, entrypoint):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE", "ANTHROPIC_", "HARNESS_", "RIGGER_"))}
        env.update(
            CLAUDE_CONFIG_DIR=str(self.tmp / "cfg"),
            ANTHROPIC_BASE_URL="http://127.0.0.1:%d" % self.server.server_address[1],
            ANTHROPIC_API_KEY="stub-key",
            HOME=str(self.tmp),  # USERPROFILE stays: Store Python derives sys.executable from it
            CLAUDE_CODE_PLUGIN_CACHE_DIR=str(self.tmp / "plugin-cache"),
            HARNESS_LESSONS_INBOX=str(self.tmp / "inbox"),
        )
        if entrypoint:
            env["CLAUDE_CODE_ENTRYPOINT"] = entrypoint
        try:
            proc = subprocess.run(
                [self.claude, "-p", "/rigger-core:coordinate plan.md", "--plugin-dir", str(PLUGINS / "rigger-core"),
                 "--allowedTools", "Bash", "--model", "haiku", "--output-format", "json"],
                cwd=str(self.cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=90)
        except subprocess.TimeoutExpired as error:
            partial = (error.stdout or b"").decode("utf-8", "replace").replace("\r\n", "\n")
            self.fail("claude -p timed out after 90 s; output so far:\n" + partial)
        output = proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        self.assertEqual(proc.returncode, 0, output)
        return [b for r in self.server.requests for m in r["messages"] if isinstance(m.get("content"), list)
                for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]

    def assert_host(self, entrypoint, expected):
        results = self.run_coordinate(entrypoint)
        # Not a skip: the handler found the expanded line and ran it, so a tool_result came back.
        self.assertEqual(len(results), 1, "no host call reached the stub; requests: %d; expected root: %s" % (
            len(self.server.requests), (PLUGINS / "rigger-core").as_posix()))
        text = "\n".join(strings(results[0].get("content")))
        self.assertIn("coord-state: ok host " + expected, text)

    @unittest.skipIf(os.environ.get("HARNESS_NO_CLAUDE") == "1", "HARNESS_NO_CLAUDE=1: claude -p not run")
    def test_inherited_desktop_entrypoint_reads_desktop(self):
        self.assert_host("claude-desktop", "desktop")

    @unittest.skipIf(os.environ.get("HARNESS_NO_CLAUDE") == "1", "HARNESS_NO_CLAUDE=1: claude -p not run")
    def test_plain_print_session_reads_none(self):
        self.assert_host(None, "none")


if __name__ == "__main__":
    unittest.main(verbosity=2)
