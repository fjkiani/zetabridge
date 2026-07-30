"""
bridge_edge_minter.py — Session 7
Mints cross-endpoint bridge edges between Synapse and PDS entities.

Bridge relation types:
  gene_trial_link          — GenomicFeature (Synapse) → Trial (PDS) via gene mention
  shares_gene_target       — biomarker node → cohort/vault (cross-endpoint gene link)
  federated_with           — vault:synapse_msk_spectrum → vault:sas_pds
  ae_blind_spot            — AdverseEventTerm (PDS) → vault:synapse_msk_spectrum
  potential_external_control — cohort:msk_spectrum_hgsoc → Trial (PDS)
  genomic_ae_hypothesis    — GenomicFeature (Synapse) → AdverseEventTerm (PDS)
  cancer_type_overlap      — PatientCohort (Synapse) → Trial (PDS)

Idempotent: deduplicates on (source, relation, target).
All edges tagged _bridge=True, _session=7.
"""

import json, datetime
from pathlib import Path
from collections import Counter

KG_DIR = Path('/workspace/zeta_vault/kg')

GENE_AE_MAP = {
    'BRCA1': ['neutropenia', 'thrombocytopenia', 'anemia'],
    'BRCA2': ['neutropenia', 'thrombocytopenia'],
    'PIK3CA': ['hyperglycemia', 'diarrhea', 'nausea'],
    'NF1': ['nausea', 'vomiting', 'fatigue'],
    'CDK12': ['neutropenia', 'fatigue'],
}

def mint_edge(source, relation, target, attributes=None):
    return {
        'source': source, 'relation': relation, 'target': target,
        'source_receipts': [{'source': 'bridge_edge_minter_s7', 'session': 7}],
        'attributes': {**(attributes or {}), '_bridge': True, '_session': 7,
                       '_mint_timestamp': datetime.datetime.utcnow().isoformat()},
        '_mint_planner': 'zeta_custodian_session7',
        '_mint_timestamp': datetime.datetime.utcnow().isoformat(),
    }

def run():
    with open(KG_DIR / 'zeta_entities.json') as f:
        entities = json.load(f)
    with open(KG_DIR / 'zeta_edges.json') as f:
        edges = json.load(f)

    entity_map = {e['id']: e for e in entities}
    edge_keys = {(e['source'], e['relation'], e['target']) for e in edges}
    new_edges = []

    def add(src, rel, tgt, attrs=None):
        key = (src, rel, tgt)
        if key not in edge_keys:
            edge_keys.add(key)
            new_edges.append(mint_edge(src, rel, tgt, attrs))

    gf_entities  = [e for e in entities if e.get('type') == 'GenomicFeature']
    ae_terms     = [e for e in entities if e.get('type') == 'AdverseEventTerm']
    trial_entities = [e for e in entities if e.get('type') == 'Trial' and 'sas' in e['id']]
    msk_cohort   = 'cohort:msk_spectrum_hgsoc'
    vault_msk    = 'vault:synapse_msk_spectrum'
    vault_pds    = 'vault:sas_pds'
    bm_kras      = 'biomarker:kras:colorectal'

    # 1. Gene-trial links
    for gf in gf_entities:
        gene = gf['attributes'].get('gene','').upper()
        if not gene: continue
        for trial in trial_entities:
            if gene in str(trial).upper():
                add(gf['id'], 'gene_trial_link', trial['id'],
                    {'gene': gene, 'pds_cancer': trial['attributes'].get('cancer_type','')})

    # 2. KRAS biomarker bridges
    add(bm_kras, 'shares_gene_target', vault_msk,
        {'gene': 'KRAS', 'mismatch': 'colorectal_mutation_vs_hgsoc_copy_number',
         'pds_wt': 884, 'pds_mut': 667})
    add(bm_kras, 'shares_gene_target', msk_cohort,
        {'gene': 'KRAS', 'mismatch': 'assay_type_mismatch'})
    add(vault_msk, 'federated_with', vault_pds,
        {'bridge_type': 'cross_endpoint_federation', 'session': 7})
    add(bm_kras, 'gene_trial_link', 'trial:sas:Colorec_Amgen_2005_262',
        {'gene': 'KRAS', 'pds_cancer': 'Colorec'})
    add(bm_kras, 'gene_trial_link', 'trial:sas:Colorec_Amgen_2006_263',
        {'gene': 'KRAS', 'pds_cancer': 'Colorec'})

    # 3. AE blind-spot edges (severe AEs → Synapse vault)
    for ae in ae_terms:
        gd = ae['attributes'].get('grade_distribution', {})
        total = sum(int(gd.get(str(g), 0) or 0) for g in [3,4,5])
        fatal = int(gd.get('5', 0) or 0)
        if total >= 100:
            add(ae['id'], 'ae_blind_spot', vault_msk,
                {'total_severe_events': total, 'fatal_events': fatal,
                 'ae_name': ae.get('name',''),
                 'n_trials': ae['attributes'].get('n_trials', 0)})

    # 4. External control bridges
    for trial in trial_entities:
        add(msk_cohort, 'potential_external_control', trial['id'],
            {'msk_cancer': 'HGSOC_ovarian', 'pds_cancer': trial['attributes'].get('cancer_type',''),
             'n_msk_patients': 40, 'n_pds_patients': trial['attributes'].get('n_patients', 0)})

    # 5. Genomic-AE hypothesis edges
    for gf in gf_entities:
        gene = gf['attributes'].get('gene','').upper()
        for ae in ae_terms:
            ae_name = ae.get('name','').lower()
            for kw in GENE_AE_MAP.get(gene, []):
                if kw in ae_name:
                    add(gf['id'], 'genomic_ae_hypothesis', ae['id'],
                        {'gene': gene, 'ae_keyword': kw,
                         'hypothesis': f'{gene} alteration in HGSOC may predispose to {ae_name}'})

    # 6. Cancer type overlap (cohort → all trials)
    cohorts = [e for e in entities if e.get('type') in ('PatientCohort','CohortStratum')
               and ('hgsoc' in e['id'].lower() or 'msk' in e['id'].lower())]
    for cohort in cohorts:
        for trial in trial_entities:
            add(cohort['id'], 'cancer_type_overlap', trial['id'],
                {'msk_cancer': 'HGSOC', 'pds_cancer': trial['attributes'].get('cancer_type','')})

    # Save
    all_edges = edges + new_edges
    with open(KG_DIR / 'zeta_edges.json', 'w') as f:
        json.dump(all_edges, f)
    with open(KG_DIR / 'bridge_edges_session7.json', 'w') as f:
        json.dump(new_edges, f, indent=2)

    print(f"New bridge edges: {len(new_edges)}")
    print(f"Total edges: {len(all_edges)}")
    print("By relation:", dict(Counter(e['relation'] for e in new_edges).most_common()))
    return new_edges

if __name__ == '__main__':
    run()
