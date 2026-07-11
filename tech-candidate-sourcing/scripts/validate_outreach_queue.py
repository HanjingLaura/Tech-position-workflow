#!/usr/bin/env python3
"""Validate an outreach queue CSV before approval or sending."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from candidate_schema import EMAIL_RE
from outreach_safety import has_unresolved_placeholder, has_unsafe_public_content
REQUIRED_HEADERS = [
    "status",
    "to",
    "candidate_name",
    "subject",
    "body",
    "channel",
    "confidence",
    "source",
    "matched_keywords",
    "risk_to_confirm",
]
RECOMMENDED_HEADERS = [
    "personalization_note",
    "review_note",
]
APPROVAL_METADATA_HEADERS = [
    "approved_at",
    "approved_by",
    "approval_note",
]
BLOCKED_EMAIL_FRAGMENTS = [
    "example",
    "localhost",
    "noreply",
    "your_email",
    "yourname",
    "username@",
    "googlegroups.com",
    "ingest.sentry.io",
]


def read_queue(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        rows = [{key: (value or "").strip() for key, value in row.items()} for row in reader]
    return headers, rows


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch(value or ""))


def blocked_email(value: str) -> bool:
    lowered = (value or "").lower()
    return any(fragment in lowered for fragment in BLOCKED_EMAIL_FRAGMENTS)


def load_suppression_lists(paths: list[str]) -> set[str]:
    suppressed: set[str] = set()
    for value in paths:
        path = Path(value)
        if not path.exists():
            raise FileNotFoundError(f"Suppression list does not exist: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for email in EMAIL_RE.findall(line):
                suppressed.add(email.lower())
    return suppressed


def validate(path: Path, approved_status: str, suppression: set[str] | None = None) -> dict[str, object]:
    suppression = suppression or set()
    headers, rows = read_queue(path)
    missing_headers = [header for header in REQUIRED_HEADERS if header not in headers]
    missing_recommended_headers = [header for header in RECOMMENDED_HEADERS if header not in headers]
    status_counts = Counter((row.get("status") or "blank").lower() for row in rows)
    confidence_counts = Counter((row.get("confidence") or "missing").lower() for row in rows)
    channel_counts = Counter((row.get("channel") or "missing").lower() for row in rows)
    empty_queue = len(rows) == 0

    invalid_email_rows = []
    blocked_email_rows = []
    duplicate_emails = []
    missing_subject_rows = []
    missing_body_rows = []
    missing_source_rows = []
    missing_risk_rows = []
    missing_personalization_rows = []
    missing_review_note_rows = []
    placeholder_rows = []
    unsafe_content_rows = []
    approved_rows = []
    approved_missing_metadata_rows = []
    suppressed_rows = []
    approved_suppressed_rows = []
    seen: set[str] = set()
    duplicate_seen: set[str] = set()

    for idx, row in enumerate(rows, start=1):
        email = row.get("to", "").lower()
        if not valid_email(row.get("to", "")):
            invalid_email_rows.append(idx)
        if blocked_email(row.get("to", "")):
            blocked_email_rows.append((idx, row.get("to", "")))
        if email and email in suppression:
            suppressed_rows.append((idx, row.get("to", ""), row.get("status", "")))
            if row.get("status", "").lower() == approved_status.lower():
                approved_suppressed_rows.append((idx, row.get("to", "")))
        if email:
            if email in seen and email not in duplicate_seen:
                duplicate_emails.append(email)
                duplicate_seen.add(email)
            seen.add(email)
        if not row.get("subject"):
            missing_subject_rows.append(idx)
        if not row.get("body"):
            missing_body_rows.append(idx)
        if has_unresolved_placeholder(row.get("subject", ""), row.get("body", "")):
            placeholder_rows.append(idx)
        if has_unsafe_public_content(row.get("subject", ""), row.get("body", "")):
            unsafe_content_rows.append(idx)
        if not row.get("source"):
            missing_source_rows.append(idx)
        if not row.get("risk_to_confirm"):
            missing_risk_rows.append(idx)
        if "personalization_note" in headers and not row.get("personalization_note"):
            missing_personalization_rows.append(idx)
        if "review_note" in headers and not row.get("review_note"):
            missing_review_note_rows.append(idx)
        if row.get("status", "").lower() == approved_status.lower():
            approved_rows.append(idx)
            missing_metadata = [field for field in APPROVAL_METADATA_HEADERS if not row.get(field)]
            if missing_metadata:
                approved_missing_metadata_rows.append((idx, row.get("to", ""), ",".join(missing_metadata)))

    status = "pass"
    if (
        missing_headers
        or empty_queue
        or invalid_email_rows
        or blocked_email_rows
        or duplicate_emails
        or missing_subject_rows
        or missing_body_rows
        or placeholder_rows
        or unsafe_content_rows
        or approved_suppressed_rows
        or approved_missing_metadata_rows
    ):
        status = "fail"
    elif (
        missing_source_rows
        or missing_risk_rows
        or missing_recommended_headers
        or missing_personalization_rows
        or missing_review_note_rows
        or suppressed_rows
    ):
        status = "needs_review"

    return {
        "status": status,
        "row_count": len(rows),
        "empty_queue": empty_queue,
        "missing_headers": missing_headers,
        "missing_recommended_headers": missing_recommended_headers,
        "status_counts": status_counts,
        "confidence_counts": confidence_counts,
        "channel_counts": channel_counts,
        "approved_rows": approved_rows,
        "approved_missing_metadata_rows": approved_missing_metadata_rows,
        "suppressed_rows": suppressed_rows,
        "approved_suppressed_rows": approved_suppressed_rows,
        "invalid_email_rows": invalid_email_rows,
        "blocked_email_rows": blocked_email_rows,
        "duplicate_emails": duplicate_emails,
        "missing_subject_rows": missing_subject_rows,
        "missing_body_rows": missing_body_rows,
        "missing_source_rows": missing_source_rows,
        "missing_risk_rows": missing_risk_rows,
        "missing_personalization_rows": missing_personalization_rows,
        "missing_review_note_rows": missing_review_note_rows,
        "placeholder_rows": placeholder_rows,
        "unsafe_content_rows": unsafe_content_rows,
    }


def render_counter(counter: Counter[str]) -> str:
    if not counter:
        return "none"
    return ", ".join(f"{key}: {value}" for key, value in sorted(counter.items()))


def list_or_none(values: list[object], limit: int = 20) -> str:
    if not values:
        return "none"
    shown = values[:limit]
    suffix = "" if len(values) <= limit else f", ... +{len(values) - limit} more"
    return ", ".join(str(value) for value in shown) + suffix


def render_report(path: Path, result: dict[str, object], approved_status: str) -> str:
    blocked = [f"row {idx}: {email}" for idx, email in result["blocked_email_rows"]]  # type: ignore[index]
    suppressed = [f"row {idx}: {email} ({status or 'blank'})" for idx, email, status in result["suppressed_rows"]]  # type: ignore[index]
    approved_suppressed = [f"row {idx}: {email}" for idx, email in result["approved_suppressed_rows"]]  # type: ignore[index]
    approved_missing_metadata = [
        f"row {idx}: {email} missing {fields}"
        for idx, email, fields in result["approved_missing_metadata_rows"]  # type: ignore[index]
    ]
    return "\n".join(
        [
            "# Outreach Queue Validation Report",
            "",
            f"- Queue: `{path}`",
            f"- Status: `{result['status']}`",
            f"- Rows: {result['row_count']}",
            f"- Approved status: `{approved_status}`",
            f"- Approved rows: {len(result['approved_rows'])}",  # type: ignore[arg-type]
            f"- Status distribution: {render_counter(result['status_counts'])}",  # type: ignore[arg-type]
            f"- Channel distribution: {render_counter(result['channel_counts'])}",  # type: ignore[arg-type]
            f"- Confidence distribution: {render_counter(result['confidence_counts'])}",  # type: ignore[arg-type]
            "",
            "## Blocking Checks",
            "",
            f"- Missing required headers: {list_or_none(result['missing_headers'])}",  # type: ignore[arg-type]
            f"- Empty queue: {'yes' if result['empty_queue'] else 'no'}",
            f"- Rows with invalid email: {list_or_none(result['invalid_email_rows'])}",  # type: ignore[arg-type]
            f"- Rows with blocked/service/placeholder emails: {list_or_none(blocked)}",
            f"- Approved rows on suppression list: {list_or_none(approved_suppressed)}",
            f"- Duplicate recipients: {list_or_none(result['duplicate_emails'])}",  # type: ignore[arg-type]
            f"- Rows missing subject: {list_or_none(result['missing_subject_rows'])}",  # type: ignore[arg-type]
            f"- Rows missing body: {list_or_none(result['missing_body_rows'])}",  # type: ignore[arg-type]
            f"- Rows containing unresolved template placeholders: {list_or_none(result['placeholder_rows'])}",  # type: ignore[arg-type]
            f"- Rows containing URL/instruction-like public-source content: {list_or_none(result['unsafe_content_rows'])}",  # type: ignore[arg-type]
            f"- Approved rows missing approval metadata: {list_or_none(approved_missing_metadata)}",
            "",
            "## Review Checks",
            "",
            f"- Missing recommended headers: {list_or_none(result['missing_recommended_headers'])}",  # type: ignore[arg-type]
            f"- Rows missing source: {list_or_none(result['missing_source_rows'])}",  # type: ignore[arg-type]
            f"- Rows missing risk_to_confirm: {list_or_none(result['missing_risk_rows'])}",  # type: ignore[arg-type]
            f"- Rows missing personalization_note: {list_or_none(result['missing_personalization_rows'])}",  # type: ignore[arg-type]
            f"- Rows missing review_note: {list_or_none(result['missing_review_note_rows'])}",  # type: ignore[arg-type]
            f"- Rows on suppression list: {list_or_none(suppressed)}",
            "",
            "## Interpretation",
            "",
            "- `pass`: queue is structurally safe for review/dry-run. Real send still requires explicit approval and SMTP config.",
            "- `needs_review`: queue is usable after manually filling source, risk, personalization, or review context.",
            "- `fail`: do not send; fix blocking checks first, including empty queues.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an outreach queue CSV.")
    parser.add_argument("--queue", required=True, help="Queue CSV generated by draft_outreach.py.")
    parser.add_argument("--out", required=True, help="Output Markdown validation report.")
    parser.add_argument("--approved-status", default="approved")
    parser.add_argument("--suppression-list", action="append", default=[], help="Optional no-contact list, one email per line. Repeatable.")
    args = parser.parse_args()

    queue_path = Path(args.queue)
    result = validate(queue_path, args.approved_status, load_suppression_lists(args.suppression_list))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_report(queue_path, result, args.approved_status), encoding="utf-8")
    print(f"Queue validation status: {result['status']}")
    print(f"Wrote queue validation report to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
