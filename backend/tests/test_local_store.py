"""Tests for backend/lineage/local_store.py"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def patch_lineage_db(tmp_path, monkeypatch):
    """Redirect lineage DB to a temp file for each test."""
    db_path = str(tmp_path / "test_lineage.sqlite")
    import config as cfg_module
    monkeypatch.setattr(cfg_module.cfg, "LINEAGE_DB_PATH", db_path)
    # Re-import to pick up patched path
    import importlib
    import lineage.local_store as ls
    importlib.reload(ls)
    ls.init_lineage_store()
    return db_path


def test_emit_and_retrieve_event():
    from lineage.local_store import DatasetRef, LineageEvent, emit_event, get_events
    event = LineageEvent(
        job_name="test.job",
        namespace="test",
        inputs=[DatasetRef("test", "table_a")],
        outputs=[],
    )
    run_id = emit_event(event)
    assert run_id == event.run_id

    events = get_events(job="test.job")
    assert len(events) == 1
    assert events[0]["job_name"] == "test.job"


def test_emit_idempotent():
    """Emitting the same run_id twice should not duplicate."""
    from lineage.local_store import LineageEvent, emit_event, get_events
    event = LineageEvent(job_name="test.dedup", namespace="test")
    emit_event(event)
    emit_event(event)  # second emit with same run_id
    events = get_events(job="test.dedup")
    assert len(events) == 1


def test_get_graph_returns_nodes_and_edges():
    from lineage.local_store import DatasetRef, LineageEvent, emit_event, get_graph
    event = LineageEvent(
        job_name="graph.job",
        namespace="test",
        inputs=[DatasetRef("test", "input_table")],
        outputs=[DatasetRef("test", "output_table")],
    )
    emit_event(event)
    graph = get_graph()
    assert "nodes" in graph
    assert "edges" in graph
    node_labels = [n["label"] for n in graph["nodes"]]
    assert "graph.job" in node_labels
    assert "input_table" in node_labels
    assert "output_table" in node_labels


def test_emit_query_lineage_convenience():
    from lineage.local_store import emit_query_lineage, get_events
    run_id = emit_query_lineage(
        job_name="query.test",
        sql="SELECT * FROM patients LIMIT 50;",
        input_tables=["patients"],
        output_table=None,
        source="duckdb",
    )
    assert run_id
    events = get_events(job="query.test")
    assert len(events) == 1
    assert events[0]["source"] == "duckdb"


def test_claim_lineage_stub_does_not_raise():
    from lineage.local_store import emit_claim_lineage
    result = emit_claim_lineage("claim-001", "source-001", "artifact-001")
    assert result == ""  # stub returns empty string


def test_artifact_lineage_stub_does_not_raise():
    from lineage.local_store import emit_artifact_lineage
    result = emit_artifact_lineage("artifact-001", "consumer-001")
    assert result == ""  # stub returns empty string
