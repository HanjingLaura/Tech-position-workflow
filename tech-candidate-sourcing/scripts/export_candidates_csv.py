#!/usr/bin/env python3
"""Export a standard candidate Markdown table to CSV for spreadsheet review."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from md_table import parse_path, write_jsonl


def parse_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    headers, rows = parse_path(path)
    return headers, [row for row in rows if row.get("邮箱")]


def write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a candidate Markdown table to CSV.")
    parser.add_argument("--candidates", required=True, type=Path, help="Candidate Markdown table.")
    parser.add_argument("--out", required=True, type=Path, help="Output CSV path.")
    parser.add_argument("--jsonl-out", default=None, type=Path, help="Optional canonical machine-readable JSONL output.")
    args = parser.parse_args()

    headers, rows = parse_table(args.candidates)
    if not headers:
        raise ValueError(f"No Markdown candidate table found in {args.candidates}")
    write_csv(args.out, headers, rows)
    if args.jsonl_out:
        write_jsonl(args.jsonl_out, rows)
        print(f"Wrote {len(rows)} candidates to {args.jsonl_out}")
    print(f"Wrote {len(rows)} candidates to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
