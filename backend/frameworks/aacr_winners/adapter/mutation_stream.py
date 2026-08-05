"""DuckDB mutation flag stream — NEVER pandas-load the 990MB MAF."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from .poison import refuse_poison

DEFAULT_MUTATIONS = Path(
    "/Users/fahadkiani/Desktop/development/zetabridge/backend/data/features/"
    "genie_crc/raw/r20_public/data_mutations_extended.txt"
)

DRIVER_PACK_DEFAULT = (
    "KRAS",
    "NRAS",
    "BRAF",
    "TP53",
    "MLH1",
    "MSH2",
    "MSH6",
    "PMS2",
    "EPCAM",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class MutationFlagStream:
    """
    Stream gene presence flags for SAMPLE_IDs via DuckDB.

    Aligns conceptually with genie.stream_mutation_flags.
    """

    def __init__(self, mutation_path: Optional[str | Path] = None):
        self.mutation_path = Path(mutation_path) if mutation_path else DEFAULT_MUTATIONS

    def stream_flags(
        self,
        sample_ids: Optional[Sequence[str]] = None,
        genes: Sequence[str] = DRIVER_PACK_DEFAULT,
        out_path: Optional[str | Path] = None,
        limit_samples: Optional[int] = None,
    ) -> dict[str, Any]:
        refuse_poison(self.mutation_path)
        if not self.mutation_path.exists():
            return {
                "ok": False,
                "error": f"mutation file missing: {self.mutation_path}",
                "n": 0,
            }

        try:
            import duckdb
        except ImportError as e:
            return {"ok": False, "error": f"duckdb required: {e}", "n": 0}

        genes_u = [g.upper() for g in genes]
        gene_list_sql = ", ".join("'" + g.replace("'", "''") + "'" for g in genes_u)

        # Headered TSV; GENIE uses Tumor_Sample_Barcode + Hugo_Symbol
        path = str(self.mutation_path)
        con = duckdb.connect(database=":memory:")

        # Optional sample filter via temp table
        sample_filter_sql = ""
        if sample_ids is not None:
            ids = list(sample_ids)
            if limit_samples is not None:
                ids = ids[: int(limit_samples)]
            con.execute("CREATE TEMP TABLE sample_filter(SAMPLE_ID VARCHAR)")
            con.executemany(
                "INSERT INTO sample_filter VALUES (?)", [(s,) for s in ids]
            )
            sample_filter_sql = (
                "AND Tumor_Sample_Barcode IN (SELECT SAMPLE_ID FROM sample_filter)"
            )

        # Stream aggregate: sample × gene presence (no full materialize of MAF)
        q = f"""
        SELECT
          Tumor_Sample_Barcode AS SAMPLE_ID,
          UPPER(Hugo_Symbol) AS gene,
          COUNT(*) AS n_alts
        FROM read_csv_auto(?, delim='\t', header=true, sample_size=-1,
                           ignore_errors=true, parallel=true)
        WHERE UPPER(Hugo_Symbol) IN ({gene_list_sql})
          {sample_filter_sql}
        GROUP BY 1, 2
        """
        rel = con.execute(q, [path])
        rows = rel.fetchall()
        cols = [d[0] for d in rel.description]

        # Pivot to wide flags
        by_sample: dict[str, dict[str, int]] = {}
        for r in rows:
            sid, gene, n_alts = r[0], r[1], int(r[2])
            if sid is None:
                continue
            bucket = by_sample.setdefault(str(sid), {g: 0 for g in genes_u})
            if gene in bucket:
                bucket[gene] = 1 if n_alts > 0 else 0

        flag_rows = []
        for sid, flags in by_sample.items():
            rec = {"SAMPLE_ID": sid}
            rec.update({f"mut_{g}": int(flags.get(g, 0)) for g in genes_u})
            any_mmr = int(
                any(flags.get(g, 0) for g in ("MLH1", "MSH2", "MSH6", "PMS2", "EPCAM"))
            )
            rec["mut_MMR_any"] = any_mmr
            flag_rows.append(rec)

        artifacts: list[str] = []
        receipt_sha = _sha256_bytes(
            json.dumps(
                {"genes": genes_u, "n_flag_rows": len(flag_rows)}, sort_keys=True
            ).encode()
        )
        if out_path:
            outp = Path(out_path)
            refuse_poison(outp)
            outp.parent.mkdir(parents=True, exist_ok=True)
            # write JSONL (streaming-friendly artifact)
            with outp.open("w") as f:
                for rec in flag_rows:
                    f.write(json.dumps(rec) + "\n")
            artifacts.append(str(outp))
            receipt_sha = hashlib.sha256(outp.read_bytes()).hexdigest()

        # Prevalence among flagged samples
        prevalence = {}
        n = len(flag_rows)
        if n:
            for g in genes_u:
                key = f"mut_{g}"
                prevalence[g] = sum(r[key] for r in flag_rows) / n
            prevalence["MMR_any"] = sum(r["mut_MMR_any"] for r in flag_rows) / n

        return {
            "ok": True,
            "n": n,
            "n_raw_gene_sample_pairs": len(rows),
            "genes": genes_u,
            "prevalence_among_flagged_samples": prevalence,
            "flag_rows": flag_rows,
            "artifacts": artifacts,
            "receipt_sha": receipt_sha,
            "mutation_path": path,
            "method": "duckdb_stream_groupby_hugo_symbol",
            "warnings": [
                "Flags are gene-level presence (any alt), not allele-specific.",
                "Do not claim MSI from MMR gene flags alone.",
            ],
            "schema_version": "aacr-winners-framework/mutation_stream/0.1",
        }
