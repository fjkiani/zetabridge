"""cBioPortal public REST client — replaces GENIE for tissue TMB / MSI / mutations.

No auth required (public data). Pulls per-sample clinical attributes (TMB, MSI,
OS, PFS) and gene mutations (KRAS/NRAS/BRAF) for CRC studies, unified into one
row-per-sample frame for outcome joining + external validation.
"""
from __future__ import annotations
import time
import urllib.request
import urllib.parse
import json

BASE = "https://www.cbioportal.org/api"

# CRC studies with outcome + TMB/MSI (verified reachable, no token)
CRC_STUDIES = [
    "crc_msk_2017",            # 1,134 mCRC, OS + TMB + MSI
    "crc_apc_impact_2020",     # 471 mCRC, OS + PFS + TMB + MSI
    "crc_eo_2020",             # 1,516 CRC (early-onset focus)
    "coad_silu_2022",          # 348 colon (AC-ICAM)
    "coadread_tcga_pan_can_atlas_2018",  # 594 TCGA COAD/READ
]

CLIN_ATTRS = [
    "OS_MONTHS", "OS_STATUS", "PFS_MONTHS", "PFS_STATUS",
    "TMB_NONSYNONYMOUS", "MSI_SCORE", "MSI_STATUS", "MSI_TYPE",
]
GENES = ["KRAS", "NRAS", "BRAF"]


def _get(path: str, params: dict | None = None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def _post(path: str, body, params: dict | None = None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Accept": "application/json",
                                          "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode())
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def list_samples(study: str) -> list[dict]:
    return _get(f"/studies/{study}/samples",
                {"projection": "SUMMARY", "pageSize": 20000})


def _attr_id(rec: dict) -> str:
    # DETAILED projection nests the attribute; SUMMARY/flat uses a top-level key
    return (rec.get("clinicalAttribute", {}) or {}).get("clinicalAttributeId") \
        or rec.get("clinicalAttributeId", "")


def clinical_for_study(study: str) -> dict[str, dict]:
    """Return {patient_id: {attr: value}} merging patient- and sample-level data.

    OS/PFS are patient-level; TMB/MSI are sample-level (MSK-IMPACT sequences the
    tumor sample). We pull both and merge onto the patient.
    """
    out: dict[str, dict] = {}
    s2p = sample_to_patient(study)
    for dtype in ("PATIENT", "SAMPLE"):
        data = _get(f"/studies/{study}/clinical-data",
                    {"clinicalDataType": dtype, "projection": "DETAILED",
                     "pageSize": 500000})
        for rec in data:
            attr = _attr_id(rec)
            if attr not in CLIN_ATTRS:
                continue
            pid = rec.get("patientId") or s2p.get(rec.get("sampleId", ""))
            if pid:
                # don't overwrite an existing value with a duplicate sample
                out.setdefault(pid, {}).setdefault(attr, rec.get("value"))
    return out


def sample_to_patient(study: str) -> dict[str, str]:
    return {s["sampleId"]: s["patientId"] for s in list_samples(study)}


def mutations_for_study(study: str) -> dict[str, set[str]]:
    """Return {patient_id: set of mutated genes among GENES}."""
    profiles = _get(f"/studies/{study}/molecular-profiles",
                    {"projection": "SUMMARY"})
    mut_prof = next((p["molecularProfileId"] for p in profiles
                     if p.get("molecularAlterationType") == "MUTATION_EXTENDED"), None)
    if not mut_prof:
        return {}
    s2p = sample_to_patient(study)
    # use the study's sequenced sample list (projection must be a query param)
    sample_list = f"{study}_sequenced"
    entrez = {"KRAS": 3845, "NRAS": 4893, "BRAF": 673}
    out: dict[str, set[str]] = {}
    # initialize all sequenced patients so 0 vs None (no call) is distinguishable
    for pid in s2p.values():
        out.setdefault(pid, set())
    for gene, eid in entrez.items():
        try:
            muts = _post(f"/molecular-profiles/{mut_prof}/mutations/fetch",
                         {"entrezGeneIds": [eid], "sampleListId": sample_list},
                         {"projection": "SUMMARY"})
        except Exception:
            muts = []
        for m in muts:
            pid = s2p.get(m.get("sampleId", ""))
            if pid:
                out.setdefault(pid, set()).add(gene)
    return out


def unified_crc_cohort(studies: list[str] | None = None) -> list[dict]:
    """One row per patient: study, TMB, MSI, RAS/BRAF, OS/PFS."""
    studies = studies or CRC_STUDIES
    rows: list[dict] = []
    for study in studies:
        try:
            clin = clinical_for_study(study)
        except Exception as e:
            print(f"  {study}: clinical failed ({e})")
            clin = {}
        try:
            muts = mutations_for_study(study)
        except Exception as e:
            print(f"  {study}: mutations failed ({e})")
            muts = {}
        for pid, attrs in clin.items():
            def fnum(k):
                v = attrs.get(k)
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
            os_m = fnum("OS_MONTHS")
            os_s = (attrs.get("OS_STATUS") or "").upper()
            pfs_m = fnum("PFS_MONTHS")
            pfs_s = (attrs.get("PFS_STATUS") or "").upper()
            genes = muts.get(pid)  # None if patient not sequenced
            msi_status = (attrs.get("MSI_STATUS") or attrs.get("MSI_TYPE") or "").upper()
            has_call = genes is not None
            genes = genes or set()
            rows.append({
                "study_id": study, "patient_id": pid,
                "tmb": fnum("TMB_NONSYNONYMOUS"),
                "msi_score": fnum("MSI_SCORE"),
                "msi_status": msi_status or None,
                "msi_high": 1 if ("HIGH" in msi_status or "MSI-H" in msi_status or "INSTABLE" in msi_status) else (0 if msi_status else None),
                "kras_mut": (1 if "KRAS" in genes else 0) if has_call else None,
                "nras_mut": (1 if "NRAS" in genes else 0) if has_call else None,
                "braf_mut": (1 if "BRAF" in genes else 0) if has_call else None,
                "ras_mut": (1 if ({"KRAS", "NRAS"} & genes) else 0) if has_call else None,
                "os_days": round(os_m * 30.44, 1) if os_m is not None else None,
                "os_event": 1 if ("DECEASED" in os_s or "1:" in os_s) else (0 if os_s else None),
                "pfs_days": round(pfs_m * 30.44, 1) if pfs_m is not None else None,
                "pfs_event": 1 if ("PROGRESS" in pfs_s or "RECUR" in pfs_s or "1:" in pfs_s) else (0 if pfs_s else None),
            })
        print(f"  {study}: {len(clin)} patients, {len(muts)} with mutation calls")
    return rows
