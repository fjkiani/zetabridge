"""Seed DuckDB with biotech/oncology research data.

Creates tables modeled on real TCGA/GDC schemas:
- tcga_clinical: Patient demographics + clinical outcomes (TCGA-OV, TCGA-BRCA)
- genomic_variants: Somatic mutations in DDR pathway genes
- hrd_scores: Homologous recombination deficiency scores per patient
- drug_responses: PARP inhibitor + platinum response data
- synthetic_lethality: Gene-pair synthetic lethality from DepMap CRISPR screens

All data is synthetic but structurally faithful to real research schemas.
"""
from __future__ import annotations

import logging
import random
import uuid
from datetime import date, timedelta

import duckdb

from config import cfg

log = logging.getLogger("zetabridge.seed_biotech")

random.seed(42)  # reproducible

# ── Helpers ────────────────────────────────────────────────────────────────

def _rand_date(start_year: int = 2015, end_year: int = 2024) -> str:
    start = date(start_year, 1, 1)
    delta = (date(end_year, 12, 31) - start).days
    return (start + timedelta(days=random.randint(0, delta))).isoformat()

def _rand_id() -> str:
    return "TCGA-" + uuid.uuid4().hex[:8].upper()


# ── Gene / Drug Constants ─────────────────────────────────────────────────

DDR_GENES = ["BRCA1", "BRCA2", "ATM", "ATR", "PALB2", "RAD51C", "RAD51D",
             "CHEK2", "CDK12", "TP53", "PTEN", "FANCA", "FANCL", "RPA1"]

VARIANT_CLASSES = ["pathogenic", "likely_pathogenic", "VUS", "benign"]
VARIANT_TYPES = ["missense", "nonsense", "frameshift", "splice_site", "in_frame_del"]

PARP_DRUGS = ["olaparib", "rucaparib", "niraparib", "talazoparib"]
PLATINUM_DRUGS = ["carboplatin", "cisplatin", "oxaliplatin"]

RECIST_RESPONSES = ["CR", "PR", "SD", "PD"]  # complete, partial, stable, progressive

CANCER_TYPES = ["TCGA-OV", "TCGA-BRCA", "TCGA-PRAD", "TCGA-LUAD", "TCGA-PAAD"]
STAGES = ["I", "II", "IIA", "IIB", "III", "IIIA", "IIIB", "IIIC", "IV"]


# ── Table Creation + Seeding ──────────────────────────────────────────────

