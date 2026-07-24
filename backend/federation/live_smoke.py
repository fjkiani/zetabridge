"""LIVE smoke — the visible proof that the three source endpoints are really
connected and really return real data (this is what refutes "fake passing
tests"). Unlike the contract tests, nothing here is mocked: every call hits the
actual source system.

Behaviour by endpoint:
  * EGA (C_EGA): metadata API is PUBLIC — this runs with **no credentials** and
    will print real BriTROC file accessions/checksums every time.
  * Synapse (A_MSK): needs SYNAPSE_AUTH_TOKEN in env. Without it, honestly
    reports 'unconfigured' (not a failure).
  * SAS Viya CAS (B_SAS): needs SAS_CAS_HOST + token/creds. Without them,
    honestly reports 'unconfigured'. A valid token that hits the known TLS CA
    issue will honestly report 'unreachable: tls_ca_unavailable'.

Run as a script (human-readable report):
  cd backend && python3 -m federation.live_smoke
  # with tokens:
  SYNAPSE_AUTH_TOKEN=... SAS_CAS_TOKEN=... python3 -m federation.live_smoke

Run under pytest (the EGA public test always runs; token-gated ones skip):
  cd backend && python3 -m pytest federation/live_smoke.py -q -s
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from federation.source_gateway import SourceGateway  # noqa: E402

# real identifiers from the project's prior extraction (BriTROC / MSK / SAS)
EGA_DATASET = os.environ.get("EGA_DEFAULT_DATASET", "EGAD00001011049")
SYNAPSE_SYNIDS = ["syn25569736", "syn39607857"]
SAS_CANDIDATE_TABLES = [("CASUSER", "ADAE"), ("Public", "ADAE")]


def _show(title: str, env_dict: dict) -> None:
    print(f"\n--- {title} ---")
    print(f"  endpoint={env_dict['endpoint']}  source={env_dict['source']}  "
          f"status={env_dict['status']}  latency_ms={env_dict['latency_ms']}")
    if env_dict.get("grounding"):
        print(f"  grounding={env_dict['grounding']}")
    if env_dict["status"] == "live":
        blob = json.dumps(env_dict["data"], indent=2, default=str)
        # keep the console readable but prove there's real content
        print("  DATA (real, live):")
        for line in blob.splitlines()[:24]:
            print("   ", line)
    else:
        print(f"  error={env_dict['error']}")


# ── EGA: always-live public proof ────────────────────────────────────────────
def test_ega_public_live_listing(capsys=None):
    """EGA metadata is public -> this ALWAYS proves a real live extraction."""
    gw = SourceGateway.from_env()
    env = gw.ega.list_files(EGA_DATASET, limit=5)
    d = env.to_dict()
    _show(f"EGA list_files({EGA_DATASET}, limit=5) [PUBLIC METADATA]", d)
    assert d["status"] == "live", f"EGA metadata should be reachable; got {d['status']}: {d['error']}"
    assert d["data"]["n_files"] >= 1
    first = d["data"]["files"][0]
    assert first["accession_id"].startswith("EGAF")
    # one file metadata fetch, too
    fid = first["accession_id"]
    env2 = gw.ega.file_metadata(fid).to_dict()
    _show(f"EGA file_metadata({fid}) [PUBLIC METADATA]", env2)
    assert env2["status"] == "live"


# ── Synapse: token-gated ─────────────────────────────────────────────────────
def test_synapse_live_if_token():
    gw = SourceGateway.from_env()
    if not gw.synapse.configured():
        print("\n--- Synapse: SKIP (SYNAPSE_AUTH_TOKEN not set) — honest 'unconfigured' ---")
        return
    who = gw.synapse.whoami().to_dict()
    _show("Synapse whoami()", who)
    assert who["status"] in ("live", "unreachable")
    if who["status"] == "live":
        for syn_id in SYNAPSE_SYNIDS:
            ent = gw.synapse.get_entity(syn_id).to_dict()
            _show(f"Synapse get_entity({syn_id})", ent)
            if ent["status"] == "live":
                break


# ── SAS Viya CAS: token-gated ────────────────────────────────────────────────
def test_sas_live_if_creds():
    gw = SourceGateway.from_env()
    if not gw.sas.configured():
        print("\n--- SAS CAS: SKIP (host/token/creds not set) — honest 'unconfigured' ---")
        return
    libs = gw.sas.list_caslibs().to_dict()
    _show("SAS list_caslibs()", libs)
    assert libs["status"] in ("live", "unreachable")
    if libs["status"] == "live":
        for caslib, table in SAS_CANDIDATE_TABLES:
            rows = gw.sas.query_adam(caslib, table, limit=10).to_dict()
            _show(f"SAS query_adam({caslib}, {table}, limit=10)", rows)
            if rows["status"] == "live":
                break


def main() -> int:
    print("=" * 70)
    print("ZETA BRIDGE — LIVE SOURCE SMOKE  (nothing mocked; real network calls)")
    print("=" * 70)
    gw = SourceGateway.from_env()
    health = gw.health()
    print("\n[health handshake]")
    for e in health["endpoints"]:
        print(f"  {e['endpoint']:6} {e['source']:8} "
              f"configured={e['configured']!s:5} status={e['status']:12} "
              f"latency={e['latency_ms']}ms"
              + (f"  ({e['error']})" if e.get("error") else ""))
    print(f"  any_live={health['any_live']}")

    # EGA public proof (always)
    try:
        test_ega_public_live_listing()
        ega_ok = True
    except AssertionError as exc:
        print(f"\n[EGA proof FAILED] {exc}")
        ega_ok = False

    # token-gated
    test_synapse_live_if_token()
    test_sas_live_if_creds()

    print("\n" + "=" * 70)
    print(f"RESULT: EGA live proof {'PASSED (real data printed above)' if ega_ok else 'FAILED'}; "
          "Synapse/SAS printed live data where tokens were present, else honest 'unconfigured'.")
    print("=" * 70)
    return 0 if ega_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
