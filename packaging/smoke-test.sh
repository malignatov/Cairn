#!/usr/bin/env bash
# Functional smoke test for the frozen binary: initializes it over stdio,
# proves bundled content resolves, and exercises a SQLite write + FTS5 search.
# Thin wrapper around smoke_test.py (stdlib-only, so any python3 works).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/packaging/smoke_test.py"
