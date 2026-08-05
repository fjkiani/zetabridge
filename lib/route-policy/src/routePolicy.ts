/**
 * routePolicy.ts
 * Pure, deterministic anti-EGFR efficacy scoring policy for CRC treatment routing.
 *
 * This closes design-doc rows 14/18/19 (SPECIFIED_NOT_PUSHED): it propagates into
 * TypeScript the same setting/RAS/regimen logic the Python STC1010GatingEngine
 * already applies (adjuvant Gate-1 fail; anti-EGFR+bev hard block; RAS selection
 * axis). It has ZERO runtime dependencies.
 *
 * KEY INTEGRITY POINT (Directive 2): the adjuvant downgrade to 0.0/POOR is anchored
 * on the DIRECT N0147/Alliance-161 no-benefit / numeric-harm finding (KRAS-WT DFS
 * HR 1.18 [0.958-1.454]; Alberts JAMA 2012 KRAS-WT DFS HR 1.21 [0.98-1.49], P=.08),
 * NOT on "the treatment x RAS interaction was non-significant". A null interaction
 * alone does not imply zero efficacy; the direct setting-specific evidence does.
 *
 * RUO - research use only. Evidence-anchored routing logic, not medical advice.
 */

export type TreatmentSetting =
  | "METASTATIC"
  | "ADJUVANT"
  | "STAGE_III"
  | "RESECTED"
  | "NEO_ADJUVANT"
  | "UNKNOWN";

export type RasStatus = "WT" | "MUT" | "UNKNOWN";

export type EfficacyTier =
  | "HARD_BLOCK"
  | "POOR"
  | "MODERATE"
  | "STRONG"
  | "INDETERMINATE"
  | "NOT_APPLICABLE";

export interface PatientProfile {
  setting: TreatmentSetting;
  ras: RasStatus;
  antiEgfr: boolean;
  bev: boolean;
  braf?: string;
  /** single-trial confidence in [0,1]; absent or < confThreshold => pooled fallback */
  confidence?: number;
}

export interface EvidenceRef {
  source: string;
  metric: string;
  value: string;
  doi?: string;
  note?: string;
}

export interface EfficacyResult {
  /** [0,1] efficacy score = max(0, 1 - selection-axis HR); 0.0 = no efficacy */
  score: number;
  tier: EfficacyTier;
  reasonCodes: string[];
  usedPooledFallback: boolean;
  hrSelectionAxis: number | null;
  evidence: EvidenceRef[];
  policyVersion: string;
}

export const ROUTE_POLICY_VERSION = "1.0.0";

/**
 * Published / PDS-derived evidence anchors.
 * MUST stay identical to emit_model_router_payload.py (cross-language concordance).
 */
export const ANCHORS = {
  selectionHrRasWt: 0.732, // metastatic anti-EGFR RAS-WT PFS HR (0.648-0.827)
  selectionHrRasMut: 1.046, // metastatic anti-EGFR RAS-MUT PFS HR (0.912-1.20)
  interactionHrRatio: 1.43, // RAS-WT vs RAS-MUT HR-ratio (1.19-1.719) p=0.00014
  adjuvantKrasWtDfsHr: 1.18, // N0147/Alliance-161 adjuvant KRAS-WT DFS HR (0.958-1.454)
  albertsKrasWtDfsHr: 1.21, // Alberts JAMA 2012 adjuvant KRAS-WT DFS HR (0.98-1.49) P=.08
  pacceePfsHr: 1.27, // PACCE anti-EGFR+bev PFS HR (1.06-1.52)
  pacceOsHr: 1.43, // PACCE anti-EGFR+bev OS HR (1.11-1.83)
  confThreshold: 0.85,
  strongThreshold: 0.2,
} as const;

const NON_METASTATIC: ReadonlySet<TreatmentSetting> = new Set<TreatmentSetting>([
  "ADJUVANT",
  "STAGE_III",
  "RESECTED",
  "NEO_ADJUVANT",
]);

/** efficacy score = 1 - HR, floored at 0, rounded to 3 dp (deterministic). */
export function benefitScore(hr: number): number {
  return Math.max(0, Math.round((1 - hr) * 1000) / 1000);
}

function tierFromScore(score: number): EfficacyTier {
  if (score >= ANCHORS.strongThreshold) return "STRONG";
  if (score > 0) return "MODERATE";
  return "POOR";
}

/**
 * Score anti-EGFR efficacy for a CRC profile. Pure and deterministic:
 * identical input always yields a deep-equal result.
 */
