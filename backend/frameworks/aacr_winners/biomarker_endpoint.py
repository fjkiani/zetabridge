"""Biomarker + endpoint definition helpers with honesty gates."""

from __future__ import annotations

from typing import Any


def define_biomarker(name: str, cohort: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """
    Explicit rules:
    - tissue TMB ≠ plasma pTMB
    - MSI only if column exists AND non-null counts > 0 (probe, don't invent)
    """
    name_l = name.lower().strip()
    cols = set(cohort.get("columns") or [])

    if name_l in ("tmb", "tissue_panel_tmb", "tmb_high", "tmb_bin"):
        if cohort.get("IS_GUARDANT_PTMB"):
            return {
                "ok": False,
                "error": "REFUSE: matrix marks IS_GUARDANT_PTMB — do not claim tissue TMB",
            }
        return {
            "ok": True,
            "name": "tissue_panel_TMB",
            "alias_requested": name,
            "columns_used": [c for c in ("tmb_bin", "tmb_mut_per_mb", "tmb") if c in cols],
            "label": "tissue_panel_TMB",
            "IS_PLASMA_PTMB": False,
            "requires_assay_stratification": True,
            "note": "Mandatory AssayStratifier before any TMB claim",
        }

    if name_l in ("msi", "msi_status", "msi_high"):
        probe = cohort.get("msi_probe") or {}
        available = bool(cohort.get("msi_available"))
        if not available:
            return {
                "ok": False,
                "error": "MSI_NOT_AVAILABLE: R20 clinical MSI columns absent or all-null",
                "msi_probe": probe,
                "msi_non_null": cohort.get("msi_non_null", 0),
            }
        return {
            "ok": True,
            "name": "MSI_STATUS",
            "columns_used": probe.get("msi_like_columns", []),
            "msi_non_null": cohort.get("msi_non_null"),
        }

    if name_l.startswith("driver") or name_l in ("kras", "nras", "braf", "tp53", "mmr"):
        return {
            "ok": True,
            "name": name,
            "source": "mutation_stream_flags",
            "requires_streaming": True,
            "note": "Gene flags via DuckDB stream; not MSI surrogate",
        }

    return {
        "ok": False,
        "error": f"UNKNOWN_BIOMARKER: {name}",
    }


def define_endpoint(name: str, cohort: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """
    GENIE clinical has DEAD / YEAR_DEATH etc.
    DEAD may be used ONLY as NEGATIVE control (prognostic-only kill).
    Never ADVANCE a 'winner' that is prognostic-only on DEAD without treatment contrast.
    """
    name_l = name.lower().strip()
    cols = set(cohort.get("columns") or [])

    if name_l in ("dead", "os_proxy_dead", "prognostic_dead"):
        if "DEAD" not in cols:
            return {"ok": False, "error": "DEAD column missing"}
        return {
            "ok": True,
            "name": "DEAD",
            "role": "NEGATIVE_CONTROL_ONLY",
            "treatment_contrast": "BLOCKED",
            "warning": (
                "Prognostic association with DEAD kills enrichment-winner claims; "
                "requires IPD treatment×marker for predictive ADVANCE"
            ),
        }

    if name_l in ("pfs", "os", "trial_os", "trial_pfs"):
        return {
            "ok": False,
            "error": "ENDPOINT_BLOCKED: trial OS/PFS not in GENIE — needs IPD lane",
            "role": "NEEDS_IPD",
        }

    if name_l in ("prevalence", "enrichment_or", "prior_table"):
        return {
            "ok": True,
            "name": name_l,
            "role": "GENIE_PRIOR_METRIC",
            "treatment_contrast": "BLOCKED",
        }

    return {"ok": False, "error": f"UNKNOWN_ENDPOINT: {name}"}
