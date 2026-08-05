"""Winners analysis engine — RE-DERIVED numbers only.

Pure functions on the harmonized PDS IPD backbone (crc_ipd_features_backbone_v5.csv).
This module NEVER reads answer-key files (crc_backbone_source_of_truth.json,
GENIE_HARD_AGENT_BRIEF.md); every effect size is recomputed from the IPD.

Coding conventions
------------------
- arm      = anti_egfr (1 = anti-EGFR-containing arm, 0 = control backbone)
- ras_mut  = 1 if marker == MUT, 0 if WT, NaN otherwise (row dropped from model)
- interaction term arm:ras_mut => log(HR_ratio MUT:WT); exp() > 1 means anti-EGFR
  works WORSE in mutant (benefit confined to WT) => predictive enrichment.
"""
from __future__ import annotations

import math
from typing import Any, Optional

WT = "WT"
MUT = "MUT"

# External published enrollment (literature priors, for deposit-attenuation kill only;
# NOT used as effect-size answers). Citations live in the run report.
PUBLISHED_ENROLLMENT = {
    "20050181_pmab_FOLFIRI_2L": {"n_pub": 1186, "ref": "Peeters 2010 JCO (2L mCRC)"},
    "PRIME_20050203": {"n_pub": 1183, "ref": "Douillard 2013 NEJM (1L mCRC)"},
    "PaniBSC_20020408": {"n_pub": 463, "ref": "Van Cutsem 2007 JCO (chemo-refractory mCRC)"},
    "PACCE_20040249": {"n_pub": 1053, "ref": "Hecht 2009 JCO (bev +/- pmab; halted)"},
    "N0147_20040161": {"n_pub": 2686, "ref": "Alberts 2012 JAMA (adjuvant stage III)"},
}


# ─────────────────────────── loaders / prep ────────────────────────────────


def load_backbone(path: str):
    import pandas as pd

    return pd.read_csv(path)


def prep(df, marker_col: str = "kras"):
    """Add numeric arm + ras_mut (from marker_col) columns. Non-destructive."""
    import numpy as np
    import pandas as pd

    d = df.copy()
    d["arm"] = pd.to_numeric(d["anti_egfr"], errors="coerce")
    m = d[marker_col].astype(str).str.upper().str.strip()
    d["ras_mut"] = np.where(m.eq(MUT), 1.0, np.where(m.eq(WT), 0.0, np.nan))
    return d


def trial_structure(df):
    """Design metadata per trial (NO outcome cross-tabs) — feasibility, not scoring."""
    import numpy as np
    import pandas as pd

    rows = []
    for tr, g in df.groupby("trial"):
        arm = pd.to_numeric(g["anti_egfr"], errors="coerce")
        bev = pd.to_numeric(g["bev"], errors="coerce")
        kras_typed = g["kras"].astype(str).str.upper().isin([WT, MUT]).sum()
        ras_ext_typed = g["ras_extended"].astype(str).str.upper().isin([WT, MUT]).sum()
        line_vals = sorted(set(g["line"].dropna().astype(str)))
        rows.append(
            dict(
                trial=str(tr),
                n=int(len(g)),
                arms=sorted(set(arm.dropna().astype(int).tolist())),
                both_arms=bool(arm.dropna().nunique() >= 2),
                bev_max=(None if bev.dropna().empty else int(bev.max())),
                kras_typed=int(kras_typed),
                ras_ext_typed=int(ras_ext_typed),
                lines=line_vals,
                pfs_events=int(pd.to_numeric(g["pfs_event"], errors="coerce").fillna(0).sum()),
                os_events=int(pd.to_numeric(g["os_event"], errors="coerce").fillna(0).sum()),
            )
        )
    return rows


def _is_adjuvant(line_vals: list[str]) -> bool:
    return any("adj" in str(v).lower() for v in line_vals)


