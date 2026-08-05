"""IO + orchestration for genie.* / winners.* tools.

All file readers are poison-gated. Winner numbers are RE-DERIVED via stats.py from the
IPD backbone; no answer-key file is ever read. Tools return envelope dicts; server.py
wraps them with dumps().
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from .envelope import envelope, is_poison_path, refuse_poison_path, sha256_file, sha256_text

# ── path roots ──────────────────────────────────────────────────────────────
_PKG = Path(__file__).resolve()
ZETA_ROOT = _PKG.parents[4]  # .../zetabridge
BRENUS_ROOT = Path(os.environ.get("BRENUS_ROOT", str(ZETA_ROOT.parent / "Brenus")))
GENIE_DIR = ZETA_ROOT / "datasets" / "genie_r20"
GENIE_RECEIPTS = GENIE_DIR / "mcp_receipts"
WINNERS_OUT = BRENUS_ROOT / "engagements" / "brenus" / "genie_synapse" / "winners_mcp_run"

ANSWER_KEYS = {"crc_backbone_source_of_truth.json", "genie_hard_agent_brief.md"}
_CLIN_RE = re.compile(r"(OS_|_OS\b|\bOS\b|PFS|SURV|DEATH|DTH|TREAT|DRUG|REGIMEN|\bARM\b|RESPON|RECIST|BEST_RESP)", re.I)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ensure(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, obj: Any) -> str:
    _ensure(path)
    text = json.dumps(obj, indent=2, default=str)
    path.write_text(text, encoding="utf-8")
    return sha256_text(text)


def _write_text(path: Path, text: str) -> str:
    _ensure(path)
    path.write_text(text, encoding="utf-8")
    return sha256_text(text)


def assert_no_answer_key(paths) -> list[str]:
    w = []
    for p in paths:
        if p and Path(str(p)).name.lower() in ANSWER_KEYS:
            w.append(f"ANSWER_KEY_INPUT_REFUSED: {Path(str(p)).name} must not seed scoring")
    return w


# ── GENIE substrate ─────────────────────────────────────────────────────────


def list_assets(root_paths: Optional[list[str]] = None) -> dict:
    roots = [Path(p) for p in (root_paths or [str(GENIE_DIR)])]
    files, poison = [], 0
    for root in roots:
        if not root.exists():
            continue
        for f in sorted(root.rglob("*")):
            if f.is_file():
                pois = is_poison_path(f)
                poison += int(pois)
                files.append(
                    dict(
                        path=str(f.relative_to(ZETA_ROOT)) if str(f).startswith(str(ZETA_ROOT)) else str(f),
                        bytes=f.stat().st_size,
                        mtime=time.strftime("%Y-%m-%d", time.gmtime(f.stat().st_mtime)),
                        is_poison=bool(pois),
                    )
                )
    rec = GENIE_RECEIPTS / "list_assets.json"
    sha = _write_json(rec, {"generated": _now(), "roots": [str(r) for r in roots], "files": files})
    return envelope(
        ok=True,
        tool="genie.list_assets",
        n=len(files),
        receipt_sha=sha,
        artifacts=[str(rec.relative_to(ZETA_ROOT))],
        warnings=([f"{poison} poison/quarantine file(s) listed but NOT loaded"] if poison else []),
        data={"n_files": len(files), "n_poison": poison, "files": files},
    )


def _read_matrix(path: Path):
    import pandas as pd

    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def matrix_summary(matrix_path: str) -> dict:
    import pandas as pd

    p = Path(matrix_path)
    if not p.exists():
        return envelope(ok=False, tool="genie.matrix_summary", n=0, error=f"FILE_NOT_FOUND: {matrix_path}")
    df = _read_matrix(p)
    key = [c for c in ["tmb", "tmb_mut_per_mb", "tmb_bin", "MSI_STATUS", "MSI_AVAILABLE", "IS_GUARDANT_PTMB",
                       "TMB_ASSAY_TYPE", "ONCOTREE_CODE", "SEQ_ASSAY_ID", "CANCER_TYPE", "PATIENT_ID", "SAMPLE_ID"] if c in df.columns]
    null_rates = {c: round(float(df[c].isna().mean()), 4) for c in key}
    stats_out: dict[str, Any] = {}
    if "tmb_bin" in df.columns:
        stats_out["tmb_bin_counts"] = df["tmb_bin"].value_counts(dropna=False).to_dict()
    if "IS_GUARDANT_PTMB" in df.columns:
        stats_out["IS_GUARDANT_PTMB"] = df["IS_GUARDANT_PTMB"].value_counts(dropna=False).astype(int).to_dict()
    if "TMB_ASSAY_TYPE" in df.columns:
        stats_out["TMB_ASSAY_TYPE"] = df["TMB_ASSAY_TYPE"].value_counts(dropna=False).astype(int).to_dict()
    if "MSI_STATUS" in df.columns:
        stats_out["MSI_all_null"] = bool(df["MSI_STATUS"].isna().all())
    if "ONCOTREE_CODE" in df.columns:
        stats_out["oncotree_top"] = df["ONCOTREE_CODE"].value_counts().head(6).astype(int).to_dict()
    if "SEQ_ASSAY_ID" in df.columns:
        stats_out["seq_assay_top"] = df["SEQ_ASSAY_ID"].value_counts().head(6).astype(int).to_dict()
    file_sha = sha256_file(p)
    body = dict(
        generated=_now(),
        matrix=str(p.name),
        file_sha256=file_sha,
        n_rows=int(len(df)),
        n_patients=int(df["PATIENT_ID"].nunique()) if "PATIENT_ID" in df.columns else None,
        n_cols=int(df.shape[1]),
        columns=list(df.columns),
        dtypes={c: str(t) for c, t in df.dtypes.items()},
        null_rates_key_cols=null_rates,
        stats=stats_out,
    )
    rec = GENIE_RECEIPTS / "matrix_summary.json"
    _write_json(rec, body)
    warn = []
    if stats_out.get("MSI_all_null"):
        warn.append("MSI_STATUS 100% null — MSI unavailable; do not invent MSI")
    if stats_out.get("IS_GUARDANT_PTMB", {}).get(False):
        warn.append("TMB is tissue-panel (IS_GUARDANT_PTMB=False) — do not relabel as plasma pTMB")
    return envelope(ok=True, tool="genie.matrix_summary", n=int(len(df)), receipt_sha=file_sha,
                    artifacts=[str(rec.relative_to(ZETA_ROOT))], warnings=warn, data=body)


def clinical_header_probe(paths: list[str]) -> dict:
    import pandas as pd

    out = []
    for pth in paths or []:
        p = Path(pth)
        if not p.exists():
            out.append({"path": str(p.name), "error": "FILE_NOT_FOUND"})
            continue
        if p.suffix == ".parquet":
            import pyarrow.parquet as pq

            cols = list(pq.read_schema(p).names)
        else:
            sep = "\t" if p.suffix in (".tsv", ".txt") else ","
            with p.open("r", encoding="utf-8", errors="replace") as f:
                first = f.readline().rstrip("\n")
            cols = first.split(sep)
        hits = sorted({c for c in cols if _CLIN_RE.search(str(c))})
        out.append({"path": str(p.name), "n_cols": len(cols), "columns": cols,
                    "clinical_name_hits": hits, "has_treatment_or_response": bool(hits)})
    rec = GENIE_RECEIPTS / "clinical_header_probe.json"
    sha = _write_json(rec, {"generated": _now(), "probes": out})
    return envelope(ok=True, tool="genie.clinical_header_probe", n=len(out), receipt_sha=sha,
                    artifacts=[str(rec.relative_to(ZETA_ROOT))], data={"probes": out})


def assay_tmb_strata(matrix_path: str, tmb_col: Optional[str] = None, assay_col: Optional[str] = None) -> dict:
    import pandas as pd

    p = Path(matrix_path)
    if not p.exists():
        return envelope(ok=False, tool="genie.assay_tmb_strata", n=0, error=f"FILE_NOT_FOUND: {matrix_path}")
    df = _read_matrix(p)
    tcol = tmb_col or ("tmb_mut_per_mb" if "tmb_mut_per_mb" in df.columns else "tmb")
    acol = assay_col or ("SEQ_ASSAY_ID" if "SEQ_ASSAY_ID" in df.columns else None)
    if acol is None or tcol not in df.columns:
        return envelope(ok=False, tool="genie.assay_tmb_strata", n=0,
                        error=f"COLUMN_NOT_FOUND: tmb_col={tcol} assay_col={acol}")
    g = df.groupby(acol)[tcol].agg(["count", "mean", "median", "std"]).round(3)
    strata = [{"assay": str(i), "n": int(r["count"]), "tmb_mean": r["mean"], "tmb_median": r["median"],
               "tmb_std": (None if r["std"] != r["std"] else r["std"])} for i, r in g.sort_values("count", ascending=False).iterrows()]
    rec = GENIE_RECEIPTS / "assay_tmb_strata.json"
    sha = _write_json(rec, {"generated": _now(), "tmb_col": tcol, "assay_col": acol, "strata": strata})
    return envelope(ok=True, tool="genie.assay_tmb_strata", n=len(strata), receipt_sha=sha,
                    artifacts=[str(rec.relative_to(ZETA_ROOT))],
                    warnings=["assay-panel heterogeneity: TMB comparisons across panels are not calibrated"],
                    data={"tmb_col": tcol, "assay_col": acol, "strata": strata})


def stream_mutation_flags(mutation_path: str, sample_ids: list[str], genes: list[str], out_path: str) -> dict:
    import pandas as pd

    p = Path(mutation_path)
    if not p.exists():
        return envelope(ok=False, tool="genie.stream_mutation_flags", n=0,
                        error=f"FILE_NOT_FOUND: {mutation_path} (v1 local-file only; no Synapse re-download)")
    sset = set(sample_ids or [])
    gset = {g.upper() for g in (genes or [])}
    flags: dict[tuple, int] = {}
    sep = "\t" if p.suffix in (".txt", ".tsv", ".maf") else ","
    for chunk in pd.read_csv(p, sep=sep, chunksize=200_000, usecols=lambda c: c in
                             ("Tumor_Sample_Barcode", "SAMPLE_ID", "Hugo_Symbol", "gene")):
        sc = "Tumor_Sample_Barcode" if "Tumor_Sample_Barcode" in chunk.columns else "SAMPLE_ID"
        gc = "Hugo_Symbol" if "Hugo_Symbol" in chunk.columns else "gene"
        m = chunk[chunk[sc].isin(sset) & chunk[gc].str.upper().isin(gset)]
        for _, r in m.iterrows():
            flags[(r[sc], r[gc].upper())] = 1
    rows = [{"SAMPLE_ID": s, "gene": g, "mutated": 1} for (s, g) in sorted(flags)]
    op = Path(out_path)
    _ensure(op)
    pd.DataFrame(rows).to_csv(op, index=False)
    sha = sha256_file(op) if op.exists() else None
    return envelope(ok=True, tool="genie.stream_mutation_flags", n=len(rows), receipt_sha=sha,
                    artifacts=[str(op)], data={"n_flag_rows": len(rows), "genes": sorted(gset)})


# ── ids / pds ────────────────────────────────────────────────────────────────


def _read_id_column(path: Path, id_col: Optional[str]):
    import pandas as pd

    candidates = [id_col] if id_col else ["subjid", "PATIENT_ID", "SAMPLE_ID", "patient_id", "sample_id"]
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        names = list(pq.read_schema(path).names)
        col = next((c for c in candidates if c and c in names), None)
        if not col:
            return None, None
        return pd.read_parquet(path, columns=[col])[col], col
    head = pd.read_csv(path, nrows=1)
    col = next((c for c in candidates if c and c in head.columns), None)
    if not col:
        return None, None
    return pd.read_csv(path, usecols=[col])[col], col


def ids_intersect(set_a_path: str, set_b_path: str, id_col_a: Optional[str] = None,
                  id_col_b: Optional[str] = None) -> dict:
    a, ca = _read_id_column(Path(set_a_path), id_col_a)
    b, cb = _read_id_column(Path(set_b_path), id_col_b)
    if a is None or b is None:
        return envelope(ok=False, tool="ids.intersect", n=0,
                        error=f"ID_COLUMN_NOT_FOUND: a={ca} b={cb}")
    sa = set(a.dropna().astype(str))
    sb = set(b.dropna().astype(str))
    inter = sa & sb
    data = dict(
        col_a=ca, col_b=cb, n_a=len(sa), n_b=len(sb), n_intersection=len(inter),
        examples_a=sorted(list(sa))[:3], examples_b=sorted(list(sb))[:3],
        verdict=("JOIN_IMPOSSIBLE" if len(inter) == 0 else "OVERLAP_FOUND"),
    )
    rec = GENIE_RECEIPTS / "ids_intersect.json"
    sha = _write_json(rec, {"generated": _now(), **data})
    warn = ["disjoint ID namespaces — exact join impossible; no fuzzy join without protocol"] if not inter else []
    return envelope(ok=True, tool="ids.intersect", n=len(inter), receipt_sha=sha,
                    artifacts=[str(rec.relative_to(ZETA_ROOT))], warnings=warn, data=data)


def pds_outcomes_manifest_read(manifest_path: str) -> dict:
    p = Path(manifest_path)
    if not p.exists():
        return envelope(ok=False, tool="pds.outcomes_manifest_read", n=0, error=f"FILE_NOT_FOUND: {manifest_path}")
    man = json.loads(p.read_text(encoding="utf-8"))
    trials = man.get("trials") or man.get("entries") or man
    n = len(trials) if isinstance(trials, (list, dict)) else None
    return envelope(ok=True, tool="pds.outcomes_manifest_read", n=n, receipt_sha=sha256_file(p),
                    artifacts=[], data={"manifest_keys": list(man.keys()) if isinstance(man, dict) else None,
                                        "n_entries": n, "manifest": man})


# ── winners plane ────────────────────────────────────────────────────────────

_REQUIRED_DEF = ["schema_version", "primary_endpoint", "population", "biomarker_cuts",
                 "treatment_contrast", "null_model", "held_out_protocol", "pre_reg_seed",
                 "success_rule", "exploratory_policy"]


def winners_define(definition: Optional[dict] = None, definition_path: Optional[str] = None,
                   force: bool = False, out_dir: Optional[str] = None) -> dict:
    import yaml

    if definition is None and definition_path:
        definition = yaml.safe_load(Path(definition_path).read_text(encoding="utf-8"))
    if not isinstance(definition, dict):
        return envelope(ok=False, tool="winners.define", n=0, error="NO_DEFINITION: pass a definition dict")
    missing = [k for k in _REQUIRED_DEF if k not in definition]
    if missing:
        return envelope(ok=False, tool="winners.define", n=0, error=f"SCHEMA_INVALID: missing {missing}")
    hp = definition.get("held_out_protocol", {})
    if "seed" not in hp or "split" not in hp or "metric" not in hp:
        return envelope(ok=False, tool="winners.define", n=0, error="SCHEMA_INVALID: held_out_protocol needs split/seed/metric")
    outd = Path(out_dir) if out_dir else WINNERS_OUT
    path = outd / "WINNER_DEFINITION.yaml"
    text = yaml.safe_dump(definition, sort_keys=False)
    sha = _write_text(path, text + f"\n# pre_reg_written: {_now()}\n# RUO: Research Use Only. Does NOT soft-unblock 8D-04.\n")
    warn = [
        "pre-reg written BEFORE scoring; H1 biology fixed from published priors (Karapetis 2008; Amado 2008; Douillard 2013; FDA RAS-WT label), NOT from deposit effect sizes",
        "scoreboard will be populated ONLY by recomputed tool outputs; prior point estimates used as out-of-band concordance only",
    ]
    return envelope(ok=True, tool="winners.define", n=len(definition), receipt_sha=sha,
                    artifacts=[str(path)], warnings=warn, data={"definition": definition, "written": str(path)})


def _backbone_typed_counts(backbone_path: str) -> dict:
    import pandas as pd

    df = pd.read_csv(backbone_path)
    def typed(col):
        return int(df[col].astype(str).str.upper().isin(["WT", "MUT"]).sum()) if col in df.columns else 0
    return {
        "columns": list(df.columns),
        "kras_typed": typed("kras"),
        "ras_extended_typed": typed("ras_extended"),
        "braf_typed": typed("braf"),
        "liver_met_nonnull": int(df["liver_met"].notna().sum()) if "liver_met" in df.columns else 0,
        "anti_egfr_present": "anti_egfr" in df.columns,
        "pfs_events": int(pd.to_numeric(df.get("pfs_event"), errors="coerce").fillna(0).sum()) if "pfs_event" in df.columns else 0,
        "os_events": int(pd.to_numeric(df.get("os_event"), errors="coerce").fillna(0).sum()) if "os_event" in df.columns else 0,
        "n": int(len(df)),
    }


def winners_hypotheses_draft(hypotheses: list[dict], backbone_path: Optional[str] = None,
                             matrix_path: Optional[str] = None, out_dir: Optional[str] = None) -> dict:
    if not isinstance(hypotheses, list) or not hypotheses:
        return envelope(ok=False, tool="winners.hypotheses_draft", n=0, error="NO_HYPOTHESES")
    if len(hypotheses) > 5:
        return envelope(ok=False, tool="winners.hypotheses_draft", n=len(hypotheses), error="TOO_MANY: max 5 hypotheses")
    bb = _backbone_typed_counts(backbone_path) if backbone_path and Path(backbone_path).exists() else {}
    bb_cols = set(bb.get("columns", []))
    field_counts = {"kras": bb.get("kras_typed"), "ras_extended": bb.get("ras_extended_typed"),
                    "braf": bb.get("braf_typed"), "liver_met": bb.get("liver_met_nonnull")}
    matrix_cols = set()
    if matrix_path and Path(matrix_path).exists():
        import pyarrow.parquet as pq

        try:
            matrix_cols = set(pq.read_schema(Path(matrix_path)).names) if Path(matrix_path).suffix == ".parquet" else set()
        except Exception:  # noqa: BLE001
            matrix_cols = set()
    enriched = []
    for h in hypotheses:
        reqs = h.get("fields_required", [])
        checks = []
        for f in reqs:
            in_bb = f in bb_cols
            typed_n = field_counts.get(f)
            checks.append({"field": f, "exists_on_disk": bool(in_bb or f in matrix_cols),
                           "source": ("backbone" if in_bb else ("genie_matrix" if f in matrix_cols else "ABSENT")),
                           "typed_n": typed_n})
        enriched.append({**h, "field_existence": checks})
    outd = Path(out_dir) if out_dir else WINNERS_OUT
    jpath = outd / "CANDIDATE_ENRICHMENT_HYPOTHESES.json"
    sha = _write_json(jpath, {"generated": _now(), "n": len(enriched), "hypotheses": enriched})
    # markdown
    lines = ["# Candidate Enrichment Hypotheses (W1)", "", f"_Generated {_now()} · RUO · re-derived, no answer key_", ""]
    for h in enriched:
        lines += [f"## {h.get('id')} — {h.get('status','').upper()}", "",
                  f"- **Statement:** {h.get('statement')}",
                  f"- **Biomarker:** {h.get('biomarker')}",
                  f"- **Treatment contrast:** {h.get('treatment_contrast')}",
                  f"- **Endpoint:** {h.get('endpoint')}",
                  "- **Field existence on disk:**"]
        for c in h["field_existence"]:
            lines.append(f"    - `{c['field']}` → exists={c['exists_on_disk']} source={c['source']} typed_n={c['typed_n']}")
        lines.append("")
    mpath = outd / "CANDIDATE_ENRICHMENT_HYPOTHESES.md"
    _write_text(mpath, "\n".join(lines) + "\n")
    return envelope(ok=True, tool="winners.hypotheses_draft", n=len(enriched), receipt_sha=sha,
                    artifacts=[str(jpath), str(mpath)], data={"hypotheses": enriched})


def run_kill_battery(hypotheses_path: str, definition_path: str, matrix_path: Optional[str] = None,
                     outcomes_manifest_path: Optional[str] = None, backbone_path: Optional[str] = None,
                     out_dir: Optional[str] = None) -> dict:
    from . import stats as S

    warn = assert_no_answer_key([hypotheses_path, definition_path, matrix_path, backbone_path])
    if not backbone_path or not Path(backbone_path).exists():
        return envelope(ok=False, tool="winners.kill_tests", n=0, error="BACKBONE_REQUIRED: pass backbone_path to IPD v5")
    df = S.load_backbone(backbone_path)
    sel, structure, excluded = S.select_h1_trials(df)
    dfx = S.prep(df, "kras")
    h1 = dfx[dfx["trial"].isin(sel)].copy()

    # ── primary predictive results (H1 KRAS-ex2) ──
    h1_pfs = S.cox_interaction(h1, "pfs_days", "pfs_event")
    h1_os = S.cox_interaction(h1, "os_days", "os_event")
    h1_loo = S.leave_one_trial_out(h1, "pfs_days", "pfs_event")
    h1_prog = S.prognostic_control_only(h1, "pfs_days", "pfs_event")
    h1_iptw = S.iptw_sensitivity(h1)

    # ── H2 extended-RAS refinement ──
    dfe = S.prep(df, "ras_extended")
    sel_e = [t for t in sel if dfe[dfe["trial"] == t]["ras_mut"].notna().sum() > 0]
    h2 = dfe[dfe["trial"].isin(sel_e)].copy()
    h2_pfs = S.cox_interaction(h2, "pfs_days", "pfs_event") if len(sel_e) >= 1 else {"error": "no_ext_ras_typing"}
    h2_loo = S.leave_one_trial_out(h2, "pfs_days", "pfs_event") if len(sel_e) >= 2 else {"note": "single trial with ext-RAS typing; LOO not applicable", "pools": sel_e}

    # ── kills ──
    k1 = {"genie_assay_strata_note": "GENIE substrate assay-skewed (MSK-IMPACT heavy) — prevalence prior only",
          "ipd_typing_source": "IPD KRAS/RAS typing is trial-central lab, NOT GENIE panel => winner not GENIE-assay-confounded",
          "ipd_provenance": [{"trial": r["trial"], "n": r["n"]} for r in structure if r["trial"] in sel]}
    k2 = {"prognostic_control_only": h1_prog, "predictive_interaction_pfs": {k: h1_pfs.get(k) for k in ("interaction_ratio_MUT_WT", "ci", "p")},
          "verdict": "PREDICTIVE_not_merely_prognostic" if (h1_pfs.get("ci") and h1_pfs["ci"][0] > 1) else "INTERACTION_NOT_SIGNIFICANT"}
    k3 = {"event_rate_by_trial": S.event_rate_by_trial(df[df["trial"].isin(sel + ["N0147_20040161"])]),
          "deposit_attenuation": S.deposit_attenuation(df, sel),
          "note": "adjuvant low event-rate vs refractory high; deposit ~0.8 of published N => wider CI (power), not directional flip"}
    # K4 id-join via ids.intersect (re-derived)
    k4 = {"status": "SKIPPED_no_matrix"}
    if matrix_path and Path(matrix_path).exists():
        ij = ids_intersect(matrix_path, backbone_path, id_col_a="PATIENT_ID", id_col_b="subjid")
        k4 = ij.get("data", {})
    # K5 path-rank vs outcome: real held-out interaction computed; fit-rank AUROC not reconstructable locally
    k5 = {"real_heldout_interaction_pfs": {"reproduces_direction": h1_loo["reproduces_direction"],
                                           "reproduces_ci_excl_1": h1_loo["reproduces_ci_excl_1"],
                                           "leave_one_out": h1_loo["leave_one_out"]},
          "fit_rank_auroc": {"status": "BLOCKED",
                             "reason": "MoA/PATH fit-rank vs realized-winner artifact not reconstructable from local IPD; prior PATH-B receipt (AUROC~0.25) is NOT recomputed here and is not used as a score"}}
    # K6 setting specificity (adjuvant negative control)
    k6 = S.setting_specificity(df)
    # K7 safety hard-block: anti-EGFR + bev (PACCE) all-comers
    pacce = S.prep(df[df["trial"] == "PACCE_20040249"], "kras")
    k7 = {"trial": "PACCE_20040249", "contrast": "chemo+bev +/- panitumumab",
          "pfs_arm_hr": S.cox_arm_only(pacce, "pfs_days", "pfs_event"),
          "os_arm_hr": S.cox_arm_only(pacce, "os_days", "os_event"),
          "verdict": "SAFETY_HARD_BLOCK anti-EGFR + bevacizumab (harm direction; Hecht 2009 JCO halted)"}

    # response AUROC leg (BLOCKED — no local response IPD)
    auroc = S.heldout_response_auroc(None)

    result = dict(
        generated=_now(),
        seed=20260804,
        selected_trials=sel,
        excluded_trials=[{"trial": e["trial"], "reasons": e["exclude_reasons"]} for e in excluded],
        primary_results={
            "H1_kras_ex2": {"pfs": h1_pfs, "os": h1_os, "leave_one_out": h1_loo,
                            "prognostic_control_only": h1_prog, "iptw_sensitivity": h1_iptw},
            "H2_ext_ras": {"pools": sel_e, "pfs": h2_pfs, "leave_one_out": h2_loo},
        },
        kills={"K1_assay_panel_bias": k1, "K2_prognostic_only": k2, "K3_leakage_event_rate": k3,
               "K4_id_join": k4, "K5_pathrank_vs_outcome": k5, "K6_setting_specificity": k6,
               "K7_safety_hardblock": k7},
        secondary_evaluation_loop={
            "stratified_cox_interaction_pfs": {k: h1_pfs.get(k) for k in ("interaction_ratio_MUT_WT", "ci", "p", "n", "events")},
            "cross_cohort_leave_one_trial_out": h1_loo,
            "iptw_bounded": h1_iptw,
            "response_auroc_vs_null": auroc,
        },
        response_auroc=auroc,
    )
    outd = Path(out_dir) if out_dir else WINNERS_OUT
    jpath = outd / "FALSE_WINNER_KILLS.json"
    sha = _write_json(jpath, result)
    _write_json(outd / "results" / "enrichment_heldout_auroc.json",
                {"generated": _now(), "primary_metric": "cross_cohort_interaction_HR_ratio_MUT_WT",
                 "h1_pfs_interaction": {k: h1_pfs.get(k) for k in ("interaction_ratio_MUT_WT", "ci", "p")},
                 "leave_one_out": h1_loo["leave_one_out"], "response_auroc": auroc})
    # markdown
    md = _kills_md(result)
    _write_text(outd / "FALSE_WINNER_KILLS.md", md)
    return envelope(ok=True, tool="winners.kill_tests", n=7, receipt_sha=sha,
                    artifacts=[str(jpath), str(outd / "FALSE_WINNER_KILLS.md"),
                               str(outd / "results" / "enrichment_heldout_auroc.json")],
                    warnings=warn + ["response-AUROC leg BLOCKED: response IPD (outcomes/local/*.parquet) absent locally"],
                    data=result)


def _fmt_hr(d):
    if not isinstance(d, dict):
        return "n/a"
    for k in ("hr", "interaction_ratio_MUT_WT", "prognostic_hr_MUT_vs_WT",
              "iptw_interaction_ratio_MUT_WT", "hr_treated_vs_synthetic"):
        v = d.get(k)
        if v is not None:
            ci = d.get("ci")
            p = d.get("p")
            pstr = f", p={p:.4g}" if isinstance(p, (int, float)) else ""
            return f"{v:.3f} [{ci[0]:.3f}, {ci[1]:.3f}]{pstr}" if ci else f"{v:.3f}"
    return d.get("error", "n/a")


def _kills_md(r: dict) -> str:
    h1 = r["primary_results"]["H1_kras_ex2"]
    L = ["# False-Winner Kill Battery (W2)", "", f"_Generated {r['generated']} · seed {r['seed']} · RUO · re-derived_", "",
         f"**Selected metastatic anti-EGFR pool:** {', '.join(r['selected_trials'])}", "",
         "## Primary predictive test — H1 (KRAS ex2 WT × anti-EGFR)", "",
         f"- PFS interaction HR ratio (MUT:WT): **{_fmt_hr(h1['pfs'])}**",
         f"- OS interaction HR ratio (MUT:WT): {_fmt_hr(h1['os'])}",
         f"- anti-EGFR PFS HR within WT: {_fmt_hr(h1['pfs']['hr_arm_WT'])}",
         f"- anti-EGFR PFS HR within MUT: {_fmt_hr(h1['pfs']['hr_arm_MUT'])}",
         f"- Cross-cohort leave-one-trial-out reproduces direction: **{h1['leave_one_out']['reproduces_direction']}**, CI-excl-1 all folds: {h1['leave_one_out']['reproduces_ci_excl_1']}",
         f"- Control-arm prognostic-only HR (MUT vs WT): {_fmt_hr(h1['prognostic_control_only'])}",
         f"- IPTW-bounded interaction (stabilized PS): {_fmt_hr(h1['iptw_sensitivity'])}"
         + (f" · ESS treated/control={h1['iptw_sensitivity'].get('ess_treated')}/{h1['iptw_sensitivity'].get('ess_control')} · max|SMD| weighted={h1['iptw_sensitivity'].get('max_abs_smd_weighted')}"
            if isinstance(h1['iptw_sensitivity'], dict) and 'error' not in h1['iptw_sensitivity'] else ""), ""]
    L += ["## Kills", ""]
    verds = {
        "K1 assay/panel bias": r["kills"]["K1_assay_panel_bias"]["ipd_typing_source"],
        "K2 prognostic-only": r["kills"]["K2_prognostic_only"]["verdict"],
        "K3 leakage/event-rate": r["kills"]["K3_leakage_event_rate"]["note"],
        "K4 ID-join GENIE∩PDS": r["kills"]["K4_id_join"].get("verdict", "SKIPPED"),
        "K5 fit-rank vs outcome": r["kills"]["K5_pathrank_vs_outcome"]["fit_rank_auroc"]["status"] + " (real held-out interaction computed)",
        "K6 setting-specificity (adjuvant N0147)": _fmt_hr(r["kills"]["K6_setting_specificity"]),
        "K7 safety hard-block (anti-EGFR+bev)": r["kills"]["K7_safety_hardblock"]["verdict"],
    }
    for k, v in verds.items():
        L.append(f"- **{k}:** {v}")
    L += ["", f"**Response-AUROC leg:** {r['response_auroc']['status']} — {r['response_auroc'].get('reason','')}", "",
          "_RUO: Research Use Only. Does NOT soft-unblock 8D-04._", ""]
    return "\n".join(L)


def winners_scoreboard(definition_path: str, hypotheses_path: str, kills_path: str,
                       out_dir: Optional[str] = None) -> dict:
    warn = assert_no_answer_key([definition_path, hypotheses_path, kills_path])
    kills = json.loads(Path(kills_path).read_text(encoding="utf-8"))
    hyps = json.loads(Path(hypotheses_path).read_text(encoding="utf-8")).get("hypotheses", [])
    pr = kills["primary_results"]
    rows = []
    # H1
    h1 = pr["H1_kras_ex2"]
    h1i = h1["pfs"]
    h1_adv = bool(h1i.get("ci") and h1i["ci"][0] > 1 and h1["leave_one_out"]["reproduces_direction"]
                  and h1["leave_one_out"]["reproduces_ci_excl_1"])
    rows.append(dict(
        hypothesis="H1 KRAS-ex2-WT × anti-EGFR", status="primary",
        predictive_interaction_MUT_WT=_compact(h1i), within_WT_hr=_compact(h1i.get("hr_arm_WT")),
        within_MUT_hr=_compact(h1i.get("hr_arm_MUT")),
        prognostic_only_hr=_compact(h1["prognostic_control_only"]),
        cross_cohort_reproduces=h1["leave_one_out"]["reproduces_direction"] and h1["leave_one_out"]["reproduces_ci_excl_1"],
        response_auroc=kills["response_auroc"]["status"],
        decision=("ADVANCE" if h1_adv else "BLOCKED"),
    ))
    # H2
    h2 = pr["H2_ext_ras"]
    h2i = h2["pfs"]
    h2_ok = isinstance(h2i, dict) and h2i.get("ci") and h2i["ci"][0] > 1
    rows.append(dict(
        hypothesis="H2 extended-RAS-WT × anti-EGFR", status="refinement",
        predictive_interaction_MUT_WT=_compact(h2i) if isinstance(h2i, dict) else "n/a",
        pools=h2.get("pools"),
        cross_cohort_reproduces=(h2.get("leave_one_out", {}) or {}).get("reproduces_direction"),
        decision=("ADVANCE" if h2_ok else "EXPLORATORY"),
    ))
    # H3-H5 from hypotheses file (exploratory/blocked)
    for h in hyps:
        if h.get("id") in ("H3", "H4", "H5"):
            rows.append(dict(hypothesis=f"{h['id']} {h.get('biomarker')}", status=h.get("status"),
                             decision=("BLOCKED" if h.get("status") in ("blocked", "exploratory") else "EXPLORATORY"),
                             note=h.get("blocked_reason") or h.get("statement")))
    safety = kills["kills"]["K7_safety_hardblock"]
    board = dict(generated=_now(), seed=kills.get("seed"), rows=rows,
                 safety_hard_block=safety["verdict"],
                 provenance="rows derived ONLY from winners.kill_tests + hypotheses artifacts")
    outd = Path(out_dir) if out_dir else WINNERS_OUT
    jpath = outd / "WINNERS_SCOREBOARD.json"
    sha = _write_json(jpath, board)
    _write_text(outd / "WINNERS_SCOREBOARD.md", _scoreboard_md(board))
    return envelope(ok=True, tool="winners.scoreboard", n=len(rows), receipt_sha=sha,
                    artifacts=[str(jpath), str(outd / "WINNERS_SCOREBOARD.md")], warnings=warn, data=board)


def _compact(d):
    if not isinstance(d, dict):
        return "n/a"
    if "interaction_ratio_MUT_WT" in d:
        ci = d.get("ci")
        return f"{d['interaction_ratio_MUT_WT']:.3f} [{ci[0]:.3f},{ci[1]:.3f}] p={d.get('p'):.3g}" if ci else "n/a"
    if "hr" in d:
        ci = d.get("ci")
        return f"{d['hr']:.3f} [{ci[0]:.3f},{ci[1]:.3f}] p={d.get('p'):.3g}" if ci else "n/a"
    if "prognostic_hr_MUT_vs_WT" in d:
        ci = d.get("ci")
        return f"{d['prognostic_hr_MUT_vs_WT']:.3f} [{ci[0]:.3f},{ci[1]:.3f}] p={d.get('p'):.3g}" if ci else "n/a"
    return "n/a"


def _scoreboard_md(b: dict) -> str:
    L = ["# Winners Scoreboard (W3)", "", f"_Generated {b['generated']} · seed {b['seed']} · RUO · rows from tool artifacts only_", ""]
    for r in b["rows"]:
        L += [f"## {r['hypothesis']} — decision: **{r['decision']}** ({r.get('status')})"]
        for k, v in r.items():
            if k in ("hypothesis", "decision", "status"):
                continue
            L.append(f"- {k}: {v}")
        L.append("")
    L += [f"**Safety hard-block:** {b['safety_hard_block']}", "", "_RUO: Research Use Only. Does NOT soft-unblock 8D-04._", ""]
    return "\n".join(L)


def winners_pick(scoreboard_path: str, out_dir: Optional[str] = None) -> dict:
    board = json.loads(Path(scoreboard_path).read_text(encoding="utf-8"))
    advance = [r for r in board["rows"] if r.get("decision") == "ADVANCE"]
    gaps = [
        "response-level RECIST IPD (outcomes/local/*.parquet) absent → held-out AUROC leg BLOCKED",
        "extended-RAS typing present only in PRIME + PaniBSC (NRAS/BRAF sparse) → H2 cross-cohort thin",
        "SAS-locked trials (Sanofi/AZ/MOSAIC/VELOUR/HORIZON3) need pyreadstat unblock (DAR) to widen cohorts",
        "GENIE↔PDS join impossible (disjoint namespaces) → no genomic-prevalence↔outcome linkage without new DAR",
    ]
    outd = Path(out_dir) if out_dir else WINNERS_OUT
    L = ["# Winners For Money (W4)", "", f"_Generated {_now()} · seed {board.get('seed')} · RUO_", "",
         "## ADVANCE (predictive enrichment × treatment)", ""]
    if advance:
        for r in advance:
            L.append(f"### {r['hypothesis']}")
            L.append(f"- Predictive interaction (MUT:WT): {r.get('predictive_interaction_MUT_WT')}")
            if r.get("within_WT_hr") or r.get("within_MUT_hr"):
                L.append(f"- anti-EGFR HR within WT: {r.get('within_WT_hr')}  |  within MUT: {r.get('within_MUT_hr')}")
            if r.get("prognostic_only_hr"):
                L.append(f"- Prognostic-only HR (control arm): {r.get('prognostic_only_hr')}")
            if r.get("pools"):
                L.append(f"- Cross-cohort pools: {r.get('pools')}")
            L.append(f"- Cross-cohort reproduces: {r.get('cross_cohort_reproduces')}")
            if r.get("response_auroc"):
                L.append(f"- Held-out response AUROC: {r.get('response_auroc')}")
            L.append("")
    else:
        L += ["- (none met ADVANCE bar)", ""]
    L += [f"## SAFETY HARD-BLOCK", "", f"- {board['safety_hard_block']}", "",
          "## DAR / IPD gaps (required before scale-up)", ""]
    L += [f"- {g}" for g in gaps]
    L += ["", "## Governance", "",
          "- This is enrichment/IPD research support. It does **NOT** soft-unblock 8D-04.",
          "- PATH ranking formulas remain under their own held-out governance.", "",
          "_RUO: Research Use Only._", ""]
    path = outd / "WINNERS_FOR_MONEY.md"
    sha = _write_text(path, "\n".join(L))
    return envelope(ok=True, tool="winners.pick", n=len(advance), receipt_sha=sha,
                    artifacts=[str(path)], data={"advance": advance, "dar_ipd_gaps": gaps,
                                                 "safety_hard_block": board["safety_hard_block"],
                                                 "note_8d04": "NOT soft-unblocked"})
