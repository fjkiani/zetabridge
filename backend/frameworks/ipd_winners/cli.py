#!/usr/bin/env python3
"""CLI: run H1 anti-EGFR × KRAS WT held-out TreatMarkerEngine.

Example (from zetabridge repo root, Brenus sibling checkout):

  python -m backend.frameworks.ipd_winners.cli \\
    --backbone ../Brenus-repo/engagements/brenus/pds_extraction/crc_ipd_features_backbone_v5.csv \\
    --out ../Brenus-repo/engagements/brenus/genie_synapse/parallel_ipd_winners \\
    --seed 20260805
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as script without install
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.frameworks.ipd_winners.treat_marker_engine import run_h1_anti_egfr_kras


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="H1 TreatMarkerEngine held-out runner (RUO)")
    p.add_argument("--backbone", required=True, help="Path to crc_ipd_features_backbone_v5.csv")
    p.add_argument("--out", required=True, help="Output directory for receipts")
    p.add_argument("--seed", type=int, default=20260805, help="Pre-reg seed (must match W0)")
    args = p.parse_args(argv)

    receipt = run_h1_anti_egfr_kras(args.backbone, args.out, seed=args.seed)
    print(
        json.dumps(
            {
                "verdict": receipt.get("verdict"),
                "money": receipt.get("money"),
                "success_pass": (receipt.get("success_rule_eval") or {}).get("pass"),
                "receipt_sha256": receipt.get("receipt_sha256"),
                "interaction": (
                    (receipt.get("primary_heldout_cox_interaction") or {})
                    .get("coefs", {})
                    .get("treat_x_marker")
                ),
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
