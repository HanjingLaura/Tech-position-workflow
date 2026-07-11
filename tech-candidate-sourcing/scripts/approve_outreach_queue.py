#!/usr/bin/env python3
"""Approve selected outreach queue rows by explicit email allowlist.

This script never sends email. It rewrites a queue CSV so only explicitly
allowed recipients move to status=approved.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from candidate_schema import EMAIL_RE


APPROVAL_FIELDS = ["approved_at", "approved_by", "approval_note"]


def read_queue(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = [{key: (value or "").strip() for key, value in row.items()} for row in reader]
    if "status" not in fieldnames:
        fieldnames.insert(0, "status")
    for field in APPROVAL_FIELDS:
        if field not in fieldnames:
            fieldnames.append(field)
    return fieldnames, rows


def emails_from_text(value: str) -> set[str]:
    return {email.lower() for email in EMAIL_RE.findall(value or "")}


def read_allowlist(files: list[Path], inline_emails: list[str]) -> set[str]:
    allowed: set[str] = set()
    for value in inline_emails:
        allowed |= emails_from_text(value)
    for path in files:
        allowed |= emails_from_text(path.read_text(encoding="utf-8-sig"))
    return allowed


def approve_rows(
    rows: list[dict[str, str]],
    allowed: set[str],
    reset_others: bool,
    approved_by: str,
    approval_note: str,
) -> tuple[int, int]:
    approved = 0
    unmatched = set(allowed)
    approved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for row in rows:
        email = (row.get("to") or "").lower()
        if email in allowed:
            row["status"] = "approved"
            row["approved_at"] = approved_at
            row["approved_by"] = approved_by
            row["approval_note"] = approval_note
            approved += 1
            unmatched.discard(email)
        elif reset_others and row.get("status", "").lower() == "approved":
            row["status"] = "needs_review"
            row["approved_at"] = ""
            row["approved_by"] = ""
            row["approval_note"] = ""
    return approved, len(unmatched)


def write_queue(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve selected outreach queue rows by email allowlist.")
    parser.add_argument("--queue", required=True, type=Path, help="Input review queue CSV.")
    parser.add_argument("--out", required=True, type=Path, help="Output approved queue CSV.")
    parser.add_argument("--email", action="append", default=[], help="Email to approve. Repeatable.")
    parser.add_argument("--allowlist", action="append", default=[], type=Path, help="Text/CSV file containing approved emails. Repeatable.")
    parser.add_argument("--reset-others", action="store_true", help="Reset rows not in allowlist from approved back to needs_review.")
    parser.add_argument("--approved-by", default=os.environ.get("USERNAME") or os.environ.get("USER") or "manual_reviewer")
    parser.add_argument("--approval-note", default="Manually approved for outreach after review.")
    args = parser.parse_args()

    allowed = read_allowlist(args.allowlist, args.email)
    if not allowed:
        raise ValueError("No approval emails found. Pass --email or --allowlist.")

    fieldnames, rows = read_queue(args.queue)
    approved, unmatched = approve_rows(rows, allowed, args.reset_others, args.approved_by, args.approval_note)
    write_queue(args.out, fieldnames, rows)
    print(f"Approved rows: {approved}")
    print(f"Allowlist emails not found in queue: {unmatched}")
    print(f"Wrote approved queue to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