def select_h1_trials(df):
    """Metastatic anti-EGFR EFFICACY pool: both arms, KRAS-typed, NO bev, NOT adjuvant.
    Returns (selected_trials, structure_rows, excluded_with_reason)."""
    rows = trial_structure(df)
    selected, excluded = [], []
    for r in rows:
        reasons = []
        if not r["both_arms"]:
            reasons.append("single_arm")
        if r["kras_typed"] == 0:
            reasons.append("no_kras_typing")
        if r["bev_max"] == 1:
            reasons.append("bev_containing_reserved_for_safety_kill")
        if _is_adjuvant(r["lines"]):
            reasons.append("adjuvant_setting_reserved_for_setting_specificity_kill")
        if reasons:
            excluded.append({**r, "exclude_reasons": reasons})
        else:
            selected.append(r["trial"])
    return selected, rows, excluded


# ─────────────────────────── Cox engines ───────────────────────────────────


def _fit_cox(dd, T, E, strata):
    from lifelines import CoxPHFitter

    warn = None
    for pen in (0.0, 0.1):
        try:
            cph = CoxPHFitter(penalizer=pen)
            cph.fit(dd, T, E, strata=strata, robust=False)
            if pen > 0:
                warn = f"used penalizer={pen} for convergence"
            return cph, warn
        except Exception as e:  # noqa: BLE001
            last = str(e)
    raise RuntimeError(f"cox_fit_failed: {last}")


def cox_arm_only(sub, T, E):
    """anti-EGFR HR within a single marker stratum (subset), Cox strat by trial."""
    d = sub[[T, E, "arm", "trial"]].dropna()
    if len(d) == 0 or d["arm"].nunique() < 2 or d[E].sum() == 0:
        return {"error": "insufficient_data", "n": int(len(d)), "events": int(d[E].sum()) if len(d) else 0}
    strata = ["trial"] if d["trial"].nunique() > 1 else None
    dd = d if strata else d.drop(columns=["trial"])
    cph, warn = _fit_cox(dd, T, E, strata)
    s = cph.summary.loc["arm"]
    out = dict(
        hr=float(math.exp(s["coef"])),
        ci=[float(math.exp(s["coef"] - 1.96 * s["se(coef)"])), float(math.exp(s["coef"] + 1.96 * s["se(coef)"]))],
        p=float(s["p"]),
        n=int(len(dd)),
        events=int(dd[E].sum()),
    )
    if warn:
        out["warning"] = warn
    return out


def cox_interaction(frame, T="pfs_days", E="pfs_event", marker="ras_mut"):
    """arm x marker interaction (predictive test). Returns HR ratio MUT:WT + within-arm HRs."""
    d = frame.copy()
    d["armXmut"] = d["arm"] * d[marker]
    cols = [T, E, "trial", "arm", marker, "armXmut"]
    d = d[cols].dropna()
    if d["armXmut"].nunique() < 2 or d[E].sum() == 0:
        return {"error": "insufficient_data_for_interaction", "n": int(len(d)), "events": int(d[E].sum()) if len(d) else 0}
    strata = ["trial"] if d["trial"].nunique() > 1 else None
    dd = d if strata else d.drop(columns=["trial"])
    cph, warn = _fit_cox(dd, T, E, strata)
    s = cph.summary.loc["armXmut"]
    coef, se = float(s["coef"]), float(s["se(coef)"])
    out = dict(
        interaction_ratio_MUT_WT=float(math.exp(coef)),
        ci=[float(math.exp(coef - 1.96 * se)), float(math.exp(coef + 1.96 * se))],
        p=float(s["p"]),
        n=int(len(dd)),
        events=int(dd[E].sum()),
        n_trials=int(d["trial"].nunique()),
        hr_arm_WT=cox_arm_only(frame[frame[marker] == 0], T, E),
        hr_arm_MUT=cox_arm_only(frame[frame[marker] == 1], T, E),
    )
    if warn:
        out["warning"] = warn
    return out