export function scoreAntiEgfrEfficacy(profile: PatientProfile): EfficacyResult {
  const reasonCodes: string[] = [];
  const evidence: EvidenceRef[] = [];

  // conf gate: absent OR below threshold => pooled meta-analytic anchors.
  const usedPooledFallback =
    profile.confidence === undefined || profile.confidence < ANCHORS.confThreshold;
  if (usedPooledFallback) {
    reasonCodes.push("POOLED_FALLBACK");
    evidence.push({
      source: "pooled meta-analytic anti-EGFR RAS axis",
      metric: "RAS-WT PFS HR / interaction HR-ratio",
      value: `${ANCHORS.selectionHrRasWt} / ${ANCHORS.interactionHrRatio}`,
      note: "single-trial confidence absent or < 0.85 -> defaulting to pooled estimate",
    });
  }

  const base = (over: Partial<EfficacyResult>): EfficacyResult => ({
    score: 0,
    tier: "INDETERMINATE",
    reasonCodes,
    usedPooledFallback,
    hrSelectionAxis: null,
    evidence,
    policyVersion: ROUTE_POLICY_VERSION,
    ...over,
  });

  // 0) anti-EGFR not in the regimen -> policy is not applicable.
  if (!profile.antiEgfr) {
    reasonCodes.push("ANTI_EGFR_NOT_IN_REGIMEN");
    return base({ tier: "NOT_APPLICABLE" });
  }

  // 1) anti-EGFR + bevacizumab -> hard contraindication (highest precedence).
  if (profile.bev) {
    reasonCodes.push("ANTI_EGFR_PLUS_BEV_HARD_BLOCK");
    evidence.push({
      source: "PACCE (Hecht JCO 2009)",
      metric: "PFS HR / OS HR (panitumumab + bev vs bev)",
      value: `${ANCHORS.pacceePfsHr} [1.06-1.52] / ${ANCHORS.pacceOsHr} [1.11-1.83]`,
      doi: "10.1200/JCO.2008.19.8135",
      note: "adding anti-EGFR to bev worsens PFS and OS -> regimen contraindicated",
    });
    return base({ tier: "HARD_BLOCK", score: 0 });
  }

  // 2) adjuvant / non-metastatic setting -> DIRECT no-benefit; downgrade to 0.0/POOR.
  if (NON_METASTATIC.has(profile.setting)) {
    reasonCodes.push("ADJUVANT_ANTI_EGFR_NO_BENEFIT");
    evidence.push({
      source: "N0147 / Alliance-161 (NCT00079274)",
      metric: "adjuvant KRAS-WT DFS HR (FOLFOX + cetuximab vs FOLFOX)",
      value: `${ANCHORS.adjuvantKrasWtDfsHr} [0.958-1.454]`,
      note:
        "DIRECT adjuvant no-benefit / numeric harm even in KRAS-WT -- setting reversal " +
        "vs metastatic RAS-WT benefit. Downgrade anchored on this DIRECT finding, " +
        "NOT on the null treatment x RAS interaction.",
    });
    evidence.push({
      source: "Alberts JAMA 2012;307(13):1383-93",
      metric: "adjuvant KRAS-WT DFS HR",
      value: `${ANCHORS.albertsKrasWtDfsHr} [0.98-1.49], P=.08`,
      doi: "10.1001/jama.2012.385",
      note: "concordant published cross-cohort confirmation of adjuvant no-benefit",
    });
    return base({ tier: "POOR", score: 0, hrSelectionAxis: ANCHORS.adjuvantKrasWtDfsHr });
  }

  if (profile.setting === "UNKNOWN") {
    reasonCodes.push("SETTING_ASSUMED_METASTATIC");
  }

  // 3) metastatic selection axis by RAS status.
  if (profile.ras === "WT") {
    const score = benefitScore(ANCHORS.selectionHrRasWt);
    reasonCodes.push("METASTATIC_RAS_WT_ANTI_EGFR_BENEFIT");
    evidence.push({
      source: "metastatic anti-EGFR RAS selection axis (pooled)",
      metric: "RAS-WT PFS HR",
      value: `${ANCHORS.selectionHrRasWt} [0.648-0.827]`,
      note: "RAS-WT metastatic patients benefit from anti-EGFR (no bev)",
    });
    return base({ tier: tierFromScore(score), score, hrSelectionAxis: ANCHORS.selectionHrRasWt });
  }

  if (profile.ras === "MUT") {
    const score = benefitScore(ANCHORS.selectionHrRasMut);
    reasonCodes.push("METASTATIC_RAS_MUT_NO_BENEFIT");
    evidence.push({
      source: "metastatic anti-EGFR RAS selection axis (pooled)",
      metric: "RAS-MUT PFS HR / interaction HR-ratio",
      value: `${ANCHORS.selectionHrRasMut} [0.912-1.20] / ${ANCHORS.interactionHrRatio} [1.19-1.719]`,
      note: "RAS-mutant metastatic patients derive no benefit (HR ~1) from anti-EGFR",
    });
    return base({ tier: "POOR", score, hrSelectionAxis: ANCHORS.selectionHrRasMut });
  }

  // 4) RAS unknown -> cannot apply the selection axis; require testing.
  reasonCodes.push("RAS_STATUS_REQUIRED");
  evidence.push({
    source: "metastatic anti-EGFR RAS selection axis",
    metric: "RAS status",
    value: "unknown",
    note: "RAS genotype required before anti-EGFR selection can be scored",
  });
  return base({ tier: "INDETERMINATE", score: 0 });
}
