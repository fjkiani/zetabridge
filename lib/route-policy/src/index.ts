/**
 * @workspace/route-policy
 * Evidence-anchored anti-EGFR efficacy scoring + model routing for CRC.
 * RUO - research use only.
 */
export {
  scoreAntiEgfrEfficacy,
  benefitScore,
  ANCHORS,
  ROUTE_POLICY_VERSION,
} from "./routePolicy.js";
export type {
  TreatmentSetting,
  RasStatus,
  EfficacyTier,
  PatientProfile,
  EvidenceRef,
  EfficacyResult,
} from "./routePolicy.js";
export { routeModel } from "./modelRouter.js";
export type { RouteAction, RoutingDecision } from "./modelRouter.js";
