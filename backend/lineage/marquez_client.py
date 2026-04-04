"""Marquez OpenLineage HTTP client (RunEvent shape per OpenLineage 1.x)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import requests

from config import cfg

log = logging.getLogger("zetabridge.marquez")

# Marquez accepts standard OpenLineage RunEvent JSON; producer should be a stable URL string.
_OL_PRODUCER = "https://github.com/OpenLineage/OpenLineage/tree/main/spec/OpenLineage.json"


def _event_time_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def emit_query_lineage(
    job_name: str,
    sql: str,
    input_tables: list[str],
    output_table: str | None,
    source: str,
) -> bool:
    run_id = str(uuid.uuid4())
    namespace = "zetabridge." + source

    inputs: list[dict[str, str]] = [
        {"namespace": namespace, "name": t} for t in input_tables if t
    ]
    if not inputs:
        inputs = [{"namespace": namespace, "name": "adhoc_query_source"}]

    outputs: list[dict[str, str]] = []
    if output_table:
        outputs.append({"namespace": namespace, "name": output_table})

    event: dict[str, Any] = {
        "eventType": "COMPLETE",
        "eventTime": _event_time_z(),
        "run": {"runId": run_id},
        "job": {"namespace": namespace, "name": job_name},
        "inputs": inputs,
        "outputs": outputs,
        "producer": _OL_PRODUCER,
    }
    if sql:
        log.debug("Marquez lineage SQL (not sent as facet): %s", sql[:500])

    url = cfg.MARQUEZ_URL + "/api/v1/lineage"
    try:
        r = requests.post(
            url,
            json=event,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if r.ok:
            log.debug("Marquez lineage accepted runId=%s status=%s", run_id, r.status_code)
            return True
        log.warning(
            "Marquez lineage rejected status=%s body=%s",
            r.status_code,
            (r.text or "")[:500],
        )
        return False
    except Exception as exc:
        log.warning("Marquez emit_query_lineage failed: %s", exc)
        return False


def get_lineage_graph(dataset: str, source: str) -> dict[str, Any]:
    namespace = "zetabridge." + source
    node_id = "dataset:" + namespace + ":" + dataset
    try:
        url = cfg.MARQUEZ_URL + "/api/v1/lineage"
        resp = requests.get(url, params={"nodeId": node_id, "depth": 5}, timeout=15)
        if resp.ok:
            return resp.json()
        log.warning("Marquez get_lineage_graph HTTP %s: %s", resp.status_code, (resp.text or "")[:300])
    except Exception as exc:
        log.warning("Marquez get_lineage_graph failed: %s", exc)
    return {"nodes": [], "edges": []}