def seed_biotech_tables(db_path: str | None = None) -> dict:
    """Create and populate biotech tables in DuckDB. Returns summary."""
    path = db_path or getattr(cfg, "DUCKDB_PATH", "data/zetabridge.duckdb")
    import os
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    
    conn = duckdb.connect(path)
    summary = {}

    # ── 1. tcga_clinical ──────────────────────────────────────────────
    conn.execute("""CREATE TABLE IF NOT EXISTS tcga_clinical (
        patient_id VARCHAR PRIMARY KEY,
        cancer_type VARCHAR,
        age_at_diagnosis INTEGER,
        sex VARCHAR,
        stage VARCHAR,
        diagnosis_date DATE,
        vital_status VARCHAR,
        os_months DOUBLE,
        pfs_months DOUBLE,
        prior_therapy_lines INTEGER,
        ethnicity VARCHAR
    )""")
    
    patients = []
    for _ in range(200):
        pid = _rand_id()
        cancer = random.choice(CANCER_TYPES)
        age = random.randint(28, 82)
        sex = random.choice(["Female", "Male"]) if cancer != "TCGA-OV" else "Female"
        stage = random.choice(STAGES)
        dx_date = _rand_date(2015, 2022)
        vital = random.choice(["Alive", "Alive", "Alive", "Dead"])
        os_m = round(random.uniform(2.0, 84.0), 1)
        pfs_m = round(random.uniform(1.0, min(os_m, 48.0)), 1)
        prior_lines = random.randint(0, 5)
        eth = random.choice(["White", "Black", "Asian", "Hispanic", "Other"])
        patients.append((pid, cancer, age, sex, stage, dx_date, vital, os_m, pfs_m, prior_lines, eth))
    
    conn.executemany(
        "INSERT OR IGNORE INTO tcga_clinical VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        patients
    )
    summary["tcga_clinical"] = len(patients)

    # ── 2. genomic_variants ───────────────────────────────────────────
    conn.execute("""CREATE TABLE IF NOT EXISTS genomic_variants (
        variant_id VARCHAR PRIMARY KEY,
        patient_id VARCHAR,
        gene VARCHAR,
        variant_type VARCHAR,
        variant_class VARCHAR,
        hgvs_protein VARCHAR,
        vaf DOUBLE,
        chromosome VARCHAR,
        position INTEGER,
        ref_allele VARCHAR,
        alt_allele VARCHAR,
        cosmic_id VARCHAR
    )""")
    
    variants = []
    for pid, cancer, *_ in patients:
        n_variants = random.randint(0, 6)
        for _ in range(n_variants):
            vid = "var_" + uuid.uuid4().hex[:10]
            gene = random.choice(DDR_GENES)
            vtype = random.choice(VARIANT_TYPES)
            vclass = random.choices(VARIANT_CLASSES, weights=[15, 20, 50, 15])[0]
            hgvs = f"p.{random.choice('ACDEFGHIKLMNPQRSTVWY')}{random.randint(10,2000)}{random.choice('ACDEFGHIKLMNPQRSTVWY')}"
            vaf = round(random.uniform(0.01, 0.95), 3)
            chrom = random.choice(["chr1","chr2","chr3","chr7","chr11","chr13","chr17"])
            pos = random.randint(10000, 150000000)
            ref = random.choice("ACGT")
            alt = random.choice([b for b in "ACGT" if b != ref])
            cosmic = f"COSM{random.randint(100000, 999999)}" if random.random() > 0.3 else None
            variants.append((vid, pid, gene, vtype, vclass, hgvs, vaf, chrom, pos, ref, alt, cosmic))
    
    conn.executemany(
        "INSERT OR IGNORE INTO genomic_variants VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        variants
    )
    summary["genomic_variants"] = len(variants)

    # ── 3. hrd_scores ─────────────────────────────────────────────────
    conn.execute("""CREATE TABLE IF NOT EXISTS hrd_scores (
        patient_id VARCHAR PRIMARY KEY,
        hrd_score DOUBLE,
        loh_score DOUBLE,
        tai_score DOUBLE,
        lst_score DOUBLE,
        hrd_status VARCHAR,
        assay VARCHAR,
        sample_date DATE
    )""")
    
    hrd_rows = []
    for pid, *_ in patients:
        loh = round(random.uniform(0, 30), 1)
        tai = round(random.uniform(0, 30), 1)
        lst = round(random.uniform(0, 30), 1)
        hrd = round(loh + tai + lst, 1)
        status = "HRD-positive" if hrd >= 42 else "HRD-negative"
        assay = random.choice(["FoundationOne CDx", "myChoice CDx", "SOPHiA DDM"])
        sdate = _rand_date(2016, 2023)
        hrd_rows.append((pid, hrd, loh, tai, lst, status, assay, sdate))
    
    conn.executemany(
        "INSERT OR IGNORE INTO hrd_scores VALUES (?,?,?,?,?,?,?,?)",
        hrd_rows
    )
    summary["hrd_scores"] = len(hrd_rows)

    # ── 4. drug_responses ─────────────────────────────────────────────
    conn.execute("""CREATE TABLE IF NOT EXISTS drug_responses (
        response_id VARCHAR PRIMARY KEY,
        patient_id VARCHAR,
        drug_name VARCHAR,
        drug_class VARCHAR,
        therapy_line INTEGER,
        recist_response VARCHAR,
        best_response_pct DOUBLE,
        duration_months DOUBLE,
        start_date DATE,
        resistance_type VARCHAR,
        brca_reversion BOOLEAN
    )""")
    
    responses = []
    for pid, cancer, age, sex, stage, *_ in patients:
        n_treatments = random.randint(1, 4)
        for line in range(1, n_treatments + 1):
            rid = "resp_" + uuid.uuid4().hex[:10]
            if random.random() > 0.4:
                drug = random.choice(PARP_DRUGS)
                dclass = "PARP_inhibitor"
            else:
                drug = random.choice(PLATINUM_DRUGS)
                dclass = "platinum"
            recist = random.choices(RECIST_RESPONSES, weights=[10, 25, 30, 35])[0]
            best_pct = round(random.uniform(-100, 20) if recist in ["CR","PR"] else random.uniform(-20, 100), 1)
            dur = round(random.uniform(1.0, 36.0), 1)
            sdate = _rand_date(2016, 2023)
            resistance = random.choice(["primary", "acquired", None, None])
            reversion = random.random() > 0.85 if resistance == "acquired" else False
            responses.append((rid, pid, drug, dclass, line, recist, best_pct, dur, sdate, resistance, reversion))
    
    conn.executemany(
        "INSERT OR IGNORE INTO drug_responses VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        responses
    )
    summary["drug_responses"] = len(responses)

    # ── 5. synthetic_lethality ────────────────────────────────────────
    conn.execute("""CREATE TABLE IF NOT EXISTS synthetic_lethality (
        pair_id VARCHAR PRIMARY KEY,
        gene_a VARCHAR,
        gene_b VARCHAR,
        crispr_score DOUBLE,
        cell_line VARCHAR,
        cancer_type VARCHAR,
        p_value DOUBLE,
        source VARCHAR,
        validated BOOLEAN
    )""")
    
    sl_rows = []
    sl_targets = ["PARP1", "PARP2", "POLQ", "ATR", "CHK1", "WEE1", "DNPK", "RAD52"]
    cell_lines = ["UWB1.289", "MDA-MB-436", "HCC1937", "CAPAN1", "PEO1", "COV362"]
    for gene_a in DDR_GENES[:8]:
        for gene_b in sl_targets:
            if gene_a == gene_b:
                continue
            pair_id = f"sl_{gene_a}_{gene_b}"
            score = round(random.uniform(-3.0, 0.5), 3)
            cl = random.choice(cell_lines)
            ct = random.choice(["ovarian", "breast", "prostate", "pancreatic"])
            pval = round(random.uniform(0.0001, 0.1), 6)
            src = random.choice(["DepMap_23Q4", "Broad_CRISPR", "Sanger_SCORE"])
            validated = pval < 0.01 and score < -1.0
            sl_rows.append((pair_id, gene_a, gene_b, score, cl, ct, pval, src, validated))
    
    conn.executemany(
        "INSERT OR IGNORE INTO synthetic_lethality VALUES (?,?,?,?,?,?,?,?,?)",
        sl_rows
    )
    summary["synthetic_lethality"] = len(sl_rows)

    conn.close()
    log.info("Biotech seed complete: %s", summary)
    return summary
