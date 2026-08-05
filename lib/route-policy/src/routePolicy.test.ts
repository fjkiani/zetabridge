/**
 * routePolicy.test.ts
 * RUO - research use only; not for clinical care.
 * node:test + node:assert (zero new deps). Two layers:
 *  (1) CROSS-LANGUAGE CONCORDANCE: the TS router must reproduce every expected
 *      decision in modelRouter.payload.json (emitted independently by Python).
 *  (2) TS-only invariants: determinism, bev precedence, adjuvant downgrade across
 *      BOTH RAS strata, and that the adjuvant downgrade is anchored on the DIRECT
 *      N0147/Alberts finding (not on the null interaction).
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { routeModel } from "./modelRouter.js";
import { scoreAntiEgfrEfficacy, type PatientProfile } from "./routePolicy.js";

interface PayloadCase {
  name: string;
  profile: PatientProfile;
  expected: {
    action: string;
    efficacyScore: number;
    tier: string;
    usedPooledFallback: boolean;
    requiredReasonCodes: string[];
  };
}
interface Payload {
  policyVersion: string;
  anchors: Record<string, number>;
  cases: PayloadCase[];
}

const payload: Payload = JSON.parse(
  readFileSync(new URL("../modelRouter.payload.json", import.meta.url), "utf8"),
);

// (1) cross-language concordance -----------------------------------------------
test("TS router reproduces every Python-emitted expected decision", () => {
  assert.ok(payload.cases.length >= 9, "expected >= 9 concordance cases");
  for (const c of payload.cases) {
    const d = routeModel(c.profile);
    assert.equal(d.action, c.expected.action, `${c.name}: action`);
    assert.equal(d.efficacyScore, c.expected.efficacyScore, `${c.name}: score`);
    assert.equal(d.tier, c.expected.tier, `${c.name}: tier`);
    assert.equal(d.usedPooledFallback, c.expected.usedPooledFallback, `${c.name}: pooled`);
    for (const rc of c.expected.requiredReasonCodes) {
      assert.ok(d.reasonCodes.includes(rc), `${c.name}: missing reasonCode ${rc}`);
    }
  }
});

test("payload anchors match compiled ANCHORS (no drift)", () => {
  assert.equal(payload.anchors.selectionHrRasWt, 0.732);
  assert.equal(payload.anchors.adjuvantKrasWtDfsHr, 1.18);
  assert.equal(payload.policyVersion, "1.0.0");
});

// (2) TS-only invariants -------------------------------------------------------
const adjuvant = (ras: "WT" | "MUT"): PatientProfile => ({
  setting: "ADJUVANT",
  ras,
  antiEgfr: true,
  bev: false,
});

test("adjuvant anti-EGFR -> 0.0 / POOR across BOTH RAS strata (secondary loop)", () => {
  for (const ras of ["WT", "MUT"] as const) {
    const r = scoreAntiEgfrEfficacy(adjuvant(ras));
    assert.equal(r.score, 0.0, `${ras}: score must be 0.0`);
    assert.equal(r.tier, "POOR", `${ras}: tier must be POOR`);
    assert.equal(routeModel(adjuvant(ras)).action, "DOWNGRADE_ANTI_EGFR_ADJUVANT");
  }
});

test("adjuvant downgrade is anchored on the DIRECT N0147 finding, not the null interaction", () => {
  const r = scoreAntiEgfrEfficacy(adjuvant("WT"));
  const n0147 = r.evidence.find((e) => e.source.includes("N0147"));
  assert.ok(n0147, "must cite N0147/Alliance-161 directly");
  assert.match(n0147.note ?? "", /DIRECT/);
  assert.match(n0147.note ?? "", /NOT on the null/);
  assert.ok(
    r.evidence.some((e) => e.source.includes("Alberts")),
    "must cite the concordant Alberts JAMA 2012 cross-cohort confirmation",
  );
  // no reason code should assert efficacy is zero *because* the interaction is null
  assert.ok(!r.reasonCodes.some((c) => /INTERACTION_NULL/i.test(c)));
});

test("STAGE_III and RESECTED behave like ADJUVANT", () => {
  for (const setting of ["STAGE_III", "RESECTED", "NEO_ADJUVANT"] as const) {
    const r = routeModel({ setting, ras: "WT", antiEgfr: true, bev: false });
    assert.equal(r.action, "DOWNGRADE_ANTI_EGFR_ADJUVANT", setting);
    assert.equal(r.efficacyScore, 0.0, setting);
  }
});

test("metastatic RAS-WT anti-EGFR (no bev) -> STRONG, score 0.268", () => {
  const r = routeModel({ setting: "METASTATIC", ras: "WT", antiEgfr: true, bev: false, confidence: 0.95 });
  assert.equal(r.tier, "STRONG");
  assert.equal(r.efficacyScore, 0.268);
  assert.equal(r.action, "PREFER_ANTI_EGFR");
  assert.equal(r.usedPooledFallback, false);
});

test("anti-EGFR + bev -> HARD_BLOCK takes precedence over RAS-WT benefit", () => {
  const r = routeModel({ setting: "METASTATIC", ras: "WT", antiEgfr: true, bev: true });
  assert.equal(r.tier, "HARD_BLOCK");
  assert.equal(r.action, "BLOCK_REGIMEN");
  assert.equal(r.efficacyScore, 0.0);
  assert.ok(r.efficacy.evidence.some((e) => e.source.includes("PACCE")));
});

test("low single-trial confidence (<0.85) -> pooled fallback flag", () => {
  const hi = routeModel({ setting: "METASTATIC", ras: "WT", antiEgfr: true, bev: false, confidence: 0.95 });
  const lo = routeModel({ setting: "METASTATIC", ras: "WT", antiEgfr: true, bev: false, confidence: 0.7 });
  assert.equal(hi.usedPooledFallback, false);
  assert.equal(lo.usedPooledFallback, true);
  assert.ok(lo.reasonCodes.includes("POOLED_FALLBACK"));
});

test("routing is deterministic (deep-equal on repeat)", () => {
  const p: PatientProfile = { setting: "METASTATIC", ras: "MUT", antiEgfr: true, bev: false, confidence: 0.9 };
  assert.deepEqual(routeModel(p), routeModel(p));
});

test("anti-EGFR absent -> NOT_APPLICABLE (ties to BreAK: no anti-EGFR either arm)", () => {
  const r = routeModel({ setting: "METASTATIC", ras: "MUT", antiEgfr: false, bev: true });
  assert.equal(r.tier, "NOT_APPLICABLE");
  assert.equal(r.action, "ANTI_EGFR_NOT_IN_REGIMEN");
});
