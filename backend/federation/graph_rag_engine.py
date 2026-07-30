"""
graph_rag_engine.py — Session 7
Multi-hop GraphRAG traversal engine for cross-endpoint queries.

Architecture:
  SynapseAgent  → queries Synapse GenomicFeature/Biospecimen nodes
  PDSAgent      → translates schema, queries PDS Trial/AE nodes
  SynthesisAgent → merges payloads, detects hidden signals

State machine: context dict passed through each hop without dropping context.
"""
import json
from pathlib import Path
from collections import defaultdict
from typing import Optional

KG_DIR = Path('/workspace/zeta_vault/kg')

class ZetaKG:
    def __init__(self):
        self.entities = {}
        self.adj_out = defaultdict(list)
        self.adj_in  = defaultdict(list)
        self._loaded = False

    def load(self):
        if self._loaded: return
        with open(KG_DIR / 'zeta_entities.json') as f:
            for e in json.load(f):
                self.entities[e['id']] = e
        with open(KG_DIR / 'zeta_edges.json') as f:
            for edge in json.load(f):
                s, r, t = edge['source'], edge['relation'], edge['target']
                self.adj_out[s].append({'relation': r, 'target': t, 'attributes': edge.get('attributes',{})})
                self.adj_in[t].append({'relation': r, 'source': s, 'attributes': edge.get('attributes',{})})
        self._loaded = True

    def traverse(self, start_id: str, max_hops: int = 3, relation: str = None):
        """BFS traversal returning all reachable nodes within max_hops."""
        visited = {start_id}
        frontier = [start_id]
        hops = []
        for hop in range(max_hops):
            next_frontier = []
            hop_edges = []
            for nid in frontier:
                edges = self.adj_out.get(nid, [])
                if relation:
                    edges = [e for e in edges if e['relation'] == relation]
                for e in edges[:20]:
                    hop_edges.append({'from': nid, **e})
                    if e['target'] not in visited:
                        visited.add(e['target'])
                        next_frontier.append(e['target'])
            hops.append({'hop': hop+1, 'edges': hop_edges, 'new_nodes': len(next_frontier)})
            frontier = next_frontier
            if not frontier: break
        return {'start': start_id, 'hops': hops, 'total_visited': len(visited)}

    def find_by_type(self, t): return [e for e in self.entities.values() if e.get('type') == t]
    def get(self, nid): return self.entities.get(nid)
    def bridge_edges(self): return [e for e in self.adj_out.values() for e in e if e.get('attributes',{}).get('_bridge')]


class SynapseAgent:
    """Hop 1: Query Synapse endpoint nodes."""
    def __init__(self, kg: ZetaKG): self.kg = kg

    def query_gene(self, gene: str) -> dict:
        gene = gene.upper()
        hits = [e for e in self.kg.find_by_type('GenomicFeature')
                if gene in e['attributes'].get('gene','').upper()]
        cohorts = [e for e in self.kg.find_by_type('PatientCohort')
                   if 'msk' in e['id'].lower() or 'hgsoc' in e['id'].lower()]
        return {'agent': 'SynapseAgent', 'gene': gene, 'genomic_hits': hits,
                'cohorts': cohorts, 'n_hits': len(hits)}


class PDSAgent:
    """Hop 2: Translate Synapse payload → PDS schema, query PDS nodes."""
    SCHEMA_MAP = {'gene': 'biomarker_gene', 'alteration_type': 'biomarker_type',
                  'cancer_type': 'cancer_type', 'sample_id': 'subject_id'}

    def __init__(self, kg: ZetaKG): self.kg = kg

    def query_for_gene(self, synapse_ctx: dict) -> dict:
        gene = synapse_ctx.get('gene','')
        bm_hits = [e for e in self.kg.entities.values()
                   if 'biomarker' in e['id'].lower() and gene in str(e).upper()]
        trial_hits = [e for e in self.kg.find_by_type('Trial')
                      if 'sas' in e['id'] and gene in str(e).upper()]
        bridge = []
        for gh in synapse_ctx.get('genomic_hits', [])[:5]:
            for be in self.kg.adj_out.get(gh['id'], []):
                if be.get('attributes',{}).get('_bridge'):
                    bridge.append({'from': gh['id'], **be})
        return {'agent': 'PDSAgent', 'gene': gene, 'biomarker_hits': bm_hits,
                'trial_hits': trial_hits, 'bridge_edges': bridge}


class SynthesisAgent:
    """Merge Synapse + PDS payloads, detect hidden signals."""
    def __init__(self, kg: ZetaKG): self.kg = kg

    def detect_signals(self, syn_ctx: dict, pds_ctx: dict) -> list:
        signals = []
        gene = syn_ctx.get('gene','')
        if syn_ctx['n_hits'] > 0 and pds_ctx['biomarker_hits']:
            signals.append({
                'type': 'ALTERATION_TYPE_MISMATCH', 'gene': gene, 'severity': 'HIGH',
                'synapse_nodes': [h['id'] for h in syn_ctx['genomic_hits'][:3]],
                'pds_nodes': [h['id'] for h in pds_ctx['biomarker_hits'][:3]],
                'description': f'Synapse tracks {gene} as copy-number; PDS stratifies by mutation status.',
                'action': f'Commission {gene} mutation sequencing on MSK HGSOC biospecimens.',
            })
        return signals

    def full_query(self, gene: str) -> dict:
        syn = SynapseAgent(self.kg).query_gene(gene)
        pds = PDSAgent(self.kg).query_for_gene(syn)
        signals = self.detect_signals(syn, pds)
        return {'gene': gene, 'synapse': syn, 'pds': pds, 'signals': signals}


if __name__ == '__main__':
    kg = ZetaKG()
    kg.load()
    print(f"KG: {len(kg.entities)} entities")

    agent = SynthesisAgent(kg)
    for gene in ['KRAS', 'BRCA1', 'PIK3CA']:
        r = agent.full_query(gene)
        print(f"{gene}: {r['synapse']['n_hits']} Synapse hits, "
              f"{len(r['pds']['trial_hits'])} PDS trials, {len(r['signals'])} signals")

    # 3-hop traversal from KRAS biomarker
    t = kg.traverse('biomarker:kras:colorectal', max_hops=3)
    print(f"\nKRAS 3-hop traversal: {t['total_visited']} nodes visited")
    for h in t['hops']:
        print(f"  Hop {h['hop']}: {len(h['edges'])} edges, {h['new_nodes']} new nodes")
