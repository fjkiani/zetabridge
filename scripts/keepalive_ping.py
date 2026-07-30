#!/usr/bin/env python3
"""ZetaBridge keep-alive.

Neo4j Aura Free auto-pauses (and eventually deletes) an instance after ~30 idle
days. A single trivial Bolt query resets that idle timer, which is the thing
that actually prevents the pause/delete. This script also GETs the backend
health endpoints to keep the Render free web service warm.

Intended to run on a GitHub Actions schedule (see .github/workflows/keepalive.yml)
every few days. ALL credentials come from environment variables (GitHub Actions
secrets); nothing is hardcoded. Exits non-zero only if the Aura ping fails, which
is the critical keep-alive action.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request


def _aura_ping() -> dict:
    """Open a Bolt session to Aura and run `RETURN 1` — resets the idle timer."""
    uri = os.environ["NEO4J_URI"]
    user = os.environ["NEO4J_USER"]
    pwd = os.environ["NEO4J_PASSWORD"]
    from neo4j import GraphDatabase  # imported here so import errors surface clearly

    t0 = time.time()
    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            ok = session.run("RETURN 1 AS ok").single()["ok"]
    finally:
        driver.close()
    return {"ok": ok == 1, "latency_ms": round((time.time() - t0) * 1000)}


def _http_get(url: str, headers: dict | None = None, attempts: int = 4) -> dict:
    # A Render free web service can be cold and its edge may briefly 404/5xx while
    # the dyno boots, so retry a few times before giving up. Warming the dyno is
    # itself a goal of this keep-alive, so tolerate the first slow response.
    hdrs = {"User-Agent": "zetabridge-keepalive/1.0"}
    hdrs.update(headers or {})
    last_err = None
    for i in range(attempts):
        req = urllib.request.Request(url, headers=hdrs)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (trusted URL)
                body = resp.read(2000).decode("utf-8", "replace")
                return {
                    "status": resp.status,
                    "latency_ms": round((time.time() - t0) * 1000),
                    "body": body[:300],
                }
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if i < attempts - 1:
                time.sleep(8)
    raise last_err  # type: ignore[misc]


def main() -> None:
    backend = os.environ.get(
        "BACKEND_URL", "https://zetabridge-api-q33d.onrender.com"
    ).rstrip("/")
    key = os.environ.get("ZETA_GRAPH_API_KEY", "")

    report: dict = {}

    # 1) CRITICAL: reset the Aura idle timer (this is what prevents the delete).
    report["aura"] = _aura_ping()

    # 2) Best-effort: warm the Render free dyno. A cold free dyno's edge can
    #    briefly 404/5xx while booting; these are non-fatal liveness signals.
    try:
        report["backend_health"] = _http_get(f"{backend}/health")
    except Exception as exc:  # noqa: BLE001
        report["backend_health"] = {"best_effort": True, "error": str(exc)}
    try:
        report["graph_health"] = _http_get(
            f"{backend}/api/graph/health", {"X-Zeta-Api-Key": key}
        )
    except Exception as exc:  # noqa: BLE001
        report["graph_health"] = {"best_effort": True, "error": str(exc)}

    aura_ok = bool(report["aura"].get("ok"))
    warm = report.get("backend_health", {}).get("status") == 200
    summary = (
        f"KEEPALIVE {'OK' if aura_ok else 'FAILED'} — "
        f"Aura idle-timer reset={aura_ok} ({report['aura'].get('latency_ms')} ms); "
        f"backend dyno warm={warm}."
    )
    print(summary)
    print(json.dumps(report, indent=2))

    if not aura_ok:
        print("FATAL: Aura keep-alive ping failed (idle timer NOT reset).", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
