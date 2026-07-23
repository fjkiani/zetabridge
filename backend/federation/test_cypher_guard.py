"""Attack-suite for the read-only Cypher validator.

Run:  cd backend && python3 -m pytest federation/test_cypher_guard.py -q
"""

import pytest

from federation.cypher_guard import (
    CypherWriteAttempt,
    check_read_only,
    enforce_limit,
    validate_read_only,
)

# --- Queries that MUST be rejected ---------------------------------------
FORBIDDEN = [
    "CREATE (n:Foo {id:'x'})",
    "MATCH (n) DELETE n",
    "MATCH (n) DETACH DELETE n",
    "MERGE (n:Foo {id:'x'})",
    "MATCH (n) SET n.x = 1",
    "MATCH (n) REMOVE n.x",
    "DROP INDEX idx",
    "DROP CONSTRAINT c",
    "CREATE INDEX idx FOR (n:Foo) ON (n.id)",
    "CREATE CONSTRAINT c FOR (n:Foo) REQUIRE n.id IS UNIQUE",
    "CREATE DATABASE evil",
    "DROP DATABASE neo4j",
    "CREATE USER evil SET PASSWORD 'p'",
    "DROP USER neo4j",
    "ALTER USER neo4j SET PASSWORD 'p'",
    "CREATE ROLE admin2",
    "GRANT ROLE admin TO evil",
    "REVOKE ROLE admin FROM x",
    "DENY WRITE ON GRAPH * TO x",
    "SHOW USERS",
    "SHOW USER",
    "SHOW ROLES",
    "SHOW PRIVILEGES",
    "SHOW CURRENT USER",
    "LOAD CSV FROM 'file:///x.csv' AS row CREATE (n)",
    "USING PERIODIC COMMIT 500 LOAD CSV FROM 'x' AS r",
    "MATCH (n) FOREACH (x IN [1] | SET n.y = 1)",
    "CALL apoc.create.node(['L'],{}) YIELD node RETURN node",
    "CALL apoc.merge.node(['L'],{id:1},{},{}) YIELD node RETURN node",
    "CALL apoc.periodic.iterate('MATCH (n) RETURN n','DELETE n',{}) YIELD batches RETURN batches",
    "CALL apoc.trigger.add('t','MATCH (n) SET n.x=1',{}) YIELD name RETURN name",
    "CALL dbms.security.createUser('u','p',false)",
    "CALL db.createLabel('X')",
    "CALL apoc.cypher.doIt('CREATE (n)',{}) YIELD value RETURN value",
    "CALL apoc.cypher.runWrite('CREATE (n)',{}) YIELD value RETURN value",
    "CALL apoc.atomic.add(n,'x',1) YIELD newValue RETURN newValue",
    "MATCH (n) RETURN n; MATCH (m) DELETE m",  # stacked
    "MATCH (n) RETURN n LIMIT 1; DROP DATABASE neo4j",  # stacked ddl
    "  ",  # empty
    "TERMINATE TRANSACTIONS 'x'",
    "START DATABASE neo4j",
    "STOP DATABASE neo4j",
    "CALL gds.graph.drop('g')",  # not on allow-list
]

# --- Queries that MUST be allowed ----------------------------------------
ALLOWED = [
    "MATCH (n) RETURN n LIMIT 5",
    "MATCH (n {id:'x'})-[r]->(m) RETURN n,r,m LIMIT 10",
    "MATCH (n) WHERE n.name = 'DELETE me now' RETURN n LIMIT 1",  # literal DELETE
    "MATCH (n) WHERE n.note = 'please CREATE report' RETURN n.id LIMIT 1",
    "// SET this is a comment\nMATCH (n) RETURN count(n)",
    "/* DROP block */ MATCH (n) RETURN n LIMIT 3",
    "MATCH p=shortestPath((a {id:'x'})-[*1..5]-(b {id:'y'})) RETURN p",
    "CALL db.labels() YIELD label RETURN label",
    "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType",
    "CALL apoc.path.subgraphAll(s,{maxLevel:2}) YIELD nodes,relationships RETURN nodes,relationships",
    "MATCH (n) WITH n WHERE n.x > 1 RETURN n.id ORDER BY n.id LIMIT 20",
    "UNWIND [1,2,3] AS x RETURN x",
    "MATCH (n) RETURN n.`set` LIMIT 1",  # backtick-escaped prop named set
]


@pytest.mark.parametrize("q", FORBIDDEN)
def test_forbidden_rejected(q):
    res = check_read_only(q)
    assert res.ok is False, f"should be rejected: {q!r}"
    with pytest.raises(CypherWriteAttempt):
        validate_read_only(q)


@pytest.mark.parametrize("q", ALLOWED)
def test_allowed_accepted(q):
    res = check_read_only(q)
    assert res.ok is True, f"should be allowed: {q!r} -> {res.reason}"
    assert validate_read_only(q) == q


def test_enforce_limit_appends_when_missing():
    q = "MATCH (n) RETURN n"
    out = enforce_limit(q, 1000)
    assert out.rstrip().upper().endswith("LIMIT 1000")


def test_enforce_limit_noop_when_present():
    q = "MATCH (n) RETURN n LIMIT 7"
    assert enforce_limit(q, 1000) == q


def test_enforce_limit_ignores_limit_in_literal():
    q = "MATCH (n) WHERE n.t = 'LIMIT 5' RETURN n"
    out = enforce_limit(q, 1000)
    assert out.rstrip().upper().endswith("LIMIT 1000")
