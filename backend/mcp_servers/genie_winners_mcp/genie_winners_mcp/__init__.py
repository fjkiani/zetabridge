"""genie-winners-mcp — GENIE substrate + Winners plane (stubs fail loud)."""

SCHEMA_VERSION = "genie-winners-mcp/0.1.1"

TOOL_IDS = [
    # GENIE substrate
    "genie.list_assets",
    "genie.diff_doc_claims",
    "genie.matrix_summary",
    "genie.clinical_header_probe",
    "genie.stream_mutation_flags",
    "genie.stream_cna",
    "genie.assay_tmb_strata",
    "genie.tmb_outlier_report",
    "genie.refuse_poison",
    "genie.probe_consumer_wiring",
    # MoA probe
    "moa.probe_schema",
    # Winners plane
    "winners.define",
    "winners.hypotheses_draft",
    "winners.kill_tests",
    "winners.scoreboard",
    "winners.pick",
    # Bridge
    "ids.intersect",
    "pds.outcomes_manifest_read",
]
