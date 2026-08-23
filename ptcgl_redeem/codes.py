from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

CODE_RE = re.compile(r"^[A-Z0-9]{12,16}$")
LEGACY_CODE_RE = re.compile(r"^[A-Z0-9]{3}-[A-Z0-9]{4}-[A-Z0-9]{3}-[A-Z0-9]{3}$")

TRUE_VALUES = {"TRUE", "YES", "1", "REDEEMED"}
SKIP_STATUSES = {"redeemed", "rejected", "success"}


def normalize_code(raw: str) -> str:
    return re.sub(r"\s+", "", (raw or "")).upper()


def is_code(raw: str) -> bool:
    value = normalize_code(raw)
    return bool(CODE_RE.fullmatch(value) or LEGACY_CODE_RE.fullmatch(value))


def is_truthy(value: str | None) -> bool:
    return str(value or "").strip().upper() in TRUE_VALUES


def mask_code(code: str) -> str:
    if len(code) <= 4:
        return "****"
    return f"…{code[-4:]}"


@dataclass
class CodeRow:
    code: str
    set_name: str
    batch: str
    date: str
    redeemed: bool
    status: str
    detail: str
    raw: dict[str, str]


def parse_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path} has no header row")
        fieldnames = list(reader.fieldnames)
        rows = [{key: (row.get(key) or "") for key in fieldnames} for row in reader]
    for extra in ("Status", "Detail"):
        if extra not in fieldnames:
            fieldnames.append(extra)
            for row in rows:
                row.setdefault(extra, "")
    return fieldnames, rows


def iter_codes(rows: list[dict[str, str]]) -> list[CodeRow]:
    found: list[CodeRow] = []
    for raw in rows:
        code = normalize_code(raw.get("Code", ""))
        if not is_code(code):
            continue
        found.append(
            CodeRow(
                code=code,
                set_name=(raw.get("Set") or "").strip(),
                batch=(raw.get("Batch") or "").strip(),
                date=(raw.get("Date") or "").strip(),
                redeemed=is_truthy(raw.get("Redeemed")),
                status=(raw.get("Status") or "").strip().lower(),
                detail=(raw.get("Detail") or "").strip(),
                raw=raw,
            )
        )
    return found


def pending_codes(
    rows: list[CodeRow],
    *,
    set_name: str | None = None,
    limit: int | None = None,
) -> list[CodeRow]:
    pending: list[CodeRow] = []
    for row in rows:
        if row.redeemed or row.status in SKIP_STATUSES:
            continue
        if set_name and row.set_name.casefold() != set_name.casefold():
            continue
        pending.append(row)
        if limit is not None and len(pending) >= limit:
            break
    return pending
