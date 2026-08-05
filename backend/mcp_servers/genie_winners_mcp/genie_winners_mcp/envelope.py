"""Shared envelope + poison path gate."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

from . import SCHEMA_VERSION

_POISON_RE = re.compile(
    r"(QUARANTINE|QUARANTINED|poison)",
    re.IGNORECASE,
)


def envelope(
    *,
    ok: bool,
    tool: str,
    n: Optional[int] = None,
    receipt_sha: Optional[str] = None,
    artifacts: Optional[list[str]] = None,
    warnings: Optional[list[str]] = None,
    error: Optional[str] = None,
    data: Any = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "tool": tool,
        "receipt_sha": receipt_sha,
        "n": n,
        "schema_version": SCHEMA_VERSION,
        "artifacts": artifacts or [],
        "warnings": warnings or [],
        "error": error,
        "data": data,
    }


def not_implemented(tool_id: str) -> dict[str, Any]:
    return envelope(
        ok=False,
        tool=tool_id,
        n=0,
        error=f"NOT_IMPLEMENTED: {tool_id}",
        warnings=["stub — implement per GENIE_WINNERS_MCP_BUILD.md"],
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_poison_path(path: str | Path) -> bool:
    return bool(_POISON_RE.search(str(path)))


def refuse_poison_path(path: str | Path) -> Optional[dict[str, Any]]:
    """Return error envelope if path looks like quarantine/poison; else None."""
    if is_poison_path(path):
        return envelope(
            ok=False,
            tool="genie.refuse_poison",
            n=0,
            error=f"POISON_PATH_REFUSED: {path}",
            warnings=["do not load QUARANTINE/poison into live ranking"],
            data={"path": str(path)},
        )
    return None


def dumps(obj: dict[str, Any]) -> str:
    return json.dumps(obj, indent=2, default=str)


def write_receipt(tool_id: str, body: dict[str, Any], receipts_dir: Optional[Path] = None) -> Path:
    """Write envelope JSON under package receipts/ (best-effort local audit).

    Agent must still append Brenus METRICS_LEDGER.md. Does not commit secrets.
    """
    import time

    root = receipts_dir or Path(__file__).resolve().parents[1] / "receipts"
    root.mkdir(parents=True, exist_ok=True)
    safe = tool_id.replace(".", "_")
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = root / f"{safe}.{ts}.envelope.json"
    out.write_text(json.dumps(body, indent=2, default=str) + "\n", encoding="utf-8")
    return out
