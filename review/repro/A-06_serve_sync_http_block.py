"""A-06 재현: `act transitions`가 단일 스레드 serve 루프에서 동기 HTTP를 실행해 후속 ping까지 블로킹한다."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KB = ROOT / "scripts" / "kb.py"
SAMPLE = ROOT / "examples" / "sample-vault"


class SlowTransitionsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if "/transitions" in self.path:
            time.sleep(2.0)
            body = json.dumps({"transitions": []}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


with tempfile.TemporaryDirectory(prefix="repro_a06_") as tmp:
    tmp_path = Path(tmp)
    vault = tmp_path / "vault"
    shutil.copytree(SAMPLE, vault)
    keyring_file = tmp_path / "keyring.json"
    keyring_file.write_text(json.dumps({"work-kb\njira_pat": "tok"}), encoding="utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowTransitionsHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        (vault / "config.local.yaml").write_text(
            f"environment: home\njira:\n  base_url: http://127.0.0.1:{port}\n"
            f"  pat_ref: keyring:work-kb/jira_pat\n  project_keys: [KB]\n",
            encoding="utf-8",
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("KB_")}
        env.update({
            "PYTHONPATH": str(ROOT / "tests"),
            "PYTHON_KEYRING_BACKEND": "fake_keyring.FileKeyring",
            "KB_FAKE_KEYRING_FILE": str(keyring_file),
            "PYTHONUTF8": "1",
        })
        proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", str(KB), "serve", "--root", str(vault), "--idle-sec", "20"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=env,
        )
        assert proc.stdin and proc.stdout
        ready = proc.stdout.readline()
        print("READY:", ready.strip())
        proc.stdin.write(json.dumps({"id": 1, "argv": ["act", "transitions", "KB-12", "--json", "--root", str(vault)]}) + "\n")
        t0 = time.monotonic()
        proc.stdin.write(json.dumps({"id": 2, "op": "ping"}) + "\n")
        proc.stdin.flush()
        resp1 = proc.stdout.readline()
        resp2 = proc.stdout.readline()
        ping_latency = time.monotonic() - t0
        proc.stdin.write(json.dumps({"id": 3, "op": "shutdown"}) + "\n")
        proc.stdin.flush()
        proc.wait(timeout=10)
        print(f"ping_latency={ping_latency:.2f}s resp1={resp1.strip()} resp2={resp2.strip()}")
        assert ping_latency < 0.5, f"serve ping blocked {ping_latency:.2f}s behind slow act transitions HTTP call"
    finally:
        server.shutdown()
        server.server_close()
