# Zeta Bridge — Session 11: Deep Graph Analysis

**Five-worker parallel mining of the federated knowledge graph**
Planner: `zeta_custodian_session11` · Date: 2026-07-23
Base: `zeta/session10-ega-endpoint` @ `c3c570d`

---

## Mandate

Five workers, run simultaneously, each owning one analytical layer. Go deeper than surface
counts: run graph algorithms, find the outliers and the strongest signals, mint every result
back into the KG. Pure data engineering — no shallow review.

The federated KG connects three endpoints:

| Endpoint | Source | Content |
|---|---|---|
| **A** | MSK SPECTRUM / Synapse | HGSOC single-cell + bulk |
| **B** | PDS / SAS | 94 solid-tumor clinical trials |
| **C** | EGA EGAD00001011049 / BriTROC | HGSOC sWGS, 679 samples |

---

## Headline result

| | Before | After | Added |
|---|---|---|---|
| **KG entities** | 26,312 | **26,923** | +611 |
| **KG edges** | 98,609 | **101,595** | +2,986 |
| **Neo4j nodes** | 30,106 | **30,717** | +611 |
| **Neo4j edges** | 99,139 | **102,125** | +2,986 |

All 611 nodes and 2,986 edges verified present in Neo4j after push. Zero dangling edges,
zero collisions with existing KG IDs — a clean additive merge. `crispro_kb_v3` untouched.

---

## Worker 0 — Topology, centrality, community structure
**Minted: 108 nodes, 350 edges** (90 GraphCentralityScore, 13 GraphCommunity, 5 StructuralGap)

- **PageRank** (full digraph, α=0.85): the graph is anchored by high-enrollment SAS breast
  trials — `Breast_Allianc_2002_200` (PR=0.0340) and `Breast_Allianc_2002_194` (0.0279) — with
  the two data-vault nodes (`vault:synapse_msk_spectrum`, `vault:sas_pds`) close behind. On the
  **signal-only subgraph** (patients excluded), the vaults dominate, followed by the systemic
  toxicity terms nausea, thrombocytopenia, diarrhoea, neutropenia.
- **Louvain**: 1,984 communities, but only ~13 have ≥5 members. The meaningful structure:
  - **Community 0** = the entire EGA endpoint (679 EGAFile + 679 EGASample) — cleanly separated.
  - **Community 4** = the BriTROC biospecimen block (719 Biospecimen + 72 Trial/TrialDesign).
  - The largest communities (7,247 / 5,076 / 3,042 nodes) are patient + escalation-signal
    clusters organized around individual large trials.
- **Betweenness**: `vault:synapse_msk_spectrum` is the single highest-betweenness node — the
  MSK endpoint is the structural bridge of the federation.
- **Structure**: 1,971 isolated nodes (almost all un-linked AE terms), max degree 3,881.
  Rich-club coefficient stays low (φ ≤ 0.03) — hubs are **not** densely interconnected; this is
  a hub-and-spoke federation, not a clique of hubs.

## Worker 1 — Adverse-event signal outliers
**Minted: 67 nodes, 222 edges** (39 AEOutlierSignal, 20 ToxicitySyndrome, 2 DrugToxicityProfile)

- **Strongest real signal: Panitumumab + FOLFOX4 → acneiform rash, rate ratio 124.7 (z = 6.07).**
  The entire top of the outlier ranking is the anti-EGFR cutaneous-toxicity class (acneiform
  rash, skin rash, paronychia, pruritus) — the canonical EGFR-inhibitor skin syndrome, recovered
  purely from the data.
- **28 "capped" signals** (control rate = 0, i.e. drug-exclusive AEs) are all Panitumumab skin/nail
  toxicities — events essentially never seen in the control arms.
- **Cross-trial replication**: skin rash, erythema, dry skin, pruritus and paronychia each replicate
  across all 4 Panitumumab regimens — a highly consistent, reproducible toxicity fingerprint.
- **Toxicity syndromes**: the largest severe-AE co-occurrence clique spans 9 MedDRA terms
  (myelosuppression + GI cluster), minted as ToxicitySyndrome nodes.

## Worker 2 — Escalation-pattern deep mining
**Minted: 34 nodes, 959 edges** (10 ArmEscalationBurden, 4 DrugClassEscalationProfile, 20 EscalationSyndrome)

- **Highest escalation burden: `Colorec_Amgen_2006_264` arm 42** — burden 1,738 across 420 escalation
  signals, 548 severe events. The top-burden arms are all colorectal anti-EGFR / chemo combinations.
