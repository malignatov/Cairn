#!/usr/bin/env python3
"""Functional smoke test for the frozen Cairn binary.

Drives dist/cairn over stdio exactly like a real MCP client: send a request,
wait for its response, then send the next — closing stdin only at the end.
(Slamming every request in at once then closing stdin races the final flush
and is NOT how Claude Desktop behaves; don't test that way.)

Checks the things a freeze can plausibly break:
  - the server initializes over stdio;
  - bundled content resolves (constitution + skills come from sys._MEIPASS);
  - SQLite writes work and FTS5 search finds what was written.

Uses a throwaway DB via META_DB_PATH so it never touches real data. Stdlib
only, so it runs under any Python 3 (no venv needed). Exit 0 = all passed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist", "cairn")
TOKEN = "ZQ7X-smoke"  # distinctive needle for the FTS5 search


def main() -> int:
    if not os.access(BIN, os.X_OK):
        print(f"no executable {BIN} — run ./packaging/build-macos.sh first", file=sys.stderr)
        return 1

    db = tempfile.mktemp(suffix=".db")
    proc = subprocess.Popen(
        [BIN, "--stdio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env=dict(os.environ, META_DB_PATH=db),
    )

    def send(obj: dict) -> None:
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def exchange(obj: dict) -> dict:
        send(obj)
        line = proc.stdout.readline()  # blocks until the server answers
        return json.loads(line) if line.strip() else {}

    def text(resp: dict) -> str:
        # FastMCP serialises a list return as one content block per item, so
        # join them all rather than peeking at only the first.
        content = resp.get("result", {}).get("content", [])
        return "\n".join(b.get("text", "") for b in content)

    results: list[tuple[str, bool, str]] = []
    try:
        r = exchange({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "smoke", "version": "1"}}})
        ok = r.get("result", {}).get("serverInfo", {}).get("name") == "meta-assistant"
        results.append(("initialize over stdio", ok, r.get("result", {}).get("serverInfo", {}).get("name", "?")))

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})  # no response

        def call(id_: int, name: str, args: dict) -> dict:
            return exchange({"jsonrpc": "2.0", "id": id_, "method": "tools/call",
                             "params": {"name": name, "arguments": args}})

        con = text(call(2, "state_read_constitution", {}))
        results.append(("bundled constitution readable", len(con) > 200, f"{len(con)} chars"))

        skills = text(call(3, "state_list_skills", {}))
        results.append(("bundled skills readable", "capture" in skills, "contains 'capture'" if "capture" in skills else "no 'capture' skill"))

        wrote = text(call(4, "state_write", {"entity_type": "project",
                                             "data": {"name": f"packaging {TOKEN}", "description": "smoke"}}))
        results.append(("SQLite write", '"id"' in wrote, "project created" if '"id"' in wrote else wrote[:80]))

        found = text(call(5, "state_search", {"query": TOKEN}))
        results.append(("FTS5 search finds it", TOKEN in found, "found" if TOKEN in found else "not found"))
    finally:
        proc.stdin.close()
        stderr = proc.stderr.read()
        proc.wait(timeout=10)
        if os.path.exists(db):
            os.remove(db)

    print(f"  binary       : {BIN}")
    all_ok = True
    for label, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:32} {detail}")
        all_ok = all_ok and ok

    noise = [l for l in stderr.splitlines() if "closed file" in l or "Traceback" in l]
    print(f"  exit code    : {proc.returncode}")
    print(f"  stderr noise : {'none' if not noise else noise}")

    clean = all_ok and proc.returncode == 0 and not noise
    print("\nOK — all checks passed." if clean else "\nFAILED.")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
