/**
 * modelRouter.ts
 * Wires routePolicy into a routing decision + provenance. Pure, deterministic,
 * zero runtime dependencies.
 *
 * RUO - research use only.
 */
import {
  scoreAntiEgfrEfficacy,
  ROUTE_POLICY_VERSION,
  type EfficacyResult,
  type EfficacyTier,
  type PatientProfile,
} from "./routePolicy.js";

export type RouteAction =
  | "BLOCK_REGIMEN"
  | "DOWNGRADE_ANTI_EGFR_ADJUVANT"
  | "PREFER_ANTI_EGFR"
  | "AVOID_ANTI_EGFR_RAS_MUT"
  | "REQUIRE_RAS_TESTING"
  | "ANTI_EGFR_NOT_IN_REGIMEN"
  | "REVIEW";

export interface RoutingDecision {
  action: RouteAction;
  efficacyScore: number;
  tier: EfficacyTier;
  reasonCodes: string[];
  usedPooledFallback: boolean;
  efficacy: EfficacyResult;
  provenance: {
    policyVersion: string;
    engineRef: string;
    formulaCanon: string;
  };
}

function actionForTier(tier: EfficacyTier, reasonCodes: string[]): RouteAction {
  if (tier === "HARD_BLOCK") return "BLOCK_REGIMEN";
  if (reasonCodes.includes("ADJUVANT_ANTI_EGFR_NO_BENEFIT")) {
    return "DOWNGRADE_ANTI_EGFR_ADJUVANT";
  }
  if (tier === "NOT_APPLICABLE") return "ANTI_EGFR_NOT_IN_REGIMEN";
  if (tier === "STRONG") return "PREFER_ANTI_EGFR";
  if (reasonCodes.includes("METASTATIC_RAS_MUT_NO_BENEFIT")) {
    return "AVOID_ANTI_EGFR_RAS_MUT";
  }
  if (reasonCodes.includes("RAS_STATUS_REQUIRED")) return "REQUIRE_RAS_TESTING";
  return "REVIEW";
}

/** Route a CRC profile: score anti-EGFR efficacy, then map to a routing action. */
export function routeModel(profile: PatientProfile): RoutingDecision {
  const efficacy = scoreAntiEgfrEfficacy(profile);
  const action = actionForTier(efficacy.tier, efficacy.reasonCodes);
  return {
    action,
    efficacyScore: efficacy.score,
    tier: efficacy.tier,
    reasonCodes: efficacy.reasonCodes,
    usedPooledFallback: efficacy.usedPooledFallback,
    efficacy,
    provenance: {
      policyVersion: ROUTE_POLICY_VERSION,
      engineRef: "STC1010GatingEngine",
      formulaCanon: "PATH A",
    },
  };
}
