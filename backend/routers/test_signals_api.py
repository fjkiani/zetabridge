"""Tests for the Session-14 signal layer: grounded service values, /api/signals
router, the 3 agents, and the fabrication guard.

Run:
  cd backend && env NEO4J_URI=... NEO4J_USER=... NEO4J_PASSWORD=... \
      ZETA_GRAPH_API_KEY=test-key-abc123 \
      python3 -m pytest routers/test_signals_api.py -q
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API_KEY = os.environ.get("ZETA_GRAPH_API_KEY", "test-key-abc123")
HDR = {"X-Zeta-Api-Key": API_KEY}
LIVE = bool(os.environ.get("NEO4J_URI"))
requires_live = pytest.mark.skipif(not LIVE, reason="needs live Neo4j creds in env")


@pytest.fixture(scope="module")
def svc():
    from federation.signal_service import SignalService
    s = SignalService.from_env()
    yield s
    s.close()


@pytest.fixture(scope="module")
def client():
    from routers import signals as signals_router
    app = FastAPI()
    app.include_router(signals_router.router)
    return TestClient(app)


# ── service: grounded values match the live graph ──────────────────────────────

@requires_live
def test_overview_totals(svc):
    ov = svc.overview()
    assert ov["totals"]["nodes"] == 30904
    assert ov["totals"]["relationships"] == 102760
    assert ov["n_signals"] >= 300  # 128+20+20+104+39 = 311
    assert ov["n_blind_spots"] >= 10
    assert ov["headline"]["cross_endpoint_reachability"]["all_100pct"] is True
    assert ov["headline"]["top_broker"]["node"] == "vault:synapse_msk_spectrum"
    assert abs(ov["headline"]["top_broker"]["betweenness"] - 0.36199) < 1e-3


@requires_live
def test_top_drug_ae_rate_ratio(svc):
    res = svc.top_signals("drug_ae", 5)
    assert res["count"] == 128
    top = res["signals"][0]
    assert top["native_metric"] == "rate_ratio"
    assert top["native_value"] == 999.0  # capped absolute enrichment
    assert top["endpoint"] == "B_SAS"


@requires_live
def test_top_pharmacovig_ror(svc):
    res = svc.top_signals("pharmacovig", 3)
    top = res["signals"][0]
    assert top["native_metric"] == "ror"
    assert abs(top["native_value"] - 51.323) < 0.01
    assert "SKIN RASH" in top["name"].upper()


@requires_live
def test_bridges_brca1_neutropenia(svc):
    br = svc.bridges()
    assert br["count"] == 20
    top = br["bridges"][0]
    assert top["bridge_score"] == 0.63
    assert top["ae_term"] == "NEUTROPENIA"
    assert top["gene"] in ("BRCA1", "CDK12")
    # cross-endpoint: genomic (A_MSK) node -> AE (B_SAS) node
    assert top["gene_node"]["endpoint"] == "A_MSK"
    assert top["ae_node"]["endpoint"] == "B_SAS"
    assert len(top["path"]) == 3


@requires_live
def test_cross_trial_consistency(svc):
    res = svc.top_signals("cross_trial", 3)
    top = res["signals"][0]
    assert top["native_metric"] == "consistency_score"
    assert abs(top["native_value"] - 25.2) < 0.1
    assert top["attrs"].get("ae_term") == "NEUTROPENIA"


@requires_live
def test_gaps_present(svc):
    gp = svc.gaps()
    assert gp["count"] >= 10
    names = " ".join(g["name"] or "" for g in gp["gaps"]).lower()
    assert "coding heterogeneity" in names or "meddra" in names


@requires_live
def test_signal_detail_resolves_connections(svc):
    d = svc.signal_detail("genomic_ae_bridge:s11:BRCA1:NEUTROPENIA")
    assert d is not None
    ids = {n["id"] for n in d["connections"]["nodes"]}
    assert "genomicfeature:msk:mut:BRCA1" in ids
    assert "ae:pt:neutropenia" in ids
    assert len(d["connections"]["edges"]) >= 2


# ── router: endpoints + auth ───────────────────────────────────────────────────

@requires_live
def test_auth_required(client):
    assert client.get("/api/signals/overview").status_code == 401
    assert client.get("/api/signals/overview", headers={"X-Zeta-Api-Key": "wrong"}).status_code == 401


@requires_live
def test_router_overview(client):
    r = client.get("/api/signals/overview", headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json()["totals"]["nodes"] == 30904


@requires_live
def test_router_top_and_bridges_and_gaps(client):
    assert client.get("/api/signals/top?family=drug_ae&limit=3", headers=HDR).status_code == 200
    assert client.get("/api/signals/bridges", headers=HDR).status_code == 200
    assert client.get("/api/signals/gaps", headers=HDR).status_code == 200
    # bad family rejected
    assert client.get("/api/signals/top?family=bogus", headers=HDR).status_code == 400


@requires_live
def test_router_detail_route_not_shadowed(client):
    # static route still works (not shadowed by /{slug:path})
    assert client.get("/api/signals/overview", headers=HDR).status_code == 200
    # slug detail works
    r = client.get("/api/signals/genomic_ae_bridge:s11:BRCA1:NEUTROPENIA", headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json()["name"].startswith("Bridge: BRCA1")


# ── agents: grounded envelope + fabrication guard ──────────────────────────────

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@requires_live
def test_agents_return_grounding(svc):
    from agents.signal_agents import build_signal_agents
    from agents.base import AgentContext
    agents = build_signal_agents(svc)
    for name, task in [
        ("signal_miner", {"action": "rank", "family": "all", "limit": 5}),
        ("bridge_hunter", {"limit": 5}),
        ("gap_auditor", {}),
    ]:
        r = _run(agents[name].run(AgentContext(), task))
        assert r.status.value == "success", r.error
        assert "summary" in r.output and r.output["summary"]
        assert isinstance(r.output["grounding"], list) and len(r.output["grounding"]) > 0
        # deterministic path when no GROQ key
        if not os.environ.get("GROQ_API_KEY"):
            assert r.output["used_llm"] is False


@requires_live
def test_fabrication_guard(svc):
    """Every number in an agent summary must be grounded in its findings."""
    from agents.signal_agents import build_signal_agents, _numbers_in, _grounded_numbers
    from agents.base import AgentContext
    agents = build_signal_agents(svc)
    r = _run(agents["bridge_hunter"].run(AgentContext(), {"limit": 8}))
    grounded = _grounded_numbers(r.output["findings"])
    for tok in _numbers_in(r.output["summary"]):
        # allow small integers (<=31) used for phrasing
        if tok in grounded:
            continue
        f = float(tok)
        assert 0 <= f <= 31 and f == int(f), f"ungrounded number in summary: {tok}"


def test_fabrication_guard_unit():
    """Guard rejects an injected fake number, accepts grounded ones."""
    from agents.signal_agents import _fabrication_ok
    findings = [{"bridge_score": 0.63, "name": "BRCA1 -> NEUTROPENIA"}]
    assert _fabrication_ok("The score is 0.63 for this bridge.", findings) is True
    assert _fabrication_ok("The score is 0.99 for this bridge.", findings) is False
    # small integers allowed for phrasing
    assert _fabrication_ok("There are 3 nodes in this path.", findings) is True