def leave_one_trial_out(frame, T="pfs_days", E="pfs_event", marker="ras_mut"):
    """Cross-cohort reproduction: drop each trial, refit pooled interaction; + per-trial."""
    trials = sorted(frame["trial"].unique())
    loo, per = [], []
    for t in trials:
        r = cox_interaction(frame[frame["trial"] != t], T, E, marker)
        loo.append({"held_out": t, **{k: r.get(k) for k in ("interaction_ratio_MUT_WT", "ci", "p", "n", "events")}})
    for t in trials:
        r = cox_interaction(frame[frame["trial"] == t], T, E, marker)
        per.append({"trial": t, **{k: r.get(k) for k in ("interaction_ratio_MUT_WT", "ci", "p", "n", "events", "error")}})
    # reproduction verdict: every LOO fold same direction (>1) AND CI excludes 1
    same_dir = all((f.get("interaction_ratio_MUT_WT") or 0) > 1 for f in loo if f.get("interaction_ratio_MUT_WT"))
    ci_excl = all((f.get("ci") and f["ci"][0] > 1) for f in loo if f.get("ci"))
    return {"leave_one_out": loo, "per_trial": per, "reproduces_direction": bool(same_dir), "reproduces_ci_excl_1": bool(ci_excl)}


def prognostic_control_only(frame, T="pfs_days", E="pfs_event", marker="ras_mut"):
    """Is the marker merely PROGNOSTIC? RAS effect in the CONTROL arm only (arm==0)."""
    ctrl = frame[frame["arm"] == 0]
    d = ctrl[[T, E, "trial", marker]].dropna()
    if d[marker].nunique() < 2 or d[E].sum() == 0:
        return {"error": "insufficient_control_arm_data", "n": int(len(d))}
    strata = ["trial"] if d["trial"].nunique() > 1 else None
    dd = d if strata else d.drop(columns=["trial"])
    cph, warn = _fit_cox(dd, T, E, strata)
    s = cph.summary.loc[marker]
    return dict(
        prognostic_hr_MUT_vs_WT=float(math.exp(s["coef"])),
        ci=[float(math.exp(s["coef"] - 1.96 * s["se(coef)"])), float(math.exp(s["coef"] + 1.96 * s["se(coef)"]))],
        p=float(s["p"]),
        n=int(len(dd)),
        events=int(dd[E].sum()),
        note="predictive requires arm x marker interaction beyond this control-arm prognostic effect",
    )


# ─────────────────────────── kill helpers ──────────────────────────────────


def event_rate_by_trial(df):
    import pandas as pd

    rows = []
    for tr, g in df.groupby("trial"):
        n = len(g)
        pe = pd.to_numeric(g["pfs_event"], errors="coerce")
        oe = pd.to_numeric(g["os_event"], errors="coerce")
        rows.append(
            dict(
                trial=str(tr),
                n=int(n),
                pfs_event_rate=(None if pe.notna().sum() == 0 else round(float(pe.mean()), 3)),
                os_event_rate=(None if oe.notna().sum() == 0 else round(float(oe.mean()), 3)),
                lines=sorted(set(g["line"].dropna().astype(str))),
            )
        )
    return rows


def deposit_attenuation(df, selected_trials):
    """Deposit n vs published enrollment => power attenuation flag (not directional flip)."""
    import pandas as pd

    out = []
    for tr in selected_trials:
        g = df[df["trial"] == tr]
        pub = PUBLISHED_ENROLLMENT.get(tr, {})
        n_dep = int(len(g))
        n_pub = pub.get("n_pub")
        out.append(
            dict(
                trial=tr,
                n_deposit=n_dep,
                n_published=n_pub,
                deposit_fraction=(round(n_dep / n_pub, 3) if n_pub else None),
                ref=pub.get("ref"),
            )
        )
    return out


def setting_specificity(df, adjuvant_trial="N0147_20040161", T="pfs_days", E="pfs_event"):
    """Negative-control kill: does RASxantiEGFR interaction hold in the ADJUVANT setting?
    Published expectation (Alberts 2012): adjuvant cetuximab adds NO benefit even in KRAS-WT."""
    g = prep(df[df["trial"] == adjuvant_trial], "kras")
    if len(g) == 0:
        return {"error": "adjuvant_trial_absent", "trial": adjuvant_trial}
    r = cox_interaction(g, T, E)
    return {"trial": adjuvant_trial, "setting": "adjuvant_stage_III", **r}


# ─────────────────────────── PSM / IPTW ────────────────────────────────────


