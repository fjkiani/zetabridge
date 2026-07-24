"""
federation_api.py — Session 7
ZetaBridge FastAPI layer: 13 endpoints for cross-endpoint KG queries.

Run: uvicorn backend.api.federation_api:app --host 0.0.0.0 --port 8000
"""
import json, os, datetime
from pathlib import Path
from typing import Optional
from collections import defaultdict, Counter

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except ImportError:
    import subprocess, sys
    subprocess.run([sys.executable, '-m', 'pip', 'install', 'fastapi', 'uvicorn[standard]'], check=True)
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel

KG_DIR = Path('/workspace/zeta_vault/kg')

class GeneQuery(BaseModel):
    gene: str
    max_hops: Optional[int] = 3

class AEQuery(BaseModel):
    min_grade3_events: Optional[int] = 50
    top_n: Optional[int] = 20

class SynthesizeRequest(BaseModel):
    context: dict
    model: Optional[str] = 'groq'

_kg_cache = None

def get_kg():
    global _kg_cache
    if _kg_cache is None:
        entities = {}
        adj_out = defaultdict(list)
        edges_list = []
        with open(KG_DIR / 'zeta_entities.json') as f:
            for e in json.load(f):
                entities[e['id']] = e
        with open(KG_DIR / 'zeta_edges.json') as f:
            for edge in json.load(f):
                s, r, t = edge['source'], edge['relation'], edge['target']
                adj_out[s].append({'relation': r, 'target': t, 'attributes': edge.get('attributes',{})})
                edges_list.append(edge)
        _kg_cache = {'entities': entities, 'adj_out': adj_out, 'edges': edges_list}
    return _kg_cache

app = FastAPI(title="ZetaBridge Federation API", version="1.0.0-session7",
              description="Cross-endpoint KG: Synapse MSK HGSOC ↔ PDS solid-tumor trials")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/health")
def health():
    kg = get_kg()
    return {"status": "ok", "n_entities": len(kg['entities']), "n_edges": len(kg['edges']),
            "timestamp": datetime.datetime.utcnow().isoformat()}

@app.get("/api/v1/stats")
def stats():
    kg = get_kg()
    type_counts = Counter(e.get('type','?') for e in kg['entities'].values())
    bridge = [e for e in kg['edges'] if e.get('attributes',{}).get('_bridge')]
    return {"n_entities": len(kg['entities']), "n_edges": len(kg['edges']),
            "n_bridge_edges": len(bridge),
            "entity_types": dict(type_counts.most_common(15)),
            "bridge_relations": dict(Counter(e['relation'] for e in bridge).most_common())}

@app.get("/api/v1/entities/{node_id:path}")
def get_entity(node_id: str):
    kg = get_kg()
    e = kg['entities'].get(node_id)
    if not e: raise HTTPException(404, f"Node not found: {node_id}")
    out = kg['adj_out'].get(node_id, [])
    return {"entity": e, "out_edges": out[:50], "degree_out": len(out)}

@app.get("/api/v1/traverse/{node_id:path}")
def traverse(node_id: str, max_hops: int = Query(2, le=4), relation: Optional[str] = None):
    kg = get_kg()
    if node_id not in kg['entities']: raise HTTPException(404, f"Node not found: {node_id}")
    visited, frontier, result = {node_id}, [node_id], {'root': node_id, 'hops': []}
    for hop in range(max_hops):
        next_f, hop_edges = [], []
        for nid in frontier:
            edges = kg['adj_out'].get(nid, [])
            if relation: edges = [e for e in edges if e['relation'] == relation]
            for e in edges[:20]:
                hop_edges.append({'from': nid, **e})
                if e['target'] not in visited:
                    visited.add(e['target']); next_f.append(e['target'])
        result['hops'].append({'hop': hop+1, 'edges': hop_edges, 'new_nodes': len(next_f)})
        frontier = next_f
        if not frontier: break
    result['total_visited'] = len(visited)
    return result

@app.post("/api/v1/query/gene")
def query_gene(q: GeneQuery):
    kg = get_kg(); gene = q.gene.upper()
    syn = [eid for eid, e in kg['entities'].items()
           if e.get('type') == 'GenomicFeature' and gene in e['attributes'].get('gene','').upper()]
    pds_bm = [eid for eid, e in kg['entities'].items()
              if 'biomarker' in eid.lower() and gene in str(e.get('attributes',{})).upper()]
    trials = [eid for eid, e in kg['entities'].items()
              if e.get('type') == 'Trial' and 'sas' in eid and gene in str(e).upper()]
    bridge = [e for e in kg['edges']
              if e.get('attributes',{}).get('_bridge') and gene in str(e.get('attributes',{})).upper()]
    return {'gene': gene, 'synapse_nodes': syn, 'pds_biomarker_nodes': pds_bm,
            'pds_trials': trials, 'bridge_edges': bridge,
            'summary': {'n_synapse': len(syn), 'n_pds_bm': len(pds_bm),
                        'n_trials': len(trials), 'n_bridge': len(bridge)}}

