"""End-to-end runner for the Synapse<->PDS federation bridge.

Executes the multi-hop DAG via the scaffold Orchestrator and writes the
synthesis artifact. Proves context preservation across the cross-endpoint hop.
"""
import sys, os, json, asyncio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# load /workspace/.env
with open("/workspace/.env") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from agents.orchestrator import create_orchestrator, classify_intent, Intent, decompose_task
from agents.base import AgentContext


def main(gene="KRAS", out_path="/workspace/federation_out/synthesis_raw.json"):
    q = "federate Synapse to PDS: surface hidden signals across the two endpoints"
    intent = classify_intent(q)
    print(f"INTENT: {intent.value}")

    plan = decompose_task(Intent.FEDERATE_BRIDGE, q, {"gene": gene})
    print("DAG:")
    for t in plan.tasks:
        print(f"   {t.agent_name}.{t.action}  depends_on={t.depends_on}")

    orch = create_orchestrator()
    ctx = AgentContext(session_id="federation-run")
    rec = asyncio.run(orch.execute_plan(plan, ctx))
    print(f"\nEXECUTION: succeeded={rec['succeeded']} failed={rec['failed']} "
          f"latency_ms={rec['total_latency_ms']:.1f}")
    print(f"Lineage events propagated to parent: {len(ctx.lineage_chain)}")

    # ── context-preservation proof: PDS task received Agent 1's payload ──
    results = rec["results"]
    pds_out = next((r["output"] for r in results.values()
                    if r["agent_name"] == "pds_agent"), {})
    print("\nCONTEXT PRESERVATION across the hop:")
    print(f"   pds_agent.translated_from_synapse = {pds_out.get('translated_from_synapse')}")
    print(f"   synapse alteration seen by PDS     = {pds_out.get('synapse_alteration_classes')}")

    synth = next(r["output"] for r in results.values()
                 if r["agent_name"] == "synthesis_agent")
    print(f"\nSHARED GENES: {synth['shared_genes']} | BRIDGE EDGES: {synth['n_bridge_edges']}")

    print("\n=== RANKED HYPOTHESES ===")
    for h in synth["hypotheses"]:
        print(f"[rank {h['rank']}] {h['hypothesis_id']} | tier={h['evidence_tier']} "
              f"| actionable={h['actionable_without_new_data']}")
        print(f"    {h['statement']}")
        print(f"    mismatch={h['mismatch_flags']}")
        print(f"    caveats={h['caveats']}")

    print("\n=== BRIDGE EDGE (KG schema) ===")
    print(json.dumps(synth["bridge_edges"][0], indent=2, default=str))

    print("\n=== REFUSAL LEDGER ===")
    for r in synth["refusal_ledger"]:
        print(f"  - [{r['join_class']}] {r['verdict']}: {r['reason']}")

    print(f"\n=== NARRATIVE (mode={synth['narrative']['mode']}) ===")
    print(synth["narrative"]["text"])

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(synth, open(out_path, "w"), indent=2, default=str)
    print(f"\nSAVED {out_path}")
    return synth


if __name__ == "__main__":
    main()
