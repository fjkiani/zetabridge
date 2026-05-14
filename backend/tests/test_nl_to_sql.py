"""
Tests for backend/agents/nl_to_sql.py

These tests cover the pure-Python logic (sanitizer, schema builder, heuristic fallback,
DuckDB executor) without requiring Groq or HuggingFace API keys.
"""

import sys
import os
import pytest
import duckdb

# Ensure backend/ is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.nl_to_sql import (
    ColumnSpec,
    TableSpec,
    UnsafeSQLError,
    _heuristic_fallback,
    build_schema_context,
    execute_duckdb,
    parse_and_sanitize,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_tables():
    return [
        TableSpec(
            name="patients",
            description="Patient clinical records",
            columns=[
                ColumnSpec("patient_id", "TEXT", "Primary key"),
                ColumnSpec("cancer_type", "TEXT", "e.g. BRCA, COAD"),
                ColumnSpec("age", "INT"),
                ColumnSpec("response", "TEXT", "CR/PR/SD/PD"),
            ],
        ),
        TableSpec(
            name="variants",
            description="Somatic variant calls",
            columns=[
                ColumnSpec("variant_id", "TEXT"),
                ColumnSpec("patient_id", "TEXT", "FK → patients"),
                ColumnSpec("gene", "TEXT"),
                ColumnSpec("vaf", "FLOAT"),
            ],
        ),
    ]


@pytest.fixture
def in_memory_db(tmp_path):
    """Create a temporary DuckDB with a fixture table."""
    db_path = str(tmp_path / "test.duckdb")
    con = duckdb.connect(db_path)
    con.execute("""
        CREATE TABLE patients (
            patient_id TEXT PRIMARY KEY,
            cancer_type TEXT,
            age INT,
            response TEXT
        )
    """)
    con.execute("""
        INSERT INTO patients VALUES
            ('P001', 'BRCA', 45, 'CR'),
            ('P002', 'COAD', 62, 'PR'),
            ('P003', 'GBM', 55, 'PD')
    """)
    con.close()
    return db_path


# ── parse_and_sanitize ────────────────────────────────────────────────────────

def test_parse_and_sanitize_strips_fences():
    raw = "```sql\nSELECT * FROM patients\n```"
    sql = parse_and_sanitize(raw)
    assert "```" not in sql
    assert sql.startswith("SELECT")
    assert sql.endswith(";")


def test_parse_and_sanitize_strips_fences_no_lang():
    raw = "```\nSELECT patient_id FROM patients\n```"
    sql = parse_and_sanitize(raw)
    assert "```" not in sql
    assert "SELECT" in sql


def test_parse_and_sanitize_adds_semicolon():
    sql = parse_and_sanitize("SELECT * FROM patients")
    assert sql.endswith(";")


def test_parse_and_sanitize_single_semicolon():
    """Should not double-add semicolons."""
    sql = parse_and_sanitize("SELECT * FROM patients;")
    assert sql.count(";") == 1


def test_parse_and_sanitize_injects_limit():
    sql = parse_and_sanitize("SELECT * FROM patients")
    assert "LIMIT" in sql.upper()


def test_parse_and_sanitize_preserves_existing_limit():
    sql = parse_and_sanitize("SELECT * FROM patients LIMIT 10")
    assert sql.upper().count("LIMIT") == 1


def test_parse_and_sanitize_rejects_drop():
    with pytest.raises(UnsafeSQLError, match="disallowed"):
        parse_and_sanitize("DROP TABLE patients")


def test_parse_and_sanitize_rejects_delete():
    with pytest.raises(UnsafeSQLError, match="disallowed"):
        parse_and_sanitize("DELETE FROM patients WHERE 1=1")


def test_parse_and_sanitize_rejects_insert():
    with pytest.raises(UnsafeSQLError, match="disallowed"):
        parse_and_sanitize("INSERT INTO patients VALUES ('X', 'Y', 1, 'Z')")


def test_parse_and_sanitize_rejects_update():
    with pytest.raises(UnsafeSQLError, match="disallowed"):
        parse_and_sanitize("UPDATE patients SET age = 99")


def test_parse_and_sanitize_rejects_create():
    with pytest.raises(UnsafeSQLError, match="disallowed"):
        parse_and_sanitize("CREATE TABLE foo (id INT)")


