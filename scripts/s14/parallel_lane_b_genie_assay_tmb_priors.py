#!/usr/bin/env python3
"""PARALLEL LANE B — GENIE assay-stratified tissue TMB prevalence priors.

NOT the genie_winners_mcp package (other agent owns MCP stubs).
Does NOT join GENIE IDs to PDS IPD (JOIN_IMPOSSIBLE).
Does NOT invent MSI / Guardant pTMB.
Does NOT soft-unblock 8D-04.

Usage:
  python scripts/s14/parallel_lane_b_genie_assay_tmb_priors.py \\
    --matrix datasets/genie_r20/crc_tmb_msi_matrix.parquet \\
    --out datasets/genie_r20/GENIE_ASSAY_TMB_PRIORS.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--matrix",
        type=Path,
        default=Path("datasets/genie_r20/crc_tmb_msi_matrix.parquet"),
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("datasets/genie_r20/GENIE_ASSAY_TMB_PRIORS.json"),
    )
    ap.add_argument("--min-n", type=int, default=100)
    args = ap.parse_args()

    try:
        import duckdb
    except ImportError:
        duckdb = None

    if duckdb is not None:
        con = duckdb.connect()
        q = f"""
        SELECT
          SEQ_ASSAY_ID,
          COUNT(*) AS n,
          SUM(CASE WHEN tmb_bin = 'High (>16)' THEN 1 ELSE 0 END) AS n_tmb_high,
          AVG(CASE WHEN tmb_bin = 'High (>16)' THEN 1.0 ELSE 0.0 END) AS tmb_high_rate,
          MEDIAN(tmb_mut_per_mb) AS median_tmb_mut_per_mb
        FROM read_parquet('{args.matrix.as_posix()}')
        GROUP BY 1
        HAVING COUNT(*) >= {int(args.min_n)}
        ORDER BY n DESC
        """
        rows = [dict(r) for r in con.execute(q).fetchdf().to_dict(orient="records")]
        n_crc = int(con.execute(f"SELECT COUNT(*) FROM read_parquet('{args.matrix.as_posix()}')").fetchone()[0])
        msi_any = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{args.matrix.as_posix()}') WHERE MSI_AVAILABLE = true"
        ).fetchone()[0]
    else:
        import pandas as pd

        df = pd.read_parquet(args.matrix)
        n_crc = len(df)
        msi_any = int(df["MSI_AVAILABLE"].fillna(False).astype(bool).sum()) if "MSI_AVAILABLE" in df.columns else 0
        rows = []
        for assay, g in df.groupby("SEQ_ASSAY_ID"):
            if len(g) < args.min_n:
                continue
            high = int((g["tmb_bin"] == "High (>16)").sum())
            rows.append(
                {
                    "SEQ_ASSAY_ID": assay,
                    "n": int(len(g)),
                    "n_tmb_high": high,
                    "tmb_high_rate": float(high / len(g)),
                    "median_tmb_mut_per_mb": float(g["tmb_mut_per_mb"].median()),
                }
            )
        rows = sorted(rows, key=lambda x: -x["n"])

    payload = {
        "built_utc": datetime.now(timezone.utc).isoformat(),
        "builder": "scripts/s14/parallel_lane_b_genie_assay_tmb_priors.py",
        "source_parquet": str(args.matrix),
        "sha256_parquet": sha256_file(args.matrix) if args.matrix.exists() else None,
        "n_crc": n_crc,
        "msi_available": bool(msi_any),
        "min_n": args.min_n,
        "assay_strata": rows,
        "assay_bias_note": (
            "TMB-high prevalence differs by SEQ_ASSAY_ID; pool only with assay stratification. "
            "tissue_panel_TMB only — NOT GuardantOMNI pTMB."
        ),
        "NOT_8D04_unblock": True,
        "NOT_patient_join_to_PDS": True,
        "RUO": "Research Use Only. Prevalence prior only.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str))
    print(f"wrote {args.out} assays={len(rows)} n_crc={n_crc}")


if __name__ == "__main__":
    main()
