"""
Session 8: Pharmacovigilance + Trial Design Engine Runner
Executes W0-W4 pipeline for cross-endpoint analysis
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.api.holy_grail_api import (
    get_pv_signals,
    get_grade_escalation,
    get_trial_design_recommendations,
    get_gap_audit,
    holy_grail_query
)

def main():
    print("=" * 70)
    print("ZETA CUSTODIAN — SESSION 8 PHARMACOVIGILANCE ENGINE")
    print("=" * 70)
    
    # 1. Top PV signals
    print("\n[1] Top ROR Signals (min_ror=5.0)")
    signals = get_pv_signals(min_ror=5.0, limit=10)
    print(f"    Found {signals['filtered_count']} signals above ROR=5.0")
    for s in signals['signals'][:3]:
        print(f"    {s.get('kg_node_id', 'N/A')}: ROR={s.get('ror', 0):.3f}")
    
    # 2. Grade escalation
    print("\n[2] Grade Escalation Signals (>80% grade 3+)")
    grade = get_grade_escalation(min_pct_grade3plus=0.8)
    print(f"    Found {grade['filtered_count']} critical escalation signals")
    
    # 3. HGSOC recommendations
    print("\n[3] HGSOC Trial Design Recommendations")
    recs = get_trial_design_recommendations(
        cancer_type="HGSOC",
        genomic_markers=["BRCA1", "BRCA2", "PIK3CA", "NF1", "CDK12"]
    )
    print(f"    Generated {len(recs['hgsoc_genomic_recommendations'])} HGSOC-specific recommendations")
    
    # 4. Gap audit
    print("\n[4] Knowledge Gap Audit")
    gaps = get_gap_audit()
    print(f"    Total gaps: {gaps['total_gaps']}")
    print(f"    By status: {gaps['by_status']}")
    
    # 5. Holy Grail query
    print("\n[5] Holy Grail Cross-Endpoint Query")
    hg = holy_grail_query(
        "What are the highest-priority safety signals for HGSOC trial design?",
        context_type="full"
    )
    print(f"    Cross-endpoint signals: {len(hg['cross_endpoint_signals'])}")
    print(f"    KG stats: {hg['kg_stats']}")
    
    print("\n✓ Session 8 Pharmacovigilance Engine — COMPLETE")
    print("=" * 70)

if __name__ == "__main__":
    main()