def _sex_to_num(series):
    """Robust sex -> {Male/M/1:1, Female/F/0:0}; handles the v5 backbone 'Male'/'Female' coding."""
    import pandas as pd

    s = series.astype(str).str.strip().str.lower()
    return pd.to_numeric(
        s.map({"male": 1, "m": 1, "1": 1, "1.0": 1, "female": 0, "f": 0, "0": 0, "0.0": 0}),
        errors="coerce",
    )


def _smd(x, w, t):
    """Standardized mean difference of covariate x between treated(t==1)/control, weights w."""
    import numpy as np

    x = np.asarray(x, float)
    t = np.asarray(t, float)
    w = np.asarray(w, float)
    m1 = np.average(x[t == 1], weights=w[t == 1])
    m0 = np.average(x[t == 0], weights=w[t == 0])
    v1 = np.average((x[t == 1] - m1) ** 2, weights=w[t == 1])
    v0 = np.average((x[t == 0] - m0) ** 2, weights=w[t == 0])
    sd = math.sqrt((v1 + v0) / 2) if (v1 + v0) > 0 else float("nan")
    return float((m1 - m0) / sd) if sd and not math.isnan(sd) else float("nan")


def iptw_sensitivity(frame, covariates=("age", "sex_num", "ecog_num"), T="pfs_days", E="pfs_event", marker="ras_mut"):
    """Stabilized IPTW sensitivity for the arm x marker interaction => bound uncertainty.
    Reports ESS + covariate SMD (unweighted vs weighted) + IPTW-weighted interaction HR."""
    import numpy as np
    import pandas as pd
    from lifelines import CoxPHFitter
    from sklearn.linear_model import LogisticRegression

    d = frame.copy()
    d["sex_num"] = _sex_to_num(d["sex"])
    d["ecog_num"] = pd.to_numeric(d["ecog"], errors="coerce")
    if "age" in d.columns:
        d["age"] = pd.to_numeric(d["age"], errors="coerce")
    covs = [c for c in covariates if c in d.columns]
    need = [T, E, "trial", "arm", marker] + covs
    d = d[need].dropna()
    if len(d) < 40 or d["arm"].nunique() < 2:
        return {"error": "insufficient_data_for_iptw", "n": int(len(d))}
    X = d[covs].to_numpy(float)
    a = d["arm"].to_numpy(float)
    ps = LogisticRegression(max_iter=1000).fit(X, a).predict_proba(X)[:, 1]
    ps = np.clip(ps, 0.02, 0.98)
    p_treat = a.mean()
    sw = np.where(a == 1, p_treat / ps, (1 - p_treat) / (1 - ps))  # stabilized
    ess_t = (sw[a == 1].sum() ** 2) / (sw[a == 1] ** 2).sum()
    ess_c = (sw[a == 0].sum() ** 2) / (sw[a == 0] ** 2).sum()
    smd_before = {c: round(_smd(d[c], np.ones(len(d)), a), 3) for c in covs}
    smd_after = {c: round(_smd(d[c], sw, a), 3) for c in covs}
    dd = d[[T, E, "trial", "arm", marker]].copy()
    dd["armXmut"] = dd["arm"] * dd[marker]
    dd["w"] = sw
    strata = ["trial"] if dd["trial"].nunique() > 1 else None
    cph = CoxPHFitter()
    fitcols = [T, E, "arm", marker, "armXmut", "w"] + (["trial"] if strata else [])
    cph.fit(dd[fitcols], T, E, strata=strata, weights_col="w", robust=True)
    s = cph.summary.loc["armXmut"]
    return dict(
        method="stabilized_IPTW_logistic_PS",
        covariates=covs,
        n=int(len(d)),
        ess_treated=round(float(ess_t), 1),
        ess_control=round(float(ess_c), 1),
        smd_unweighted=smd_before,
        smd_weighted=smd_after,
        max_abs_smd_weighted=round(float(max(abs(v) for v in smd_after.values())), 3),
        iptw_interaction_ratio_MUT_WT=float(math.exp(s["coef"])),
        ci=[float(math.exp(s["coef"] - 1.96 * s["se(coef)"])), float(math.exp(s["coef"] + 1.96 * s["se(coef)"]))],
        p=float(s["p"]),
    )


