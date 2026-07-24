"""Read-only Cypher validator for third-party agent access.

This module is the security boundary for the `/api/graph/cypher` endpoint and
the MCP `run_cypher_readonly` tool. It rejects any Cypher that could mutate the
graph, change schema/security, or exfiltrate credentials, and it enforces a
result-row LIMIT.

Design notes:
- String literals and comments are stripped BEFORE keyword matching, so a
  property value like ``'DELETE me'`` or ``// SET note`` does not trip the guard.
- Multiple / stacked statements are rejected (a semicolon separating two
  non-empty statements).
- Only an allow-list of read-only procedures may be CALLed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class CypherWriteAttempt(ValueError):
    """Raised when a Cypher string contains a forbidden (write/DDL/security) construct."""


@dataclass
class GuardResult:
    ok: bool
    reason: str = ""


# --- Forbidden bare keywords (clause-level writes / DDL) -------------------
# Matched as whole words on the *sanitized* query (literals + comments removed).
_FORBIDDEN_KEYWORDS = [
    "CREATE",
    "MERGE",
    "DELETE",
    "DETACH",
    "SET",
    "REMOVE",
    "DROP",
    "FOREACH",
    # data-loading / admin clauses
    "LOAD",          # LOAD CSV
    "PERIODIC",      # USING PERIODIC COMMIT
    "TERMINATE",     # TERMINATE TRANSACTION
    # DDL / security administration verbs
    "GRANT",
    "REVOKE",
    "DENY",
    "ALTER",
    "RENAME",
    "ENABLE",
    "START",         # START DATABASE (also legacy START)
    "STOP",
]

# Multi-word admin/security phrases (checked as regex on sanitized text).
_FORBIDDEN_PHRASES = [
    r"\bLOAD\s+CSV\b",
    r"\bUSING\s+PERIODIC\s+COMMIT\b",
    r"\bDETACH\s+DELETE\b",
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:INDEX|CONSTRAINT|DATABASE|USER|ROLE|ALIAS)\b",
    r"\bDROP\s+(?:INDEX|CONSTRAINT|DATABASE|USER|ROLE|ALIAS)\b",
    r"\bSHOW\s+USERS?\b",
    r"\bSHOW\s+ROLES?\b",
    r"\bSHOW\s+PRIVILEGES?\b",
    r"\bSHOW\s+(?:CURRENT\s+)?USER\b",
    r"\bALTER\s+(?:CURRENT\s+)?USER\b",
    r"\bDBMS\.SECURITY\b",
    r"\bCREATE\s+DATABASE\b",
]

# CALL <procedure> — only these read-only procedure namespaces/procs allowed.
# Everything else CALLed is rejected. We match the procedure token after CALL.
_ALLOWED_PROC_PREFIXES = (
    "db.labels",
    "db.relationshiptypes",
    "db.propertykeys",
    "db.schema",
    "db.info",
    "db.ping",
    "apoc.path",         # apoc.path.subgraphAll / expandConfig (read expansions)
    "apoc.meta",
    "apoc.coll",
    "apoc.text",
    "apoc.convert",
    "apoc.node",         # apoc.node.degree etc (read)
    "apoc.rel",
    "apoc.algo",         # apoc.algo.dijkstra / aStar (read)
    "apoc.map",
)

# Any CALLed procedure containing one of these tokens is a write/admin proc.
_FORBIDDEN_PROC_TOKENS = (
    ".create",
    ".merge",
    ".delete",
    ".remove",
    ".set",
    ".add",
    ".update",
    ".drop",
    ".import",
    ".export",
    ".load",
    ".install",
    ".restore",
    "dbms.security",
    "dbms.killquery",
    "dbms.killtransaction",
    "db.createlabel",
    "db.createproperty",
    "apoc.trigger",
    "apoc.periodic",
    "apoc.cypher.runwrite",
    "apoc.cypher.dowrite",
    "apoc.atomic",
)

_CALL_PROC_RE = re.compile(r"\bCALL\s+([a-zA-Z_][\w\.]*)", re.IGNORECASE)
_WORD_RE_CACHE: dict[str, re.Pattern] = {}


def _word_re(word: str) -> re.Pattern:
    p = _WORD_RE_CACHE.get(word)
    if p is None:
        p = re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
        _WORD_RE_CACHE[word] = p
    return p


def _sanitize(cypher: str) -> str:
    """Remove string literals and comments so keyword matching only sees code.

    Replaces the *contents* of single/double/backtick quoted spans and
    line/block comments with spaces (preserving overall structure lightly).
    """
    out = []
    i = 0
    n = len(cypher)
    while i < n:
        ch = cypher[i]
        # line comment //
        if ch == "/" and i + 1 < n and cypher[i + 1] == "/":
            j = cypher.find("\n", i)
            if j == -1:
                j = n
            out.append(" " * (j - i))
            i = j
            continue
        # block comment /* */
        if ch == "/" and i + 1 < n and cypher[i + 1] == "*":
            j = cypher.find("*/", i + 2)
            if j == -1:
                j = n
            else:
                j += 2
            out.append(" " * (j - i))
            i = j
            continue
        # quoted spans: ' " `
        if ch in ("'", '"', "`"):
            quote = ch
            j = i + 1
            while j < n:
                if cypher[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if cypher[j] == quote:
                    j += 1
                    break
                j += 1
            # keep the quote chars, blank the content
            span_len = j - i
            out.append(quote + " " * max(0, span_len - 2) + (quote if span_len >= 2 else ""))
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_statements(sanitized: str) -> list[str]:
    """Split on semicolons that are not inside (already-blanked) literals."""
    parts = [p.strip() for p in sanitized.split(";")]
    return [p for p in parts if p]


def check_read_only(cypher: str) -> GuardResult:
    """Return GuardResult(ok, reason). Does not raise."""
    if cypher is None or not str(cypher).strip():
        return GuardResult(False, "Empty query.")

    sanitized = _sanitize(str(cypher))

    # reject stacked statements
    statements = _split_statements(sanitized)
    if len(statements) > 1:
        return GuardResult(False, "Multiple statements are not allowed.")

    body = statements[0] if statements else sanitized.strip()

    # forbidden multi-word phrases
    for pat in _FORBIDDEN_PHRASES:
        if re.search(pat, body, re.IGNORECASE):
            return GuardResult(False, f"Forbidden construct: {pat}")

    # forbidden bare keywords
    for kw in _FORBIDDEN_KEYWORDS:
        if _word_re(kw).search(body):
            return GuardResult(False, f"Forbidden keyword: {kw}")

    # CALLed procedures must be on the allow-list and free of write tokens
    for m in _CALL_PROC_RE.finditer(body):
        proc = m.group(1).lower()
        # subquery CALL { ... } has no proc name -> _CALL_PROC_RE won't match "{"
        if any(tok in proc for tok in _FORBIDDEN_PROC_TOKENS):
            return GuardResult(False, f"Forbidden procedure: {proc}")
        if not any(proc.startswith(pfx) for pfx in _ALLOWED_PROC_PREFIXES):
            return GuardResult(False, f"Procedure not on read-only allow-list: {proc}")

    return GuardResult(True, "ok")


def validate_read_only(cypher: str) -> str:
    """Validate and return the query, or raise CypherWriteAttempt."""
    res = check_read_only(cypher)
    if not res.ok:
        raise CypherWriteAttempt(res.reason)
    return cypher


_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+\s*$", re.IGNORECASE)
_HAS_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+\b", re.IGNORECASE)


def enforce_limit(cypher: str, default_limit: int = 1000) -> str:
    """Append a LIMIT if the (sanitized) query has none.

    Uses the sanitized text to decide whether a real LIMIT clause exists, but
    appends to the ORIGINAL query text.
    """
    sanitized = _sanitize(str(cypher))
    if _HAS_LIMIT_RE.search(sanitized):
        return cypher
    q = cypher.rstrip().rstrip(";")
    return f"{q}\nLIMIT {int(default_limit)}"
