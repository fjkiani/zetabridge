"""GENIE + Winners MCP — FastMCP stdio server.

Tool bodies delegate to logic.py (IO/orchestration) + stats.py (analysis engine).
Every file-reader is poison-gated; every tool returns the uniform envelope.
Winner numbers are RE-DERIVED from the IPD backbone; no answer-key file is ever read.
See GENIE_WINNERS_MCP_BUILD.md.

NOTE: do NOT add `from __future__ import annotations` here — it stringifies parameter
annotations, which defeats FastMCP 1.12.x's get_origin() generic-skip guard and makes
tool registration raise TypeError on list[str]/Optional[...] params.
"""

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from . import SCHEMA_VERSION, TOOL_IDS
from . import logic as L
from .envelope import dumps, envelope, refuse_poison_path, sha256_text

mcp = FastMCP("genie-winners")


def _gate(*paths) -> Optional[str]:
    """Return a dumped poison-refusal envelope if any path is poison, else None."""
    for p in paths:
        if p:
            blocked = refuse_poison_path(p)
            if blocked:
                return dumps(blocked)
    return None


# ─── GENIE substrate ─────────────────────────────────────────────────────────


@mcp.tool(name="genie.list_assets")
def genie_list_assets(root_paths: Optional[list[str]] = None) -> str:
    """Inventory paths/sizes/mtimes under datasets/genie_r20 + optional roots (measured;
    poison files are LISTED with is_poison=true but never loaded)."""
    return dumps(L.list_assets(root_paths))


@mcp.tool(name="genie.matrix_summary")
def genie_matrix_summary(matrix_path: str) -> str:
    """Schema, n, null rates, TMB/assay/MSI stats from parquet/csv (computed live)."""
    g = _gate(matrix_path)
    if g:
        return g
    return dumps(L.matrix_summary(matrix_path))


@mcp.tool(name="genie.clinical_header_probe")
def genie_clinical_header_probe(paths: list[str]) -> str:
    """Header-only probe for OS/PFS/treatment/response column names (no row reads)."""
    g = _gate(*(paths or []))
    if g:
        return g
    return dumps(L.clinical_header_probe(paths))


@mcp.tool(name="genie.stream_mutation_flags")
def genie_stream_mutation_flags(
    mutation_path: str,
    sample_ids: list[str],
    genes: list[str],
    out_path: str,
) -> str:
    """Stream-filter a mutations/MAF file by SAMPLE_ID set + gene list → flags table + receipt
    (v1 local-file only; FILE_NOT_FOUND if absent — no Synapse re-download)."""
    g = _gate(mutation_path, out_path)
    if g:
        return g
    return dumps(L.stream_mutation_flags(mutation_path, sample_ids, genes, out_path))


@mcp.tool(name="genie.assay_tmb_strata")
def genie_assay_tmb_strata(
    matrix_path: str,
    tmb_col: Optional[str] = None,
    assay_col: Optional[str] = None,
) -> str:
    """TMB summary stratified by SEQ_ASSAY_ID (panel heterogeneity flagged, not corrected)."""
    g = _gate(matrix_path)
    if g:
        return g
    return dumps(L.assay_tmb_strata(matrix_path, tmb_col, assay_col))


@mcp.tool(name="genie.refuse_poison")
def genie_refuse_poison(path: str) -> str:
    """Hard fail if path matches QUARANTINE/poison; else ok pass (guard self-test)."""
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
    out_dir: Optional[str] = None,
) -> str:
    """Validate + write the pre-registered WINNER_DEFINITION.yaml (schema-checked, seeded)."""
    g = _gate(definition_path)
    if g:
        return g
    return dumps(L.winners_define(definition, definition_path, force, out_dir))


@mcp.tool(name="winners.hypotheses_draft")
def winners_hypotheses_draft(
    hypotheses: list[dict[str, Any]],
    backbone_path: Optional[str] = None,
    matrix_path: Optional[str] = None,
    out_dir: Optional[str] = None,
) -> str:
    """Validate ≤5 hypotheses; annotate each with on-disk field existence + typed counts."""
    g = _gate(backbone_path, matrix_path)
    if g:
        return g
    return dumps(L.winners_hypotheses_draft(hypotheses, backbone_path, matrix_path, out_dir))


@mcp.tool(name="winners.kill_tests")
def winners_kill_tests(
    hypotheses_path: str,
    definition_path: str,
    matrix_path: Optional[str] = None,
    outcomes_manifest_path: Optional[str] = None,
    backbone_path: Optional[str] = None,
    out_dir: Optional[str] = None,
) -> str:
    """Adversarial kill battery + secondary evaluation loop; every metric RE-DERIVED from IPD,
    labels-absent legs return BLOCKED (never asserts prior numbers)."""
    g = _gate(hypotheses_path, definition_path, matrix_path, outcomes_manifest_path, backbone_path)
    if g:
        return g
    return dumps(
        L.run_kill_battery(
            hypotheses_path, definition_path, matrix_path,
            outcomes_manifest_path, backbone_path, out_dir,
        )
    )


@mcp.tool(name="winners.scoreboard")
def winners_scoreboard(
    definition_path: str,
    hypotheses_path: str,
    kills_path: str,
    out_dir: Optional[str] = None,
) -> str:
    """Emit WINNERS_SCOREBOARD.md/json with rows built ONLY from prior tool artifacts."""
    g = _gate(definition_path, hypotheses_path, kills_path)
    if g:
        return g
    return dumps(L.winners_scoreboard(definition_path, hypotheses_path, kills_path, out_dir))


@mcp.tool(name="winners.pick")
def winners_pick(scoreboard_path: str, out_dir: Optional[str] = None) -> str:
    """ADVANCE-only shortlist + explicit DAR/IPD gaps + 8D-04-still-locked note."""
    g = _gate(scoreboard_path)
    if g:
        return g
    return dumps(L.winners_pick(scoreboard_path, out_dir))


# ─── Bridge ──────────────────────────────────────────────────────────────────


@mcp.tool(name="ids.intersect")
def ids_intersect(
    set_a_path: str,
    set_b_path: str,
    id_col_a: Optional[str] = None,
    id_col_b: Optional[str] = None,
    allow_fuzzy: bool = False,
) -> str:
    """Exact-match ID-set intersection + receipt (fuzzy stays gated behind explicit protocol)."""
    g = _gate(set_a_path, set_b_path)
    if g:
        return g
    if allow_fuzzy:
        return dumps(
            envelope(
                ok=False,
                tool="ids.intersect",
                n=0,
                error="FUZZY_REQUIRES_EXPLICIT_PROTOCOL: set allow_fuzzy only with Alpha-approved protocol",
            )
        )
    return dumps(L.ids_intersect(set_a_path, set_b_path, id_col_a, id_col_b))


@mcp.tool(name="pds.outcomes_manifest_read")
def pds_outcomes_manifest_read(manifest_path: str) -> str:
    """Read the local PDS outcomes manifest (no CAS login — use PDS-MCP for CAS)."""
    g = _gate(manifest_path)
    if g:
        return g
    return dumps(L.pds_outcomes_manifest_read(manifest_path))


@mcp.tool(name="genie_winners.list_tool_ids")
def list_tool_ids() -> str:
    """Smoke helper: return registered tool contract IDs + schema version."""
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