def build_synthetic_control(df, treated_trial, control_pool_trials, covariates=("age", "sex_num", "ecog_num")):
    """PSM synthetic control for a control-less/single-arm treated cohort, drawn from a pooled
    external control arm. Returns matched HR + balance. Used only when a single-arm slice exists."""
    import numpy as np
    import pandas as pd
    from lifelines import CoxPHFitter
    from sklearn.linear_model import LogisticRegression

    d = prep(df, "kras")
    d["sex_num"] = _sex_to_num(d["sex"])
    d["ecog_num"] = pd.to_numeric(d["ecog"], errors="coerce")
    if "age" in d.columns:
        d["age"] = pd.to_numeric(d["age"], errors="coerce")
    treated = d[(d["trial"] == treated_trial) & (d["arm"] == 1)].copy()
    control = d[d["trial"].isin(control_pool_trials) & (d["arm"] == 0)].copy()
    covs = [c for c in covariates if c in d.columns]
    treated["_t"] = 1
    control["_t"] = 0
    pool = pd.concat([treated, control], ignore_index=True)
    need = ["pfs_days", "pfs_event", "_t"] + covs
    pool = pool[need + ["arm"]].dropna(subset=covs)
    if pool["_t"].sum() < 20 or (pool["_t"] == 0).sum() < 20:
        return {"error": "insufficient_for_synthetic_control", "n_treated": int(pool["_t"].sum())}
    X = pool[covs].to_numpy(float)
    t = pool["_t"].to_numpy(float)
    ps = LogisticRegression(max_iter=1000).fit(X, t).predict_proba(X)[:, 1]
    ps = np.clip(ps, 0.02, 0.98)
    w = np.where(t == 1, 1.0, ps / (1 - ps))  # ATT weights for synthetic control
    smd_after = {c: round(_smd(pool[c], w, t), 3) for c in covs}
    ess_c = (w[t == 0].sum() ** 2) / (w[t == 0] ** 2).sum()
    sub = pool[["pfs_days", "pfs_event"]].copy()
    sub["treated"] = t
    sub["w"] = w
    cph = CoxPHFitter()
    cph.fit(sub, "pfs_days", "pfs_event", weights_col="w", robust=True)
    s = cph.summary.loc["treated"]
    return dict(
        method="PSM_ATT_synthetic_control",
        treated_trial=treated_trial,
        control_pool=list(control_pool_trials),
        n_treated=int(t.sum()),
        n_control_pool=int((t == 0).sum()),
        ess_synthetic_control=round(float(ess_c), 1),
        smd_weighted=smd_after,
        hr_treated_vs_synthetic=float(math.exp(s["coef"])),
        ci=[float(math.exp(s["coef"] - 1.96 * s["se(coef)"])), float(math.exp(s["coef"] + 1.96 * s["se(coef)"]))],
        p=float(s["p"]),
    )


def heldout_response_auroc(labels_df):
    """Held-out AUROC of RAS predicting anti-EGFR RECIST response vs label-permuted NULL.
    labels_df must contain response_binary + ras_mut + trial. If None => BLOCKED (no response IPD)."""
    if labels_df is None:
        return {"status": "BLOCKED", "reason": "response_IPD_absent_locally (outcomes/local/*.parquet not in repo)"}
    import numpy as np
    from sklearn.metrics import roc_auc_score

    df = labels_df.dropna(subset=["response_binary", "ras_mut"])
    if df["response_binary"].nunique() < 2:
        return {"status": "BLOCKED", "reason": "single_class_response"}
    # RAS-WT predicts response => score = 1-ras_mut
    score = 1 - df["ras_mut"].to_numpy(float)
    y = df["response_binary"].to_numpy(int)
    obs = float(roc_auc_score(y, score))
    rng = np.random.default_rng(20260804)
    null = [roc_auc_score(y, rng.permutation(score)) for _ in range(2000)]
    upper = float(np.quantile(null, 0.975))
    return {"status": "OK", "auroc_observed": obs, "null_auroc_upper95": upper, "beats_null": bool(obs > upper), "n": int(len(df))}
