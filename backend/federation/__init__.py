"""Federation graph-access subsystem (Session 12).

Read-only, application-enforced access layer over the federated Neo4j
knowledge graph. Exposes a Cypher safety validator (`cypher_guard`) and a
pooled Neo4j-backed service (`graph_service`) consumed by the FastAPI
`/api/graph` router and the MCP server.
"""
