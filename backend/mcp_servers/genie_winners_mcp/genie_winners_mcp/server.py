"""GENIE + Winners MCP — FastMCP stdio server.

Stubs fail loud until implemented. See GENIE_WINNERS_MCP_BUILD.md.
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from . import SCHEMA_VERSION, TOOL_IDS
from .envelope import dumps, envelope, not_implemented, refuse_poison_path, sha256_text

mcp = FastMCP("genie-winners")


# ─── GENIE substrate ─────────────────────────────────────────────────────────


@mcp.tool(name="genie.list_assets")
def genie_list_assets(root_paths: Optional[list[str]] = None) -> str:
    """Inventory paths/sizes under datasets/genie_r20 + optional raw dirs (measured)."""
    _ = root_paths
    return dumps(not_implemented("genie.list_assets"))


@mcp.tool(name="genie.matrix_summary")
def genie_matrix_summary(matrix_path: str) -> str:
    """Schema, n, key column stats from parquet/csv (computed live)."""
    blocked = refuse_poison_path(matrix_path)
    if blocked:
        return dumps(blocked)
    return dumps(not_implemented("genie.matrix_summary"))


@mcp.tool(name="genie.clinical_header_probe")
def genie_clinical_header_probe(paths: list[str]) -> str:
    """Header-only probe of clinical files."""
    for p in paths or []:
        blocked = refuse_poison_path(p)
        if blocked:
            return dumps(blocked)
    return dumps(not_implemented("genie.clinical_header_probe"))


@mcp.tool(name="genie.stream_mutation_flags")
def genie_stream_mutation_flags(
    mutation_path: str,
    sample_ids: list[str],
    genes: list[str],
    out_path: str,
) -> str:
    """DuckDB/stream filter by SAMPLE_ID set + gene list → flags table + receipt."""
    for p in (mutation_path, out_path):
        blocked = refuse_poison_path(p)
        if blocked:
            return dumps(blocked)
    _ = sample_ids, genes
    return dumps(not_implemented("genie.stream_mutation_flags"))


@mcp.tool(name="genie.assay_tmb_strata")
def genie_assay_tmb_strata(
    matrix_path: str,
    tmb_col: Optional[str] = None,
    assay_col: Optional[str] = None,
) -> str:
    """TMB by SEQ_ASSAY_ID (or assay column found on matrix)."""
    blocked = refuse_poison_path(matrix_path)
    if blocked:
        return dumps(blocked)
    _ = tmb_col, assay_col
    return dumps(not_implemented("genie.assay_tmb_strata"))


@mcp.tool(name="genie.refuse_poison")
def genie_refuse_poison(path: str) -> str:
    """Hard fail if path matches QUARANTINE/poison; else ok pass."""
    blocked = refuse_poison_path(path)
    if blocked:
        return dumps(blocked)
    body = envelope(
        ok=True,
        tool="genie.refuse_poison",
        n=1,
        receipt_sha=sha256_text(path),
        data={"path": path, "poison": False},
    )
    return dumps(body)


# ─── Winners plane ───────────────────────────────────────────────────────────


@mcp.tool(name="winners.define")
def winners_define(
    definition: Optional[dict[str, Any]] = None,
    definition_path: Optional[str] = None,
    force: bool = False,
) -> str:
    """Write/validate WINNER_DEFINITION.yaml (pre-reg schema)."""
    _ = definition, definition_path, force
    return dumps(not_implemented("winners.define"))


@mcp.tool(name="winners.hypotheses_draft")
def winners_hypotheses_draft(hypotheses: list[dict[str, Any]]) -> str:
    """Scaffold ≤5 hypotheses; tool validates schema (agent fills biology)."""
    _ = hypotheses
    return dumps(not_implemented("winners.hypotheses_draft"))


@mcp.tool(name="winners.kill_tests")
def winners_kill_tests(
    hypotheses_path: str,
    definition_path: str,
    matrix_path: Optional[str] = None,
    outcomes_manifest_path: Optional[str] = None,
) -> str:
    """Adversarial battery; compute metrics if labels exist — never assert prior numbers."""
    for p in (hypotheses_path, definition_path, matrix_path, outcomes_manifest_path):
        if p:
            blocked = refuse_poison_path(p)
            if blocked:
                return dumps(blocked)
    return dumps(not_implemented("winners.kill_tests"))


@mcp.tool(name="winners.scoreboard")
def winners_scoreboard(
    definition_path: str,
    hypotheses_path: str,
    kills_path: str,
) -> str:
    """Emit WINNERS_SCOREBOARD.md/json from tool outputs only."""
    for p in (definition_path, hypotheses_path, kills_path):
        blocked = refuse_poison_path(p)
        if blocked:
            return dumps(blocked)
    return dumps(not_implemented("winners.scoreboard"))


@mcp.tool(name="winners.pick")
def winners_pick(scoreboard_path: str) -> str:
    """ADVANCE-only shortlist + required DAR/IPD gaps (structured)."""
    blocked = refuse_poison_path(scoreboard_path)
    if blocked:
        return dumps(blocked)
    return dumps(not_implemented("winners.pick"))


# ─── Bridge ──────────────────────────────────────────────────────────────────


@mcp.tool(name="ids.intersect")
def ids_intersect(
    set_a_path: str,
    set_b_path: str,
    id_col_a: Optional[str] = None,
    id_col_b: Optional[str] = None,
    allow_fuzzy: bool = False,
) -> str:
    """Generic ID intersection receipt (exact match default)."""
    for p in (set_a_path, set_b_path):
        blocked = refuse_poison_path(p)
        if blocked:
            return dumps(blocked)
    if allow_fuzzy:
        return dumps(
            envelope(
                ok=False,
                tool="ids.intersect",
                n=0,
                error="FUZZY_REQUIRES_EXPLICIT_PROTOCOL: set allow_fuzzy only with Alpha-approved protocol",
            )
        )
    _ = id_col_a, id_col_b
    return dumps(not_implemented("ids.intersect"))


@mcp.tool(name="pds.outcomes_manifest_read")
def pds_outcomes_manifest_read(manifest_path: str) -> str:
    """Read local PDS outcomes manifest (no CAS login — use PDS-MCP for CAS)."""
    blocked = refuse_poison_path(manifest_path)
    if blocked:
        return dumps(blocked)
    return dumps(not_implemented("pds.outcomes_manifest_read"))


@mcp.tool(name="genie_winners.list_tool_ids")
def list_tool_ids() -> str:
    """Smoke helper: return registered tool contract IDs."""
    body = envelope(
        ok=True,
        tool="genie_winners.list_tool_ids",
        n=len(TOOL_IDS),
        receipt_sha=sha256_text("|".join(TOOL_IDS)),
        data={"tool_ids": TOOL_IDS, "schema_version": SCHEMA_VERSION},
    )
    return dumps(body)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
