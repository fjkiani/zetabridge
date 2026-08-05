"""Smoke: list contract tool IDs without requiring the mcp package for ID listing."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_ROOT.parent))

from genie_winners_mcp import SCHEMA_VERSION, TOOL_IDS  # noqa: E402
from genie_winners_mcp.envelope import (  # noqa: E402
    dumps,
    envelope,
    is_poison_path,
    not_implemented,
    refuse_poison_path,
    sha256_text,
)


def main() -> int:
    print(f"schema_version={SCHEMA_VERSION}")
    print(f"n_tools={len(TOOL_IDS)}")
    for tid in TOOL_IDS:
        print(f"  - {tid}")

    assert len(TOOL_IDS) >= 18
    assert "genie.diff_doc_claims" in TOOL_IDS
    assert "genie.stream_cna" in TOOL_IDS
    assert "genie.tmb_outlier_report" in TOOL_IDS
    assert "moa.probe_schema" in TOOL_IDS
    assert "genie.probe_consumer_wiring" in TOOL_IDS
    assert is_poison_path("foo.QUARANTINED.csv") is True
    assert is_poison_path("crc_tmb_msi_matrix.parquet") is False
    assert refuse_poison_path("x.poison.csv")["ok"] is False
    assert not_implemented("genie.list_assets")["error"].startswith("NOT_IMPLEMENTED:")
    assert not_implemented("genie.diff_doc_claims")["error"].startswith("NOT_IMPLEMENTED:")
    assert not_implemented("moa.probe_schema")["error"].startswith("NOT_IMPLEMENTED:")
    body = envelope(
        ok=True,
        tool="genie_winners.list_tool_ids",
        n=len(TOOL_IDS),
        receipt_sha=sha256_text("|".join(TOOL_IDS)),
        data={"tool_ids": TOOL_IDS},
    )
    assert "receipt_sha" in body
    print(dumps({"smoke": "ok", "n": body["n"]}))

    # Optional: import FastMCP server if mcp is installed
    try:
        from genie_winners_mcp.server import list_tool_ids  # noqa: WPS433

        import json

        payload = json.loads(list_tool_ids())
        assert payload["ok"] is True
        assert payload["n"] == len(TOOL_IDS)
        print("server_import_ok")
    except ModuleNotFoundError as e:
        print(f"server_import_skipped: {e}")

    print("smoke_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