- **Drug-class comparison**: EGFR-arm vs chemo-arm escalation burden did **not** differ significantly
  (Mann-Whitney U, p = 0.86) — the sample of arms is small and the test is underpowered; reported
  honestly rather than over-interpreted.
- **Core escalation syndrome**: {vomiting, thrombocytopenia, fatigue, constipation, nausea}
  co-escalate together across the most arms — the shared chemotherapy escalation backbone.
- **Grade trajectories**: rash, neutropenia, thrombocytopenia, leukopenia and stomatitis escalate
  across all 5 qualifying trials.

## Worker 3 — Cross-endpoint genomic-AE bridge + EGA structure
**Minted: 232 nodes, 1,295 edges** (20 GenomicAEBridge, 2 FederationPathNode, 28 EGAFileOutlier, 182 LongitudinalPair)

- **679 Biospecimen ↔ EGAFile reconciliations** — the single biggest structural win of the session.
  The Session 7/8 BriTROC biospecimens (JBLAB-*) and the Session 10 EGA files (EGAF*) were ingested
  independently; matching on EGAF accession unified them into one coherent BriTROC block
  (verified traversable in Neo4j, e.g. `specimen:britroc1:JBLAB-4927 → ega:file:EGAF00008095623`).
  This is a **within-endpoint** reconciliation (both are BriTROC) — not a cross-endpoint patient join.
- **Genomic-AE bridges**: top bridge is **BRCA1 → neutropenia** (score 0.630). Important caveat —
  the driver genes (BRCA1, CDK12, NF1, PIK3CA, TOP1) each recur at only 2.5% in the MSK HGSOC cohort,
  so bridge strength is driven by **cross-trial escalation consistency**, not recurrence, and the RR
  term is 0 (these AE terms have no matched DrugAESignal). These are **hypothesis-generating links**
  across endpoints, not validated associations.
- **182 longitudinal pairs**: EGA subjects with both a diagnosis and a post-relapse sample — the
  backbone for any future BriTROC disease-progression analysis.
- **Gene-drug target matches**: TOP1 ↔ FOLFIRI and TOP1 ↔ Topotecan (topoisomerase-I targeting).
- **28 large EGA file outliers** (>1.39 GB BAMs) flagged for QC attention.

## Worker 4 — Patient-level outliers + AE burden
**Minted: 176 nodes, 180 edges** (100 PatientAEBurdenScore, 50 RareAEProfile, 26 ArmGradeProfile)

- **122 patients exceed the mean+3σ severe-AE burden threshold.** The most extreme carries
  **14 severe AEs (z = 7.96)** — a single breast-trial patient with an exceptional toxicity load.
- **5,287 rare AE combinations** (≤2 patients sharing ≥3 terms); the rarest profiles reach 39
  distinct terms in one patient.
- **Arm grade divergence**: within `Breast_Allianc_2002_194`, arms 1 vs 8 have the most divergent
  severe-grade profiles (KL = 0.377).
- **OS events are descriptive only.** Patients with an OS event had a slightly higher mean severe-AE
  count (3.0 vs 2.1), but **no survival inference is drawn** — the KG has no time-to-event data.

---

## Caveats (read before citing any number)

1. **Genomic-AE bridges are hypothesis-generating.** Driver genes recur at 2.5%; bridge scores are
   dominated by escalation consistency, and the rate-ratio term is 0. Do not read these as validated
   pharmacogenomic associations.
2. **SAS AE vocabulary is un-harmonized** — multiple spellings of the same event (e.g. febrile
   neutropenia) exist as distinct terms. Co-occurrence and clique nodes reflect raw terms.
3. **Small-sample statistics**: the EGFR-vs-chemo escalation test (p=0.86) is underpowered
   (1 vs 6 arms). Treated as inconclusive.
4. **Top escalation-burden arm** (burden=1,738) sits just below the mean+2σ statistical-outlier
   threshold (1,766); flagged as high-burden, not a formal outlier.
5. **No cross-endpoint patient joins.** The 679 Biospecimen↔EGAFile links are within the BriTROC
   cohort only.

---

## Artifacts

- Full updated KG snapshot: `zeta_entities_s11.json` / `zeta_edges_s11.json`
- Per-worker results: `s11_w0..w4_results.json`
- Integration summary: `s11_integration_summary.json`
- Analysis scripts: `scripts/s11_*.py`
- 16 figures (PNG + SVG for the two headline charts): `figures/`
- Machine-readable manifest: `SESSION11_MANIFEST.json`
