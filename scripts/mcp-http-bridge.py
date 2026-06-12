#!/usr/bin/env python3
"""Minimal stdio↔HTTP MCP bridge.

A drop-in replacement for ``npx mcp-remote <url>`` for use with Claude
Desktop's ``claude_desktop_config.json``. Reads newline-delimited JSON-RPC
requests on stdin, POSTs them to a Streamable-HTTP MCP server, and
writes the responses to stdout — newline-delimited JSON.

Why this exists: the npm registry is blocked behind some corporate
proxies (Zscaler returns 403 on ``mcp-remote``), so ``npx -y mcp-remote``
silently fails to spawn. This bridge has no external dependencies — pure
stdlib — and works wherever Python 3 is available.

Scope: request/response only. The MCP server is allowed to respond with
either a plain JSON body or an SSE-formatted ``event: message\\ndata: ...``
block; we parse both. Server-initiated notifications via the long-lived
GET stream are not handled (no Cairn skill currently uses them). If you
later need them, the loop below is the place to extend.

Usage:
    python3 mcp-http-bridge.py http://localhost:8000/mcp

In claude_desktop_config.json:
    {
      "mcpServers": {
        "cairn": {
          "command": "/usr/bin/python3",
          "args": [
            "/abs/path/to/mcp-http-bridge.py",
            "http://localhost:8000/mcp"
          ]
        }
      }
    }
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
import urllib.error

# The bridge only ever talks to a local MCP server (localhost:<port>).
# Many corporate setups inject HTTP_PROXY / HTTPS_PROXY env vars (Zscaler,
# Cisco AnyConnect, etc.) into every process — including GUI children of
# Claude Desktop. urllib obeys those by default and tries to route even
# localhost requests through the proxy. When the proxy is down (e.g. user
# is at home, away from corp VPN) every tool call fails with a confusing
# transport error. Installing an empty ProxyHandler at module-load time
# makes urllib ignore HTTP_PROXY entirely for this process.
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({}))
)


def log(msg: str) -> None:
    """Write a diagnostic line to stderr (stdout is reserved for JSON-RPC)."""
    sys.stderr.write(f"[mcp-http-bridge] {msg}\n")
    sys.stderr.flush()


def parse_response(body: bytes, headers: dict[str, str]) -> dict | None:
    """Pull a JSON-RPC message out of a server response.

    The Streamable-HTTP transport returns either:
      - ``Content-Type: application/json`` with a JSON body, or
      - ``Content-Type: text/event-stream`` with one or more SSE frames
        in the form ``event: message\\ndata: {...}\\n\\n``.
    """
    ctype = (headers.get("content-type") or "").lower()
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    if "application/json" in ctype:
        return json.loads(text)
    if "event-stream" in ctype or text.startswith("event:"):
        # Find the first ``data: <json>`` line. SSE allows multi-line
        # data fields and multiple events; for MCP request/response we
        # only expect one event per response.
        m = re.search(r"^data:\s*(.+)$", text, flags=re.MULTILINE)
        if not m:
            return None
        return json.loads(m.group(1))
    # Fallback: try JSON anyway
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def main() -> int:
    if len(sys.argv) != 2:
        log("usage: mcp-http-bridge.py <url>")
        return 2
    url = sys.argv[1]

    session_id: str | None = None
    log(f"bridging stdin/stdout ↔ {url}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as e:
            log(f"discarding non-JSON line: {e}")
            continue

        # Notifications (no `id` field) don't expect a response. We still
        # forward them so the server is informed (initialized, cancelled, …).
        is_notification = "id" not in message

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session_id:
            headers["mcp-session-id"] = session_id

        body = line.encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                # Pull the session id from the initialize response so we
                # can attach it to every subsequent request.
                if session_id is None:
                    sid = resp.headers.get("mcp-session-id")
                    if sid:
                        session_id = sid
                        log(f"session established: {sid}")
                raw = resp.read()
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as e:
            log(f"HTTP {e.code} {e.reason} for {message.get('method')}")
            if not is_notification:
                err = {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": -32000, "message": f"HTTP {e.code}: {e.reason}"},
                }
                sys.stdout.write(json.dumps(err) + "\n")
                sys.stdout.flush()
            continue
        except (urllib.error.URLError, TimeoutError) as e:
            log(f"transport error: {e}")
            if not is_notification:
                err = {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": -32001, "message": f"transport: {e}"},
                }
                sys.stdout.write(json.dumps(err) + "\n")
                sys.stdout.flush()
            continue

        if is_notification:
            # Server may return 202 Accepted with no body; nothing to forward.
            continue

        parsed = parse_response(raw, resp_headers)
        if parsed is None:
            log(f"empty response for {message.get('method')}; skipping")
            continue
        sys.stdout.write(json.dumps(parsed) + "\n")
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    sys.exit(main())
