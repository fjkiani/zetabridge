"""
neo4j_bridge_loader.py — Session 7
Idempotent loader for cross-endpoint bridge edges into Neo4j.
MERGE on (source, relation, target) — safe to run multiple times.
"""
import json, os, sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv('/workspace/.env')

def run(bridge_file=None):
    try:
        from neo4j import GraphDatabase
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'neo4j'], check=True)
        from neo4j import GraphDatabase

    bridge_file = bridge_file or Path('/workspace/zeta_vault/kg/bridge_edges_session7.json')
    with open(bridge_file) as f:
        edges = json.load(f)

    uri  = os.environ['NEO4J_URI']
    user = os.environ['NEO4J_USER']
    pw   = os.environ['NEO4J_PASSWORD']

    driver = GraphDatabase.driver(uri, auth=(user, pw))
    CYPHER = """
    MERGE (s:ZetaVault {id: $source})
    MERGE (t:ZetaVault {id: $target})
    MERGE (s)-[r:BRIDGE_EDGE {relation: $relation, session: 7}]->(t)
    ON CREATE SET r.bridge = true, r.mint_ts = $mint_ts
    ON MATCH SET r.last_seen = 7
    """
    loaded = errors = 0
    with driver.session() as session:
        for edge in edges:
            try:
                session.run(CYPHER, source=edge['source'], target=edge['target'],
                            relation=edge['relation'], mint_ts=edge.get('_mint_timestamp',''))
                loaded += 1
            except Exception as ex:
                errors += 1
                if errors <= 3: print(f"Error: {ex}")
    driver.close()
    print(f"Neo4j: {loaded} loaded, {errors} errors")
    return loaded

if __name__ == '__main__':
    run()
