#!/usr/bin/env python3
"""
Pull Project Data Sphere SAS zip packages for CRC trials.

Auth:
  - Portal login uses PDS_PORTAL_USERNAME (email) + SAS_PASSWORD from env
  - Never hardcode passwords; load via ENV_FILE or exported env vars

Mapping notation trial (donationId→fileId[, fileId...]) comes from PDS
/api/accessdata/results/{donationId} fileGroups[].files[].id
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

BASE = "https://data.projectdatasphere.org"

# trial → list of (donation_id, file_id)
TRIAL_FILE_MAP: Dict[str, List[Tuple[int, int]]] = {
    "PRIME": [(264, 3427), (309, 4668)],
    "PACCE": [(262, 3425)],
    "PEAK": [(263, 3426)],
    "N0147": [(161, 1274), (161, 1275), (161, 1276)],
    "HORIZON_III": [(78, 384), (78, 404), (251, 2445)],
    "MOSAIC": [(128, 777)],
    "VELOUR": [(131, 773)],
    "PaniBSC": [(310, 4672)],
}


def load_env_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"ENV_FILE not found: {path}")
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        # do not overwrite already-exported vars
        os.environ.setdefault(key, val)


def portal_username() -> str:
    for key in ("PDS_PORTAL_USERNAME", "PDS_EMAIL", "SAS_PORTAL_USERNAME"):
        if os.getenv(key):
            return os.environ[key]
    # PDS web login is email; CAS service account (SAS_USERNAME=mpm*) is not accepted
    raise RuntimeError(
        "Set PDS_PORTAL_USERNAME (portal email). CAS SAS_USERNAME is not valid for web download."
    )


def login(session: requests.Session) -> Dict[str, Any]:
    user = portal_username()
    password = os.environ.get("SAS_PASSWORD", "")
    if not password:
        raise RuntimeError("SAS_PASSWORD missing from environment")
    r = session.post(
        f"{BASE}/api/auth/login",
        json={"username": user, "password": password},
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"Portal login failed ({r.status_code}): {r.text[:200]}")
    data = r.json()
    print(f"OK login as {data.get('username')} (short={data.get('shortUsername')}, group={data.get('pdsGroup')})")
    return data


def donation_meta(session: requests.Session, donation_id: int) -> Dict[str, Any]:
    r = session.get(f"{BASE}/api/accessdata/results/{donation_id}", timeout=60)
    r.raise_for_status()
    return r.json()


def index_files(meta: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for group in meta.get("fileGroups") or []:
        for key, val in group.items():
            candidates: List[Any] = []
            if isinstance(val, dict):
                candidates = [val]
            elif isinstance(val, list):
                candidates = val
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                fid = item.get("id")
                if isinstance(fid, int) and fid > 0:
                    out[fid] = item
                # nested zipFiles
                for z in item.get("zipFiles") or []:
                    if isinstance(z, dict) and isinstance(z.get("id"), int):
                        out[z["id"]] = z
    return out


def download_file(
    session: requests.Session,
    donation_id: int,
    file_id: int,
    dest_dir: Path,
    trial: str,
    expected_name: Optional[str] = None,
) -> Dict[str, Any]:
    url = f"{BASE}/api/file/download/{donation_id}/{file_id}"
    with session.get(url, timeout=600, stream=True) as r:
        status = r.status_code
        ctype = r.headers.get("content-type", "")
        disp = r.headers.get("content-disposition", "")
        if status != 200:
            return {
                "trial": trial,
                "donation_id": donation_id,
                "file_id": file_id,
                "status": "error",
                "http": status,
                "body": r.text[:300],
            }
        filename = expected_name or f"donation{donation_id}_file{file_id}.bin"
        if "filename=" in disp:
            filename = disp.split("filename=")[-1].strip().strip('"')
        # namespace collisions across trials
        out_path = dest_dir / trial / f"{donation_id}_{file_id}__{filename}"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".partial")
        n = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
                    n += len(chunk)
        tmp.replace(out_path)
        return {
            "trial": trial,
            "donation_id": donation_id,
            "file_id": file_id,
            "status": "ok",
            "http": status,
            "content_type": ctype,
            "bytes": n,
            "path": str(out_path),
            "filename": filename,
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--env-file",
        default=os.getenv("ENV_FILE", ""),
        help="Optional ENV_FILE with SAS_PASSWORD + PDS_PORTAL_USERNAME (prefer exported env)",
    )
    ap.add_argument(
        "--out-dir",
        default="backend/data/features/pds_crc",
        help="Output directory for downloaded packages",
    )
    ap.add_argument(
        "--trials",
        nargs="*",
        default=None,
        help="Subset of trial keys (default: all)",
    )
    args = ap.parse_args()

    if args.env_file:
        load_env_file(Path(args.env_file))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    trials = args.trials or list(TRIAL_FILE_MAP.keys())
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "CrisPRO-PDS-Puller/1.0",
            "Accept": "application/json, application/zip, */*",
            "Content-Type": "application/json",
        }
    )
    login(session)

    receipt: Dict[str, Any] = {
        "base": BASE,
        "out_dir": str(out_dir),
        "trials": {},
        "downloads": [],
    }

    for trial in trials:
        pairs = TRIAL_FILE_MAP.get(trial)
        if not pairs:
            print(f"SKIP unknown trial {trial}")
            continue
        print(f"\n=== {trial} ===")
        # cache donation meta
        metas: Dict[int, Dict[str, Any]] = {}
        file_index: Dict[int, Dict[int, Dict[str, Any]]] = {}
        for donation_id, file_id in pairs:
            if donation_id not in metas:
                meta = donation_meta(session, donation_id)
                metas[donation_id] = meta
                file_index[donation_id] = index_files(meta)
                print(
                    f"  donation {donation_id}: {meta.get('donationTitle', '')[:80]} "
                    f"| caslib={meta.get('uniqueDatasetId')} | files_indexed={len(file_index[donation_id])}"
                )
            info = file_index[donation_id].get(file_id)
            if info:
                print(
                    f"  target {file_id}: {info.get('filename')} "
                    f"type={info.get('fileType')} size={info.get('fileSize')} "
                    f"downloadable={info.get('downloadable')}"
                )
            else:
                print(f"  WARN file_id {file_id} not in donation {donation_id} index — still attempting download")

            result = download_file(
                session,
                donation_id,
                file_id,
                out_dir,
                trial,
                expected_name=(info or {}).get("filename"),
            )
            receipt["downloads"].append(result)
            status = result["status"]
            if status == "ok":
                print(f"  ✓ {result['filename']} ({result['bytes']} bytes) → {result['path']}")
            else:
                print(f"  ✗ {donation_id}/{file_id} http={result.get('http')} {result.get('body')}")
            time.sleep(0.3)

        receipt["trials"][trial] = {
            "donations": {
                str(d): {
                    "title": metas[d].get("donationTitle"),
                    "uniqueDatasetId": metas[d].get("uniqueDatasetId"),
                }
                for d in metas
            }
        }

    receipt_path = out_dir / "pull_receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2))
    ok = sum(1 for d in receipt["downloads"] if d["status"] == "ok")
    fail = sum(1 for d in receipt["downloads"] if d["status"] != "ok")
    print(f"\nDONE ok={ok} fail={fail} receipt={receipt_path}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