def test_parse_and_sanitize_rejects_truncate():
    with pytest.raises(UnsafeSQLError, match="disallowed"):
        parse_and_sanitize("TRUNCATE TABLE patients")


def test_parse_and_sanitize_rejects_non_select():
    with pytest.raises(UnsafeSQLError, match="SELECT or WITH"):
        parse_and_sanitize("EXPLAIN SELECT * FROM patients")


def test_parse_and_sanitize_allows_with_cte():
    sql = parse_and_sanitize(
        "WITH cte AS (SELECT * FROM patients) SELECT * FROM cte"
    )
    assert sql.startswith("WITH")
    assert sql.endswith(";")


def test_parse_and_sanitize_rejects_empty():
    with pytest.raises(UnsafeSQLError):
        parse_and_sanitize("")


def test_parse_and_sanitize_rejects_fenced_empty():
    with pytest.raises(UnsafeSQLError):
        parse_and_sanitize("```sql\n\n```")


# ── heuristic_fallback ────────────────────────────────────────────────────────

def test_heuristic_fallback_returns_valid_select(sample_tables):
    sql = _heuristic_fallback("show me all patients", sample_tables)
    assert sql.startswith("SELECT")
    assert "patients" in sql
    assert "LIMIT" in sql.upper()


def test_heuristic_fallback_picks_best_table(sample_tables):
    sql = _heuristic_fallback("list all gene variants with high vaf", sample_tables)
    assert "variants" in sql


def test_heuristic_fallback_never_raises(sample_tables):
    """Heuristic fallback must always return a valid SQL string."""
    sql = _heuristic_fallback("xyzzy nonsense query that matches nothing", sample_tables)
    assert sql.startswith("SELECT")
    assert sql.endswith(";")


def test_heuristic_fallback_empty_tables():
    """With no tables, should still return something safe."""
    sql = _heuristic_fallback("any question", [])
    assert sql.startswith("SELECT")


# ── build_schema_context ──────────────────────────────────────────────────────

def test_schema_context_builder_produces_prompt_with_all_columns(sample_tables):
    context = build_schema_context(sample_tables)
    assert "patients" in context
    assert "variants" in context
    assert "patient_id" in context
    assert "cancer_type" in context
    assert "gene" in context
    assert "vaf" in context


def test_schema_context_includes_descriptions(sample_tables):
    context = build_schema_context(sample_tables)
    assert "Patient clinical records" in context
    assert "Somatic variant calls" in context


def test_schema_context_includes_column_types(sample_tables):
    context = build_schema_context(sample_tables)
    assert "TEXT" in context
    assert "INT" in context
    assert "FLOAT" in context


# ── execute_duckdb ────────────────────────────────────────────────────────────

def test_execute_duckdb_returns_expected_shape(in_memory_db):
    result = execute_duckdb("SELECT * FROM patients LIMIT 50;", in_memory_db)
    assert "columns" in result
    assert "rows" in result
    assert "row_count" in result
    assert result["row_count"] == 3
    assert set(result["columns"]) == {"patient_id", "cancer_type", "age", "response"}


def test_execute_duckdb_returns_dicts(in_memory_db):
    result = execute_duckdb("SELECT * FROM patients LIMIT 50;", in_memory_db)
    assert isinstance(result["rows"][0], dict)
    assert result["rows"][0]["patient_id"] == "P001"


def test_execute_duckdb_filtered_query(in_memory_db):
    result = execute_duckdb(
        "SELECT * FROM patients WHERE response = 'CR' LIMIT 50;", in_memory_db
    )
    assert result["row_count"] == 1
    assert result["rows"][0]["patient_id"] == "P001"


def test_execute_duckdb_aggregation(in_memory_db):
    result = execute_duckdb(
        "SELECT cancer_type, COUNT(*) as cnt FROM patients GROUP BY cancer_type LIMIT 50;",
        in_memory_db,
    )
    assert result["row_count"] == 3
    assert "cnt" in result["columns"]


def test_execute_duckdb_raises_on_bad_sql(in_memory_db):
    with pytest.raises(Exception):
        execute_duckdb("SELECT * FROM nonexistent_table LIMIT 50;", in_memory_db)
