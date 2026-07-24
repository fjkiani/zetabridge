"""
S12 W4 — Deep longitudinal + genomic-bridge chains.

Walk the S11 structures to full multi-hop context:
 (1) LongitudinalPair -> its ega:sample members (diagnosis + post-relapse) ->
     shared EGA dataset. Captures the per-subject longitudinal sampling chain.
 (2) BRIDGE_EDGE (genomicfeature -> adverse-event) -> back through the
     biospecimen(s) that harbor the gene -> cohort, giving the full provenance
     chain for each genomic-AE hypothesis.
Mint DeepChain nodes capturing every intermediate node id.

Schema note (verified live, Session 12): LongitudinalPair nodes store their
sample membership in `attributes_json` (keys: subject_id, timepoints,
n_samples, sample_ids=[ega:sample:*], cohort, disease) — NOT as
dx_specimen/relapse_specimen props. And ega:sample nodes are NOT linked to
specimen:britroc1/ega:file/community (they connect to the pair and the EGA
dataset only), so the honest longitudinal chain terminates at the dataset.
The genomic-bridge section is unchanged (verified: 92 genomic BRIDGE_EDGEs).
"""
import json
import sys

sys.path.insert(0, "/mnt/shared-workspace/shared")
import s12_traverse_util as U

drv = U.driver()
nodes, edges = [], []
results = {"worker": "w4_deep_chains", "longitudinal": {}, "genomic_bridge": {}}

# --- (1) Longitudinal pairs -> deep sampling context ----------------------
q_lp = ("MATCH (lp) WHERE lp.type='LongitudinalPair' "
        "RETURN lp.id AS id, lp.attributes_json AS aj LIMIT 400")
lps = U.read(drv, q_lp)
print(f"[w4] {len(lps)} longitudinal pairs")
lp_minted = 0
lp_with_dataset = 0
for lp in lps[:60]:  # deep-walk a bounded sample
    try:
        meta = json.loads(lp["aj"] or "{}")
    except Exception:
        meta = {}
    subject = meta.get("subject_id")
    timepoints = meta.get("timepoints")
    try:
        sample_ids = json.loads(meta.get("sample_ids", "[]")) if isinstance(meta.get("sample_ids"), str) else meta.get("sample_ids", [])
    except Exception:
        sample_ids = []
    # verify sample nodes exist + find the EGA dataset each reaches
    verified_samples, datasets = [], set()
    for sid in sample_ids:
        r = U.read(drv, "MATCH (s {id:$id}) OPTIONAL MATCH (s)-[:RELATES]-(d) "
                        "WHERE d.id STARTS WITH 'ega:dataset:' "
                        "RETURN s.id AS sid, collect(DISTINCT d.id) AS ds", {"id": sid}, cap=1)
        if r and r[0]["sid"]:
            verified_samples.append(r[0]["sid"])
            for d in (r[0]["ds"] or []):
                datasets.add(d)
    if datasets:
        lp_with_dataset += 1
    depth = 1 + len(verified_samples) + len(datasets)
    nid = f"deepchain:s12:lp:{lp['id'].split(':')[-1]}"
    nodes.append(U.mk_node(
        nid, "DeepChain", f"Longitudinal deep chain {lp['id']}",
        {"kind": "longitudinal", "pair": lp["id"], "subject_id": subject,
         "timepoints": timepoints, "sample_ids": verified_samples,
         "n_samples": len(verified_samples), "ega_datasets": sorted(datasets),
         "cohort": meta.get("cohort"), "disease": meta.get("disease"),
         "chain_depth": depth},
        label="DeepChain"))
    edges.append(U.mk_edge(nid, "deep_chain_of", lp["id"]))
    for s in verified_samples:
        edges.append(U.mk_edge(nid, "via_sample", s))
    for d in sorted(datasets):
        edges.append(U.mk_edge(nid, "reaches_dataset", d))
    lp_minted += 1
results["longitudinal"] = {"n_pairs": len(lps), "walked": min(60, len(lps)),
                           "chains_minted": lp_minted, "with_ega_dataset": lp_with_dataset}
print(f"[w4] longitudinal chains minted={lp_minted}, reaching EGA dataset={lp_with_dataset}")

# --- (2) BRIDGE_EDGE genomic -> AE full provenance ------------------------
q_br = ("MATCH (g)-[:BRIDGE_EDGE]->(a) WHERE g.id STARTS WITH 'genomicfeature:msk:' "
        "AND a.id STARTS WITH 'ae:' RETURN g.id AS gid, a.id AS aid LIMIT 100")
brs = U.read(drv, q_br)
print(f"[w4] {len(brs)} genomic->AE bridge edges")
br_minted = 0
br_with_specimen = 0
for i, br in enumerate(brs[:40]):
    gid, aid = br["gid"], br["aid"]
    prov = U.read(drv,
        "MATCH (g {id:$gid})-[:HARBORS_MUTATION|HARBORS_CNA]-(b) "
        "OPTIONAL MATCH (b)-[:SPECIMEN_OF|DRAWN_FROM]-(c) WHERE c.id STARTS WITH 'cohort:' "
        "RETURN b.id AS bid, collect(DISTINCT c.id) AS cohorts LIMIT 3", {"gid": gid})
    specimens = [p["bid"] for p in prov if p["bid"]]
    cohorts = sorted({c for p in prov for c in (p["cohorts"] or [])})
    if specimens:
        br_with_specimen += 1
    chain = [gid] + specimens + cohorts + [aid]
    nid = f"deepchain:s12:bridge:{i}"
    nodes.append(U.mk_node(
        nid, "DeepChain", f"Genomic-AE provenance chain {gid} -> {aid}",
        {"kind": "genomic_bridge", "gene_node": gid, "ae_node": aid,
         "harboring_specimens": specimens, "cohorts": cohorts,
         "full_chain": chain, "chain_depth": len(chain)},
        label="DeepChain"))
    edges.append(U.mk_edge(nid, "chain_gene", gid))
    edges.append(U.mk_edge(nid, "chain_ae", aid))
    for sp in specimens:
        edges.append(U.mk_edge(nid, "via_specimen", sp))
    br_minted += 1
results["genomic_bridge"] = {"n_bridges": len(brs), "walked": min(40, len(brs)),
                             "chains_minted": br_minted, "with_specimen": br_with_specimen}
print(f"[w4] genomic-bridge chains minted={br_minted}, with specimen={br_with_specimen}")

results["n_nodes"] = len(nodes)
results["n_edges"] = len(edges)
U.dump_results("/mnt/shared-workspace/shared/s12_w4_results.json",
               {"results": results, "nodes": nodes, "edges": edges})
print(f"[w4] DONE nodes={len(nodes)} edges={len(edges)}")
drv.close()
