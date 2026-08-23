from __future__ import annotations

import csv
import os
from pathlib import Path

from .codes import CodeRow, iter_codes, mask_code, normalize_code, parse_rows, pending_codes


class CsvStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fieldnames, self.rows = parse_rows(path)

    def codes(self) -> list[CodeRow]:
        return iter_codes(self.rows)

    def pending(self, *, set_name: str | None = None, limit: int | None = None) -> list[CodeRow]:
        return pending_codes(self.codes(), set_name=set_name, limit=limit)

    def mark(self, code: str, *, redeemed: bool, status: str, detail: str = "") -> None:
        target = normalize_code(code)
        updated = False
        # Update every matching row: the sheet may contain duplicates, and a
        # duplicate left unmarked would be retried on every future run.
        for row in self.rows:
            if normalize_code(row.get("Code", "")) != target:
                continue
            row["Redeemed"] = "TRUE" if redeemed else "FALSE"
            row["Status"] = status
            row["Detail"] = detail
            updated = True
        if not updated:
            raise KeyError(mask_code(code))
        self.save()

    def save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.rows)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(self.path)
