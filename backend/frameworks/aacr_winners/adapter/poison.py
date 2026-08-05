"""Poison / quarantine path refusal (aligns with genie.refuse_poison concept)."""

from __future__ import annotations

from pathlib import Path

_POISON_TOKENS = ("QUARANTINE", "QUARANTINED", "poison", "POISON")


def is_poison_path(path: str | Path) -> bool:
    s = str(path)
    return any(tok in s for tok in _POISON_TOKENS)


def refuse_poison(path: str | Path) -> None:
    if is_poison_path(path):
        raise ValueError(
            f"POISON_REFUSED: path matches quarantine/poison token: {path}"
        )