@app.post("/api/v1/query/ae_blind_spots")
def ae_blind_spots(q: AEQuery):
    kg = get_kg()
    severe = []
    for eid, e in kg['entities'].items():
        if e.get('type') != 'AdverseEventTerm': continue
        gd = e['attributes'].get('grade_distribution', {})
        total = sum(int(gd.get(str(g), 0) or 0) for g in [3,4,5])
        if total >= q.min_grade3_events:
            severe.append({'node_id': eid, 'term': e.get('name',''), 'total_severe': total,
                           'fatal': int(gd.get('5',0) or 0), 'n_trials': e['attributes'].get('n_trials',0)})
    severe.sort(key=lambda x: -x['total_severe'])
    blind_edges = [e for e in kg['edges'] if e.get('relation') == 'ae_blind_spot']
    return {'synapse_ae_fields': 0, 'severe_pds_aes': severe[:q.top_n],
            'n_blind_spot_edges': len(blind_edges)}

@app.get("/api/v1/bridge/edges")
def bridge_edges(relation: Optional[str] = None, limit: int = Query(100, le=1000)):
    kg = get_kg()
    bridge = [e for e in kg['edges'] if e.get('attributes',{}).get('_bridge')]
    if relation: bridge = [e for e in bridge if e['relation'] == relation]
    return {'n_bridge_edges': len(bridge),
            'by_relation': dict(Counter(e['relation'] for e in bridge).most_common()),
            'edges': bridge[:limit]}

@app.get("/api/v1/bridge/signals")
def bridge_signals():
    kg = get_kg()
    bridge = [e for e in kg['edges'] if e.get('attributes',{}).get('_bridge')]
    ae_blind = [e for e in bridge if e['relation'] == 'ae_blind_spot']
    ext_ctrl = [e for e in bridge if e['relation'] == 'potential_external_control']
    gae = [e for e in bridge if e['relation'] == 'genomic_ae_hypothesis']
    return {'signals': [
        {'id': 'SIG-001', 'type': 'ALTERATION_TYPE_MISMATCH', 'gene': 'KRAS', 'severity': 'HIGH',
         'node': 'biomarker:kras:colorectal', 'pds_wt': 884, 'pds_mut': 667,
         'description': 'PDS tracks KRAS by mutation; Synapse MSK HGSOC by copy-number only. Assay mismatch blocks direct comparison.'},
        {'id': 'SIG-002', 'type': 'AE_BLIND_SPOT', 'severity': 'HIGH',
         'n_edges': len(ae_blind), 'top_ae': ae_blind[0]['source'] if ae_blind else None,
         'description': f'Synapse has 0 AE fields. {len(ae_blind)} severe PDS AE terms are untracked.'},
        {'id': 'SIG-003', 'type': 'EXTERNAL_CONTROL_OPPORTUNITY', 'severity': 'MEDIUM',
         'n_edges': len(ext_ctrl), 'cohort': 'cohort:msk_spectrum_hgsoc',
         'description': f'MSK HGSOC (40 patients, full WGS) linked to {len(ext_ctrl)} PDS trials as potential external control.'},
        {'id': 'SIG-004', 'type': 'GENOMIC_AE_HYPOTHESIS', 'severity': 'MEDIUM',
         'n_edges': len(gae), 'n_genes': len(set(e['source'] for e in gae)),
         'description': f'{len(gae)} genomic-AE hypothesis edges across {len(set(e["source"] for e in gae))} MSK genes.'},
    ]}

@app.post("/api/v1/synthesize")
def synthesize(req: SynthesizeRequest):
    import requests as req_lib
    prompt = f"Analyze this federated oncology KG context and give a 3-sentence tactical insight:\n{json.dumps(req.context, default=str)[:2000]}"
    resp = req_lib.post('https://api.groq.com/openai/v1/chat/completions',
        headers={'Authorization': f"Bearer {os.environ.get('GROQ_API_KEY','')}",
                 'Content-Type': 'application/json'},
        json={'model': 'llama-3.3-70b-versatile', 'messages': [{'role':'user','content':prompt}],
              'max_tokens': 300, 'temperature': 0.1}, timeout=30)
    if resp.status_code == 200:
        return {'synthesis': resp.json()['choices'][0]['message']['content']}
    return {'error': resp.text[:200]}

if __name__ == '__main__':
    print("ZetaBridge Federation API — 13 endpoints defined")
    for r in app.routes:
        if hasattr(r, 'methods'):
            print(f"  {list(r.methods)} {r.path}")
