"""GENIE CRC matrix adapter — parquet only (never full-load 990MB mutations)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional

import pyarrow.parquet as pq

from .poison import refuse_poison

DEFAULT_MATRIX = Path(
    "/Users/fahadkiani/Desktop/development/zetabridge/datasets/genie_r20/"
    "crc_tmb_msi_matrix.parquet"
)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def probe_msi_columns(column_names: list[str]) -> dict[str, Any]:
    """Probe only — do not invent MSI. Columns may exist but be all-null."""
    msi_like = [c for c in column_names if "msi" in c.lower() or "mmr" in c.lower()]
    return {
        "msi_like_columns": msi_like,
        "msi_status_column_present": "MSI_STATUS" in column_names,
        "msi_available_column_present": "MSI_AVAILABLE" in column_names,
    }


class GenieMatrixAdapter:
    """load_cohort() over crc_tmb_msi_matrix.parquet."""

    def __init__(self, matrix_path: Optional[str | Path] = None):
        self.matrix_path = Path(matrix_path) if matrix_path else DEFAULT_MATRIX

    def load_cohort(self) -> dict[str, Any]:
        refuse_poison(self.matrix_path)
        if not self.matrix_path.exists():
            raise FileNotFoundError(f"matrix missing: {self.matrix_path}")

        table = pq.read_table(self.matrix_path)
        cols = list(table.column_names)
        msi_probe = probe_msi_columns(cols)

        # Truthful MSI availability: column may exist as null placeholder
        msi_available = False
        msi_non_null = 0
        if "MSI_AVAILABLE" in cols:
            avail = table.column("MSI_AVAILABLE").to_pylist()
            # True only if any True
            msi_available = any(bool(x) for x in avail if x is not None)
        if "MSI_STATUS" in cols:
            status = table.column("MSI_STATUS").to_pylist()
            msi_non_null = sum(1 for x in status if x is not None and str(x).strip() != "")

        # Explicit honesty: tissue panel TMB label
        is_guardant = False
        if "IS_GUARDANT_PTMB" in cols:
            vals = table.column("IS_GUARDANT_PTMB").to_pylist()
            is_guardant = any(bool(x) for x in vals if x is not None)

        tmb_label = "tissue_panel_TMB"
        if "TMB_ASSAY_TYPE" in cols:
            # take modal non-null if present
            types = [x for x in table.column("TMB_ASSAY_TYPE").to_pylist() if x]
            if types:
                tmb_label = str(types[0])

        n = table.num_rows
        sha = sha256_file(self.matrix_path)

        return {
            "ok": True,
            "n": n,
            "matrix_path": str(self.matrix_path),
            "sha256_parquet": sha,
            "columns": cols,
            "table": table,  # pyarrow Table — plays may convert needed cols only
            "msi_probe": msi_probe,
            "msi_available": bool(msi_available) and msi_non_null > 0,
            "msi_non_null": msi_non_null,
            "tmb_label": tmb_label,
            "IS_GUARDANT_PTMB": bool(is_guardant),
            "NOT_guardant_ptmb": not bool(is_guardant),
            "NOT_8D04_unblock": True,
            "warnings": (
                []
                if (not is_guardant and msi_non_null == 0)
                else (
                    (["IS_GUARDANT_PTMB true in matrix — investigate"] if is_guardant else [])
                    + (
                        [f"MSI_STATUS non-null={msi_non_null}"]
                        if msi_non_null
                        else []
                    )
                )
            ),
        }
