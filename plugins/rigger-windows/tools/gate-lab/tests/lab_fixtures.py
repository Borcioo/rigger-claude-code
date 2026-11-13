"""Synthetic transcripts and row files for the gate lab tests: real record shapes, invented content."""
import collections
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.dirname(HERE)
sys.dont_write_bytecode = True
sys.path[:0] = [LAB, os.path.join(os.path.dirname(LAB), "shell-guard")]
import turn_evidence as te  # noqa: E402

SID_A = "aaaaaaaa-1111-4111-8111-111111111111"
SID_B = "bbbbbbbb-2222-4222-8222-222222222222"
SID_C = "cccccccc-3333-4333-8333-333333333333"
SID_D = "ffffffff-6666-4666-8666-666666666666"
SID_E = "abababab-7777-4777-8777-777777777777"
NOW = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def iso(days_ago, minute=0):
    moment = NOW - datetime.timedelta(days=days_ago) + datetime.timedelta(minutes=minute)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def day(days_ago):
    return iso(days_ago)[:10]


def tool_use(use_id, name, data, stamp, cwd, sid):
    return {
        "parentUuid": "p", "isSidechain": False, "message": {
            "model": "m", "id": "msg", "type": "message", "role": "assistant",
            "content": [{"type": "tool_use", "id": use_id, "name": name, "input": data, "caller": {"type": "direct"}}],
            "stop_reason": None,
        }, "requestId": "r", "type": "assistant", "uuid": "u", "timestamp": stamp, "userType": "external",
        "entrypoint": "cli", "cwd": cwd, "sessionId": sid, "version": "2", "gitBranch": "develop",
    }


def tool_result(use_id, output, stamp, cwd, sid, prompt_id, is_error):
    return {
        "parentUuid": "p", "isSidechain": False, "promptId": prompt_id, "type": "user", "message": {
            "role": "user", "content": [{"tool_use_id": use_id, "type": "tool_result", "content": output,
                                         "is_error": is_error}],
        }, "uuid": "u", "timestamp": stamp, "toolUseResult": {"stdout": "", "stderr": "", "interrupted": False},
        "sourceToolAssistantUUID": "a", "userType": "external", "entrypoint": "cli", "cwd": cwd, "sessionId": sid,
        "version": "2", "gitBranch": "develop",
    }


def human(text, stamp, cwd, sid, prompt_id, origin):
    return {
        "parentUuid": "p", "isSidechain": False, "promptId": prompt_id, "promptSource": "x", "type": "user",
        "message": {"role": "user", "content": text}, "uuid": "u", "timestamp": stamp, "permissionMode": "default",
        "userType": "external", "entrypoint": "cli", "cwd": cwd, "sessionId": sid, "turnOrigin": origin,
    }


def assistant(text, stamp, cwd, sid):
    return {
        "parentUuid": "p", "isSidechain": False, "message": {
            "model": "m", "id": "msg", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
        }, "requestId": "r", "type": "assistant", "uuid": "u", "timestamp": stamp, "userType": "external",
        "entrypoint": "cli", "cwd": cwd, "sessionId": sid, "version": "2", "gitBranch": "develop",
    }


class Session:
    """A session on disk, built call by call. Every record gets the next minute of `days_ago`'s day."""

    def __init__(self, config, project, sid, cwd, days_ago=2):
        self.sid = sid
        self.cwd = cwd
        self.days_ago = days_ago
        self.minute = 0
        self.main = os.path.join(config, "projects", project, sid + ".jsonl")
        self.path = self.main
        self.prompt_id = ""
        self.uses = 0
        self.streams = {self.main: []}

    def stamp(self):
        self.minute += 1
        return iso(self.days_ago, self.minute)

    def add(self, record):
        self.streams[self.path].append(record)

    def in_subagent(self, agent_id):
        self.path = os.path.join(os.path.splitext(self.main)[0], "subagents", "agent-%s.jsonl" % agent_id)
        self.streams.setdefault(self.path, [])
        return self

    def in_main(self):
        self.path = self.main
        return self

    def prompt(self, text="Go on with the task", origin="human"):
        self.uses += 1
        self.prompt_id = "prompt-%d" % self.uses
        self.add(human(text, self.stamp(), self.cwd, self.sid, self.prompt_id, origin))
        return self.prompt_id

    def notification(self):
        return self.prompt("<task-notification><status>completed</status></task-notification>", "task_notification")

    def say(self, text):
        self.add(assistant(text, self.stamp(), self.cwd, self.sid))

    def call(self, name, data, output, is_error=False):
        self.uses += 1
        use_id = "toolu_%s_%d" % (self.sid[:4], self.uses)
        self.add(tool_use(use_id, name, data, self.stamp(), self.cwd, self.sid))
        self.add(tool_result(use_id, output, self.stamp(), self.cwd, self.sid, self.prompt_id, is_error))
        return use_id

    def edit(self, relative):
        return self.call("Edit", {"file_path": self.cwd + "/" + relative}, "updated")

    def shell(self, command, output="done", is_error=False, name="Bash"):
        return self.call(name, {"command": command}, output, is_error)

    def check(self):
        return self.shell("pytest", "12 passed in 0.5s")

    def commit(self, is_error=False):
        return self.shell('git commit -m "change"', "1 file changed", is_error)

    def unanswered(self, command):
        self.uses += 1
        use_id = "toolu_%s_%d" % (self.sid[:4], self.uses)
        self.add(tool_use(use_id, "Bash", {"command": command}, self.stamp(), self.cwd, self.sid))
        return use_id

    def save(self):
        for path, records in self.streams.items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                for record in records:
                    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
                    handle.write(line.encode("utf-8", "surrogatepass") + b"\n")
        return self


def make_repo(base, name):
    path = os.path.join(base, name).replace("\\", "/")
    os.makedirs(path + "/.git")
    return path


def read_back(config):
    """What the corpus on disk holds: {"files": n, tool name: calls, "text": assistant closings}."""
    found = collections.Counter()
    for folder, _, names in os.walk(os.path.join(config, "projects")):
        for name in names:
            found["files"] += 1
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle]
            for record in records:
                content = record["message"]["content"]
                if record["type"] == "assistant":
                    found[content[0].get("name", "text")] += 1
    return found


def strip_field(config, field):
    """Removes a key from every record and block of the corpus."""
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key != field}
        return [clean(item) for item in value] if isinstance(value, list) else value

    for folder, _, names in os.walk(os.path.join(config, "projects")):
        for name in names:
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as handle:
                records = [clean(json.loads(line)) for line in handle]
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records))


def write_row(folder, name, *lines):
    """A row file holding one JSON line per row, or nothing at all."""
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name + ".json"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(json.dumps(line) + "\n" for line in lines))


def commit_row(sid, use_id, verdict="stale", **extra):
    use12 = te.row_key(use_id)
    row = {
        "ts": iso(1), "sid8": sid[:8], "use12": use12, "repo8": "repo0001", "op": "commit", "verdict": verdict,
        "reason": "", "staged": 1, "deleted": 0, "chained": False, "newest_age_s": 60, "check_age_s": 300,
        "reflog_age_s": 9000, "git_op": "", "ms": 44,
    }
    row.update(extra)
    return row


def stop_row(sid, prompt_id, closing, verdict="flag", **extra):
    key12 = te.row_key(sid, prompt_id, closing)
    row = {
        "ts": iso(1), "sid8": sid[:8], "key12": key12, "repo8": "repo0001", "verdict": verdict, "claim": "done",
        "code_changes": 1, "evidence": None, "fingerprint": key12, "msg_in_tail": True, "ms": 9,
    }
    row.update(extra)
    return row
