"""
CRC IPD lifespan seed — hash-gated upsert of crc_ipd_harmonized_v3 into Postgres.

Self-contained for Render (rootDir=backend): does NOT import scripts/pds.

Env:
  CRC_IPD_SEED_ON_BOOT=1     — enable (default off; render.yaml sets 1)
  CRC_IPD_DSN / DATABASE_URL — connection string
  CRC_IPD_CSV_PATH           — optional override of CSV path
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Sequence

log = logging.getLogger("crc_ipd_seed")

_BACKEND = Path(__file__).resolve().parents[1]
_DEFAULT_CSV = _BACKEND / "resources" / "crc_ipd" / "crc_ipd_harmonized_v3.csv"
# Fallback when developing against ingest out-dir (gitignored)
_FALLBACK_CSV = _BACKEND / "data" / "features" / "crc_ipd_from_zips" / "crc_ipd_harmonized_v3.csv"

SCHEMA_COLS: List[str] = [
    "subjid",
    "trial",
    "arm",
    "arm_class",
    "liver_met",
    "kras",
    "nras",
    "ras",
    "braf_mut",
    "ecog",
    "age",
    "sex",
    "pfs_days",
    "pfs_event",
    "os_days",
    "os_event",
    "liver_met_missing",
    "kras_missing",
    "nras_missing",
    "ras_missing",
    "subset_scale",
    "published_n",
    "available_n",
    "pack_role",
]


def _resolve_csv() -> Path | None:
    override = os.getenv("CRC_IPD_CSV_PATH")
    if override:
        p = Path(override)
        return p if p.exists() else None
    if _DEFAULT_CSV.exists():
        return _DEFAULT_CSV
    if _FALLBACK_CSV.exists():
        return _FALLBACK_CSV
    return None


def _content_hash(df_bytes: bytes) -> str:
    return hashlib.sha256(df_bytes).hexdigest()


def _ensure_contract_cols(df: Any) -> Any:
    import pandas as pd

    for c in SCHEMA_COLS:
        if c in df.columns:
            continue
        if c.endswith("_missing"):
            base = c.replace("_missing", "")
            df[c] = df[base].isna().astype(float) if base in df.columns else 1.0
        elif c == "subset_scale":
            df[c] = 1.0
        elif c == "pack_role":
            df[c] = "full_ipd"
        else:
            df[c] = None
    return df[SCHEMA_COLS]


def write_postgres(df: Any, dsn: str, table: str = "crc_ipd_harmonized_v3") -> Dict[str, Any]:
    """Upsert with content-hash skip. Uses psycopg2 (requirements.txt)."""
    import pandas as pd
    import psycopg2
    from psycopg2.extras import execute_batch

    df = _ensure_contract_cols(df)
    # Stable hash over CSV round-trip of schema cols
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    h = _content_hash(csv_bytes)

    ddl = f"""
    CREATE TABLE IF NOT EXISTS {table} (
      subjid text,
      trial text,
      arm text,
      arm_class text,
      liver_met double precision,
      kras double precision,
      nras double precision,
      ras double precision,
      braf_mut double precision,
      ecog double precision,
      age double precision,
      sex text,
      pfs_days double precision,
      pfs_event double precision,
      os_days double precision,
      os_event double precision,
      liver_met_missing double precision,
      kras_missing double precision,
      nras_missing double precision,
      ras_missing double precision,
      subset_scale double precision,
      published_n double precision,
      available_n double precision,
      pack_role text,
      PRIMARY KEY (trial, subjid)
    );
    CREATE TABLE IF NOT EXISTS {table}_meta (
      key text PRIMARY KEY,
      value text,
      updated_at timestamptz DEFAULT now()
    );
    """
    cols: Sequence[str] = SCHEMA_COLS
    update_set = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in ("trial", "subjid"))
    insert_sql = f"""
    INSERT INTO {table} ({", ".join(cols)})
    VALUES ({", ".join(["%s"] * len(cols))})
    ON CONFLICT (trial, subjid) DO UPDATE SET {update_set}
    """

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(ddl)
            cur.execute(f"SELECT value FROM {table}_meta WHERE key = 'content_sha256'")
            row = cur.fetchone()
            if row and row[0] == h:
                return {
                    "postgres": "skipped",
                    "reason": "content_hash_match",
                    "content_sha256": h,
                    "table": table,
                }

            batch: List[tuple] = []
            n = 0
            for tup in df[list(cols)].itertuples(index=False, name=None):
                batch.append(tuple(None if pd.isna(x) else x for x in tup))
                if len(batch) >= 2000:
                    execute_batch(cur, insert_sql, batch, page_size=500)
                    n += len(batch)
                    batch = []
            if batch:
                execute_batch(cur, insert_sql, batch, page_size=500)
                n += len(batch)

            cur.execute(
                f"""
                INSERT INTO {table}_meta (key, value) VALUES ('content_sha256', %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                """,
                (h,),
            )
            cur.execute(
                f"""
                INSERT INTO {table}_meta (key, value) VALUES ('n_rows', %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                """,
                (str(n),),
            )
        conn.commit()
    finally:
        conn.close()

    return {"postgres": "ok", "table": table, "upserted": n, "content_sha256": h}


def seed_crc_ipd_if_configured() -> Dict[str, Any]:
    if os.getenv("CRC_IPD_SEED_ON_BOOT", "0") != "1":
        return {"crc_ipd_seed": "skipped", "reason": "CRC_IPD_SEED_ON_BOOT!=1"}

    dsn = os.getenv("CRC_IPD_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        return {"crc_ipd_seed": "skipped", "reason": "no DATABASE_URL/CRC_IPD_DSN"}

    csv_path = _resolve_csv()
    if not csv_path:
        return {
            "crc_ipd_seed": "skipped",
            "reason": f"missing csv (tried {_DEFAULT_CSV} and {_FALLBACK_CSV})",
        }

    try:
        import pandas as pd

        df = pd.read_csv(csv_path)
        result = write_postgres(df, dsn)
        log.info("CRC IPD seed from %s: %s", csv_path, result)
        return {"crc_ipd_seed": result, "csv": str(csv_path)}
    except Exception as e:
        log.exception("CRC IPD seed failed")
        return {"crc_ipd_seed": "error", "error": str(e), "csv": str(csv_path)}
